import html
import json
import os
import re
import sys
import time

import psycopg2
from dotenv import load_dotenv
from openai import AzureOpenAI

load_dotenv()

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
        """, (prompt_name, prompt_name))
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
        ii.work_item_id IS NOT NULL
		and IC.work_item_id is null
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
    text = html.unescape(text)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text or "(sin datos)"


# ---------------------------
# Construcción del texto de entrada al LLM
# ---------------------------
def build_ticket_text(row: dict) -> str:
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
                {"role": "system", "content": prompt},
                {"role": "user", "content": ticket_text},
            ],
            temperature=0.0,
            max_tokens=200,
            response_format={"type": "json_object"},
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
            ON CONFLICT (work_item_id) DO UPDATE SET
                area          = EXCLUDED.area,
                justification = EXCLUDED.justification,
                model         = EXCLUDED.model,
                classified_at = EXCLUDED.classified_at;
        """, (work_item_id, area, justification, model))
    conn.commit()


# Áreas válidas — el último segmento del area_path de ADO se compara aquí.
KNOWN_AREAS = {
    "I2 Ecommerce Team",
    "I2 Airplane Team",
    "I2 VISEO Team",
    "I2 VISEO App",
    "I2 MAD Team BI",
    "Team MKT I2",
    "Team QA",
    "Teams BFM",
}

EXAMPLES_PER_AREA = 2  # ejemplos reales por área para el few-shot


def resolve_area_from_path(area_path: str | None) -> str | None:
    """Devuelve el área conocida si el último segmento del area_path coincide."""
    if not area_path:
        return None
    segment = area_path.strip().split("\\")[-1].strip()
    return segment if segment in KNOWN_AREAS else None


def fetch_examples(conn) -> str:
    """
    Obtiene hasta EXAMPLES_PER_AREA tickets reales por área (con área asignada
    e intención extraída) y los formatea como bloque few-shot para el prompt.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (last_segment, i.id)
                split_part(i.area_path, '\\', -1)  AS last_segment,
                i.work_item_type,
                i.title,
                i.area_path,
                ii.intention
            FROM public.ado_work_items i
            JOIN public.ado_work_item_intentions ii ON ii.work_item_id = i.id
            WHERE i.area_path IS NOT NULL
              AND split_part(i.area_path, '\\', -1) = ANY(%s)
              AND ii.intention IS NOT NULL
            ORDER BY last_segment, i.id DESC
        """, (list(KNOWN_AREAS),))
        rows = cur.fetchall()

    # Agrupar por área y limitar a EXAMPLES_PER_AREA por cada una
    from collections import defaultdict
    by_area: dict[str, list] = defaultdict(list)
    for last_segment, wtype, title, area_path, intention in rows:
        if len(by_area[last_segment]) < EXAMPLES_PER_AREA:
            by_area[last_segment].append((wtype, title, area_path, intention))

    if not by_area:
        return ""

    lines = ["────────────────────────────────────────",
             "EJEMPLOS REALES DE ASIGNACIÓN CORRECTA",
             "────────────────────────────────────────"]
    for area, examples in sorted(by_area.items()):
        for wtype, title, area_path, intention in examples:
            lines.append(
                f"Tipo: {wtype} | Área actual: {area_path}\n"
                f"Título: {title}\n"
                f"Intención: {intention}\n"
                f'→ {{"area": "{area}", "justification": "Asignación correcta: la intención y el tipo de ticket son coherentes con {area}."}}'
            )
            lines.append("")
    lines.append("────────────────────────────────────────")
    return "\n".join(lines)


# ---------------------------
# Procesamiento principal
# ---------------------------
def process_tickets(conn, client, base_prompt: str) -> None:
    with conn.cursor() as cur:
        cur.execute(QUERY)
        columns = [desc[0] for desc in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]

    print(f"📋 Tickets a clasificar: {len(rows)}")

    # Construir prompt enriquecido con ejemplos reales (few-shot)
    few_shot = fetch_examples(conn)
    prompt = base_prompt + ("\n\n" + few_shot if few_shot else "")
    if few_shot:
        print(f"📚 Few-shot: ejemplos reales cargados de BD")

    confirmed = 0
    llm_classified = 0

    for i, row in enumerate(rows, 1):
        ticket_id = row["id"]
        area_known = resolve_area_from_path(row.get("area_path"))

        if area_known:
            # Área ya asignada: el LLM la confirma y justifica
            ticket_text = build_ticket_text(row)
            ticket_text += f"\n\nINSTRUCCIÓN: El área '{area_known}' ya está asignada. Confírmala y justifica brevemente por qué la intención extraída es coherente con este área."
            area, justification = classify_ticket(client, ticket_id, ticket_text, prompt)
            save_classification(conn, ticket_id, area_known, justification, AZURE_DEPLOYMENT)
            confirmed += 1
            print(f"  ✅ [{i}/{len(rows)}] #{ticket_id}: {area_known} (confirmado)")
        else:
            # Sin área reconocida: el LLM propone área y justificación
            ticket_text = build_ticket_text(row)
            area, justification = classify_ticket(client, ticket_id, ticket_text, prompt)
            save_classification(conn, ticket_id, area, justification, AZURE_DEPLOYMENT)
            llm_classified += 1
            print(f"  🤖 [{i}/{len(rows)}] #{ticket_id}: {area} (propuesto por LLM)")

        time.sleep(0.3)

    print(f"   Confirmados: {confirmed} | Propuestos por LLM: {llm_classified}")


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

    prompt = load_prompt(conn, "prompt_classification")
    process_tickets(conn, client, prompt)

    conn.close()
    print("✅ Clasificación completada.")


if __name__ == "__main__":
    main()
