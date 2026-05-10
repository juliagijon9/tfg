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
        ic.area,
        ic.justification
    FROM public.ado_work_items i
    LEFT JOIN public.ado_work_item_intentions ii
        ON ii.work_item_id = i.id
    LEFT JOIN public.ado_work_item_classifications ic
        ON ic.work_item_id = i.id
	LEFT JOIN public.ado_work_item_tag it
		on it.work_item_id = i.id
    WHERE
        i.created_date > '2026-04-30'
        AND ii.work_item_id IS NOT NULL
        AND ic.work_item_id IS NOT NULL
		AND it.work_item_id IS NULL
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
        f"Área actual en DevOps: {row['area_path'] or '(sin datos)'}\n"
        f"Etiquetas actuales en DevOps: {row['tags'] or '(sin datos)'}\n"
        f"Descripción: {clean_html(row['description'])}\n"
        f"Pasos para reproducir: {clean_html(row['repro_steps'])}\n"
        f"Criterios de aceptación: {clean_html(row['acceptance_criteria'])}\n"
        f"Intencionalidad extraída: {row['intention'] or '(sin datos)'}\n"
        f"Área asignada por el clasificador: {row['area'] or '(sin datos)'}\n"
        f"Justificación del clasificador: {row['justification'] or '(sin datos)'}"
    )


# ---------------------------
# Llamada a Azure OpenAI
# ---------------------------
def get_tags(client, ticket_id: int, ticket_text: str, prompt: str) -> list[str]:
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
        tags = data.get("tags", [])
        if not isinstance(tags, list) or not tags:
            raise ValueError(f"Respuesta inválida: {data}")
        return [str(t).strip() for t in tags if t]
    except Exception as e:
        print(f"❌ Error en ticket {ticket_id}: {e}")
        return []


# ---------------------------
# Gestión de la tabla destino
# ---------------------------
def ensure_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.ado_work_item_tag (
                work_item_id     BIGINT    NOT NULL,
                tag              TEXT      NOT NULL,
                model            TEXT      NOT NULL,
                extracted_tag_at TIMESTAMP NOT NULL DEFAULT NOW(),
                PRIMARY KEY (work_item_id, tag)
            );
        """)
    conn.commit()


def save_tags(conn, work_item_id: int, tags: list[str], model: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM public.ado_work_item_tag WHERE work_item_id = %s;",
            (work_item_id,)
        )
        for tag in tags:
            cur.execute(
                """
                INSERT INTO public.ado_work_item_tag
                    (work_item_id, tag, model, extracted_tag_at)
                VALUES (%s, %s, %s, NOW());
                """,
                (work_item_id, tag, model)
            )
    conn.commit()


# ---------------------------
# Procesamiento principal
# ---------------------------
def process_tickets(conn, client, prompt: str) -> None:
    with conn.cursor() as cur:
        cur.execute(QUERY)
        columns = [desc[0] for desc in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]

    print(f"📋 Tickets a taggear: {len(rows)}")

    for i, row in enumerate(rows, 1):
        ticket_id = row["id"]
        ticket_text = build_ticket_text(row)
        tags = get_tags(client, ticket_id, ticket_text, prompt)

        if tags:
            save_tags(conn, ticket_id, tags, AZURE_DEPLOYMENT)
            print(f"  ⚙️  [{i}/{len(rows)}] Ticket {ticket_id}: {', '.join(tags)}")
        else:
            print(f"  ❌ [{i}/{len(rows)}] Ticket {ticket_id}: ERROR (sin tags)")

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

    prompt = load_prompt(conn, "prompt_tag")
    process_tickets(conn, client, prompt)

    conn.close()
    print("✅ Tagging completado.")


if __name__ == "__main__":
    main()
