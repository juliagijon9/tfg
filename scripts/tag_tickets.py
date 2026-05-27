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
    SELECT DISTINCT
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
        ii.nivel_confianza_justificacion,
        ic.area,
        ic.justification AS clasificacion_justificacion
    FROM public.ado_work_items i
    LEFT JOIN public.ado_work_item_intentions ii ON ii.work_item_id = i.id
    LEFT JOIN public.ado_work_item_classifications ic ON ic.work_item_id = i.id
    LEFT JOIN public.ado_work_item_tag it ON it.work_item_id = i.id
    WHERE
        ii.work_item_id IS NOT NULL     -- Solo tickets que ya tienen intención extraída
        AND it.work_item_id IS NULL     -- Y que todavía no tienen tags asignados (incremental)
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
    text = html.unescape(text)                # Convierte entidades HTML (ej. &amp; → &)
    text = re.sub(r'<[^>]+>', ' ', text)      # Elimina etiquetas HTML
    text = re.sub(r'\s+', ' ', text).strip()  # Colapsa espacios múltiples en uno solo
    return text or "(sin datos)"


# ---------------------------
# Construcción del texto de entrada al LLM
# ---------------------------
def build_ticket_text(row: dict) -> str:
    # Empaqueta todos los campos del ticket en texto plano para mandárselo al LLM
    # Incluye intención, nivel de confianza y clasificación de los pasos anteriores como contexto
    return (
        f"Tipo: {row['work_item_type'] or '(sin datos)'}\n"
        f"Título: {row['title'] or '(sin datos)'}\n"
        f"Área asignada en Azure DevOps: {row['area_path'] or '(sin datos)'}\n"
        f"Iteración: {row['iteration_path'] or '(sin datos)'}\n"
        f"Etiquetas actuales en DevOps: {row['tags'] or '(sin datos)'}\n"
        f"Descripción: {clean_html(row['description'])}\n"
        f"Pasos para reproducir: {clean_html(row['repro_steps'])}\n"
        f"Criterios de aceptación: {clean_html(row['acceptance_criteria'])}\n"
        f"Intención extraída: {row['intention'] or '(sin datos)'}\n"
        f"Nivel de confianza de la intención: {row['nivel_confianza'] or '(sin datos)'}\n"
        f"Justificación del nivel de confianza: {row['nivel_confianza_justificacion'] or '(sin datos)'}\n"
        f"Área clasificada: {row['area'] or '(sin datos)'}\n"
        f"Justificación de la clasificación: {row['clasificacion_justificacion'] or '(sin datos)'}"
    )


# ---------------------------
# Llamada a Azure OpenAI
# ---------------------------
def get_tags(client, ticket_id: int, ticket_text: str, prompt: str) -> dict[str, str]:
    """Devuelve un diccionario {tag: justificacion_individual} con los tags asignados al ticket."""
    try:
        response = client.chat.completions.create(
            model=AZURE_DEPLOYMENT,
            messages=[
                {"role": "system", "content": prompt},     # Instrucciones al LLM: qué tags existen y cómo asignarlos
                {"role": "user", "content": ticket_text},  # Contenido del ticket a taggear
            ],
            temperature=0.0,                               # Temperatura 0 para máxima consistencia (sin aleatoriedad)
            max_tokens=400,                                # Más tokens que clasificación porque puede haber varios tags con justificaciones
            response_format={"type": "json_object"},       # Fuerza al LLM a devolver JSON válido siempre
        )
        data = json.loads(response.choices[0].message.content)
        tags = data.get("tags", [])
        if not isinstance(tags, list) or not tags:
            raise ValueError(f"Respuesta inválida: {data}")
        result = {}
        for item in tags:
            if isinstance(item, dict):
                tag = str(item.get("tag", "")).strip()
                just = str(item.get("justificacion", "")).strip()[:200]  # Limita la justificación de cada tag a 200 chars
            else:
                tag = str(item).strip()
                just = ""
            if tag:
                result[tag] = just  # Acumula cada tag con su justificación individual en el diccionario
        return result
    except Exception as e:
        print(f"❌ Error en ticket {ticket_id}: {e}")
        return {}


# ---------------------------
# Gestión de la tabla destino
# ---------------------------

def save_tags(conn, work_item_id: int, tags_dict: dict[str, str], model: str) -> None:
    """Guarda cada tag con su justificación individual en una fila separada de la tabla."""
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM public.ado_work_item_tag WHERE work_item_id = %s;",
            (work_item_id,)
        )  # Borra los tags anteriores del ticket para reemplazarlos por los nuevos
        for tag, justificacion in tags_dict.items():
            cur.execute(
                """
                INSERT INTO public.ado_work_item_tag
                    (work_item_id, tag, model, extracted_tag_at, justificacion)
                VALUES (%s, %s, %s, NOW(), %s);
                """,
                (work_item_id, tag, model, justificacion)  # Cada tag se guarda en una fila con su propia justificación
            )
    conn.commit()


# ---------------------------
# Procesamiento principal
# ---------------------------
def process_tickets(conn, client, prompt: str) -> None:
    with conn.cursor() as cur:
        cur.execute(QUERY)
        columns = [desc[0] for desc in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]  # Convierte cada fila en un diccionario para acceso por nombre

    print(f"📋 Tickets a taggear: {len(rows)}")

    for i, row in enumerate(rows, 1):
        ticket_id = row["id"]
        ticket_text = build_ticket_text(row)
        tags_dict = get_tags(client, ticket_id, ticket_text, prompt)  # Llama al LLM para obtener los tags con sus justificaciones

        if tags_dict:
            save_tags(conn, ticket_id, tags_dict, AZURE_DEPLOYMENT)
            resumen = ", ".join(f"{t}({j[:30]})" for t, j in tags_dict.items())
            print(f"  ⚙️  [{i}/{len(rows)}] Ticket {ticket_id}: {resumen}")
        else:
            print(f"  ❌ [{i}/{len(rows)}] Ticket {ticket_id}: ERROR (sin tags)")

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

    prompt = load_prompt(conn, "prompt_tag")  # Carga la versión más reciente del prompt desde BD
    process_tickets(conn, client, prompt)

    conn.close()
    print("✅ Tagging completado.")


if __name__ == "__main__":
    main()
