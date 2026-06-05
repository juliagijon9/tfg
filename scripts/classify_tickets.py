import html
import json
import os
import re
import sys
import time

import psycopg2
from dotenv import load_dotenv
from openai import AzureOpenAI

load_dotenv()  # Carga las variables del fichero .env

# ---------------------------
# Variables de entorno
# ---------------------------
PG_HOST = os.getenv("POSTGRES_HOST", "localhost")
PG_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
PG_DB = os.getenv("POSTGRES_DB")
PG_USER = os.getenv("POSTGRES_USER")
PG_PASS = os.getenv("POSTGRES_PASSWORD")

AZURE_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION")
AZURE_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT")

# ---------------------------
# Carga del prompt desde BD
# ---------------------------
def load_prompt(conn, prompt_name: str) -> str:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT prompt_text FROM public.ado_config_prompt
            WHERE prompt_name = %s
              AND version = (
                  SELECT MAX(version) FROM public.ado_config_prompt
                  WHERE prompt_name = %s
              );
        """, (prompt_name, prompt_name))  # Carga siempre la versión más reciente del prompt
        row = cur.fetchone()
    if not row:
        print(f"❌ No se encontró el prompt '{prompt_name}' en ado_config_prompt")
        sys.exit(1)
    return row[0]

# ---------------------------
# Consulta de entrada
# ---------------------------
QUERY = """
    SELECT
    distinct
        i.id,
        i.work_item_type,
        i.title,
        i.area_path,
        i.iteration_path,
        i.tags,
        i.description,
        i.repro_steps,
        i.acceptance_criteria,
        ii.intention,
        CASE
            WHEN ii.nivel_confianza = 1 THEN 'Crítico/Nulo'
            WHEN ii.nivel_confianza = 2 THEN 'Insuficiente'
            WHEN ii.nivel_confianza = 3 THEN 'Suficiente'
            WHEN ii.nivel_confianza = 4 THEN 'Excelente'
        END AS nivel_confianza,
        ii.nivel_confianza_justificacion
    FROM public.ado_work_items i
    LEFT JOIN public.ado_work_item_intentions ii ON ii.work_item_id = i.id
	LEFT JOIN public.ado_work_item_classifications ic on IC.work_item_id = i.id
    WHERE
        ii.work_item_id IS NOT NULL     -- Solo tickets que ya tienen intención extraída
		and IC.work_item_id is null     -- Y que todavía no tienen clasificación (incremental)
    ORDER BY i.id;
"""


# ---------------------------
# Validación de entorno
# ---------------------------
def validate_env() -> None:
    required = [
        "POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD",
        "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_API_VERSION", "AZURE_OPENAI_DEPLOYMENT",
    ]
    for var in required:
        if not os.getenv(var):
            print(f"❌ Falta la variable de entorno: {var}")
            sys.exit(1)


# ---------------------------
# Limpieza de HTML
# ---------------------------
def clean_html(text) -> str:
    if not text:
        return "(sin datos)"
    text = html.unescape(text)                    # Convierte entidades HTML (ej. &amp; → &)
    text = re.sub(r'<[^>]+>', ' ', text)          # Elimina etiquetas HTML
    text = re.sub(r'\s+', ' ', text).strip()      # Colapsa espacios múltiples en uno solo
    return text or "(sin datos)"


# ---------------------------
# Construcción del texto de entrada al LLM
# ---------------------------
def build_ticket_text(row: dict) -> str:
    # Empaqueta todos los campos del ticket en texto plano para mandárselo al LLM
    # Incluye la intención extraída y el nivel de confianza del paso anterior como contexto adicional
    return (
        f"Tipo: {row['work_item_type'] or '(sin datos)'}\n"
        f"Título: {row['title'] or '(sin datos)'}\n"
        f"Área actual: {row['area_path'] or '(sin datos)'}\n"
        f"Etiquetas: {row['tags'] or '(sin datos)'}\n"
        f"Descripción: {clean_html(row['description'])}\n"
        f"Pasos para reproducir: {clean_html(row['repro_steps'])}\n"
        f"Criterios de aceptación: {clean_html(row['acceptance_criteria'])}\n"
        f"Intencionalidad extraída: {row['intention'] or '(sin datos)'}\n"
        f"Nivel de confianza de la intención: {row['nivel_confianza'] or '(sin datos)'}\n"
        f"Justificación del nivel de confianza: {row['nivel_confianza_justificacion'] or '(sin datos)'}"
    )


# ---------------------------
# Llamada a Azure OpenAI
# ---------------------------
def classify_ticket(client, ticket_id: int, ticket_text: str, prompt: str) -> tuple[str, str]:
    try:
        response = client.chat.completions.create(
            model=AZURE_DEPLOYMENT,
            messages=[
                {"role": "system", "content": prompt},     # Instrucciones al LLM: cómo clasificar y qué áreas existen
                {"role": "user", "content": ticket_text},  # Contenido del ticket a clasificar
            ],
            temperature=0.0,                               # Temperatura 0 para máxima consistencia (sin aleatoriedad)
            max_tokens=200,                                # La respuesta es corta: solo área + justificación
            response_format={"type": "json_object"},       # Fuerza al LLM a devolver JSON válido siempre
        )
        data = json.loads(response.choices[0].message.content)
        area = data.get("area", "[SIN ÁREA]").strip()
        justification = data.get("justification", "[SIN JUSTIFICACIÓN]").strip()
        return area, justification
    except Exception as e:
        print(f"❌ Error en ticket {ticket_id}: {e}")
        return "[ERROR]", "[ERROR]"


# ---------------------------
# Gestión de la tabla destino
# ---------------------------

def save_classification(conn, work_item_id: int, area: str,
                        justification: str, model: str) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO public.ado_work_item_classifications
                (work_item_id, area, justification, model, classified_at)
            VALUES (%s, %s, %s, %s, NOW())
            ON CONFLICT (work_item_id) DO UPDATE SET    -- Si ya existe (ej. tras Recalcular IA), actualiza
                area          = EXCLUDED.area,
                justification = EXCLUDED.justification,
                model         = EXCLUDED.model,
                classified_at = EXCLUDED.classified_at;
        """, (work_item_id, area, justification, model))
    conn.commit()


