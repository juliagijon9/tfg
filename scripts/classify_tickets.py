import html
import json
import os
import re
import sys
import time
from pathlib import Path

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
# Prompt de clasificación
# ---------------------------
SYSTEM_PROMPT = (
    Path(__file__).parent / "prompts" / "classification_prompt.txt"
).read_text(encoding="utf-8")

# ---------------------------
# Consulta de entrada
# ---------------------------
QUERY = """
    SELECT
        i.id,
        i.work_item_type,
        i.title,
        i.area_path,
        i.iteration_path,
        i.tags,
        i.description,
        i.repro_steps,
        i.acceptance_criteria,
        ii.intention
    FROM public.ado_work_items i
    LEFT JOIN public.ado_work_item_intentions ii
        ON ii.work_item_id = i.id
    WHERE
        i.created_date > '2026-04-30'
        AND ii.work_item_id IS NOT NULL
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
        f"Intencionalidad extraída: {row['intention'] or '(sin datos)'}"
    )


# ---------------------------
# Llamada a Azure OpenAI
# ---------------------------
def classify_ticket(client, ticket_id: int, ticket_text: str) -> tuple[str, str]:
    try:
        response = client.chat.completions.create(
            model=AZURE_DEPLOYMENT,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
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
def ensure_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.ado_work_item_classifications (
                work_item_id   BIGINT    NOT NULL,
                area           TEXT      NOT NULL,
                justification  TEXT      NOT NULL,
                model          TEXT      NOT NULL,
                classified_at  TIMESTAMP NOT NULL DEFAULT NOW(),
                PRIMARY KEY (work_item_id)
            );
        """)
    conn.commit()


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


# ---------------------------
# Procesamiento principal
# ---------------------------
def process_tickets(conn, client) -> None:
    with conn.cursor() as cur:
        cur.execute(QUERY)
        columns = [desc[0] for desc in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]

    print(f"📋 Tickets a clasificar: {len(rows)}")

    for i, row in enumerate(rows, 1):
        ticket_id = row["id"]
        ticket_text = build_ticket_text(row)
        area, justification = classify_ticket(client, ticket_id, ticket_text)
        save_classification(conn, ticket_id, area, justification, AZURE_DEPLOYMENT)
        print(f"  ⚙️  [{i}/{len(rows)}] Ticket {ticket_id}: {area}")
        time.sleep(0.3)


# ---------------------------
# Main
# ---------------------------
def main() -> None:
    validate_env()

    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB,
        user=PG_USER, password=PG_PASS
    )
    ensure_table(conn)

    client = AzureOpenAI(
        azure_endpoint=AZURE_ENDPOINT,
        api_key=AZURE_KEY,
        api_version=AZURE_API_VERSION,
    )

    process_tickets(conn, client)

    conn.close()
    print("✅ Clasificación completada.")


if __name__ == "__main__":
    main()