def fetch_model_tickets(conn) -> str:
    """
    Obtiene los tickets modelo de ado_work_item_classifications_models,
    elegidos manualmente como ejemplos de clasificación correcta (pata negra).
    Se formatean como bloque de texto y se añaden al final del prompt.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT i.id, i.work_item_type, i.title, i.area_path, i.iteration_path, i.area, i.tags, i.description, i.repro_steps, i.acceptance_criteria
            FROM ado_work_item_classifications_models i
            ORDER BY i.area_path
        """)
        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description]

    if not rows:
        return ""

    lines = []
    for row in rows:
        r = dict(zip(columns, row))
        lines.append(
            f"ID: {r['id']} | Tipo: {r['work_item_type'] or '(sin datos)'} | Área ADO: {r['area_path'] or '(sin datos)'} | Área correcta: {r['area'] or '(sin datos)'}\n"
            f"Título: {r['title'] or '(sin datos)'}\n"
            f"Iteración: {r['iteration_path'] or '(sin datos)'}\n"
            f"Etiquetas: {r['tags'] or '(sin datos)'}\n"
            f"Descripción: {clean_html(r['description'])}\n"
            f"Pasos para reproducir: {clean_html(r['repro_steps'])}\n"
            f"Criterios de aceptación: {clean_html(r['acceptance_criteria'])}"
        )
        lines.append("")

    return "\n".join(lines)


# ---------------------------
# Procesamiento principal
# ---------------------------
def process_tickets(conn, client, base_prompt: str) -> None:
    with conn.cursor() as cur:
        cur.execute(QUERY)
        columns = [desc[0] for desc in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]  # Convierte cada fila en un diccionario para acceso por nombre

    print(f"📋 Tickets a clasificar: {len(rows)}")

    # Enriquece el prompt con tickets modelo elegidos manualmente (pata negra)
    model_tickets = fetch_model_tickets(conn)
    prompt = base_prompt + ("\n\n" + model_tickets if model_tickets else "")
    if model_tickets:
        print(f"📚 Tickets modelo cargados de ado_work_item_classifications_models")

    for i, row in enumerate(rows, 1):
        ticket_id = row["id"]
        ticket_text = build_ticket_text(row)
        area, justification = classify_ticket(client, ticket_id, ticket_text, prompt)  # El LLM propone el área basándose en el contenido y los tickets modelo
        save_classification(conn, ticket_id, area, justification, AZURE_DEPLOYMENT)
        print(f"  🤖 [{i}/{len(rows)}] #{ticket_id}: {area}")

        time.sleep(0.3)  # Pausa entre tickets para no superar el rate limit de la API


# ---------------------------
# Main
# ---------------------------
def main() -> None:
    validate_env()

    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB,
        user=PG_USER, password=PG_PASS
    )

    client = AzureOpenAI(
        azure_endpoint=AZURE_ENDPOINT,
        api_key=AZURE_KEY,
        api_version=AZURE_API_VERSION,
    )

    prompt = load_prompt(conn, "prompt_classification")  # Carga la versión más reciente del prompt desde BD
    process_tickets(conn, client, prompt)

    conn.close()
    print("✅ Clasificación completada.")


if __name__ == "__main__":
    main()
