import json
import os
import re
import html as html_module
import time

import psycopg2
from openai import AzureOpenAI
from dotenv import load_dotenv

load_dotenv()

# ---------------------------
# PostgreSQL configuration
# ---------------------------
PG_HOST = os.getenv("POSTGRES_HOST", "localhost")
PG_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
PG_DB = os.getenv("POSTGRES_DB")
PG_USER = os.getenv("POSTGRES_USER")
PG_PASS = os.getenv("POSTGRES_PASSWORD")

# ---------------------------
# Azure OpenAI configuration
# ---------------------------
AZURE_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")
AZURE_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "chat-tfg")

# ---------------------------
# Script configuration
# ---------------------------
DELAY_BETWEEN_CALLS = 0.3    # Segundos entre llamadas a la API (evita rate limit)

# ---------------------------
# Carga del prompt desde BD
# ---------------------------
def load_prompt(prompt_name: str) -> str:
    conn = get_db_connection()
    try:
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
    finally:
        conn.close()
    if not row:
        print(f"❌ No se encontró el prompt '{prompt_name}' en ado_config_prompt")
        import sys; sys.exit(1)
    return row[0]


# ---------------------------
# Helpers
# ---------------------------
def strip_html(text):
    """Elimina etiquetas HTML y decodifica entidades."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_module.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def get_db_connection():
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB,
        user=PG_USER, password=PG_PASS
    )


# ---------------------------
# 1. Obtener tickets de la BD
# ---------------------------
def fetch_tickets():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT
            i.id,
            i.work_item_type,
            i.title,
            i.area_path,
            i.iteration_path,
            i.tags,
            i.description,
            i.repro_steps,
            i.acceptance_criteria
        FROM public.ado_work_items i
        LEFT JOIN ado_work_item_intentions ii ON ii.work_item_id = i.id
        WHERE ii.work_item_id IS NULL
        ORDER BY i.id DESC
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    print(f"📋 Tickets a procesar: {len(rows)}")
    return rows


# ---------------------------
# 2. Llamar a Azure OpenAI
# ---------------------------
def extract_intention(work_item_type, title, area_path, iteration_path, tags, description, repro_steps, acceptance_criteria, prompt: str):
    client = AzureOpenAI(
        azure_endpoint=AZURE_ENDPOINT,
        api_key=AZURE_KEY,
        api_version=AZURE_API_VERSION,
    )

    user_payload = (
        f"Tipo: {work_item_type}\n"
        f"Título: {title}\n"
        f"Área: {area_path or '(sin área)'}\n"
        f"Iteración: {iteration_path or '(sin iteración)'}\n"
        f"Etiquetas: {tags or '(ninguna)'}\n"
        f"Descripción: {strip_html(description) or '(sin descripción)'}\n"
        f"Pasos para reproducir: {strip_html(repro_steps) or '(no aplican)'}\n"
        f"Criterios de aceptación: {strip_html(acceptance_criteria) or '(no aplican)'}"
    )

    response = client.chat.completions.create(
        model=AZURE_DEPLOYMENT,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_payload},
        ],
        temperature=0.1,
        max_tokens=300,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content or ""
    data = json.loads(raw)
    intention = data.get("intention", "")
    # Limitar a 600 caracteres y eliminar saltos de línea
    intention = re.sub(r"\n+", " ", intention).strip()[:600]
    return intention


# ---------------------------
# 3. Guardar en la tabla
# ---------------------------
def upsert_intention(work_item_id, intention, model):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO ado_work_item_intentions (work_item_id, intention, model)
        VALUES (%s, %s, %s)
        ON CONFLICT (work_item_id) DO UPDATE SET
            intention    = EXCLUDED.intention,
            model        = EXCLUDED.model,
            extracted_at = CURRENT_TIMESTAMP
    """, (work_item_id, intention, model))
    conn.commit()
    cur.close()
    conn.close()


# ---------------------------
# Main
# ---------------------------
def main():
    t_start = time.time()
    print(f"🔧 Modelo: {AZURE_DEPLOYMENT}")

    prompt = load_prompt("prompt_intention")

    tickets = fetch_tickets()
    if not tickets:
        print("✅ Nada que procesar.")
        return

    processed = 0
    errors = 0

    for row in tickets:
        work_item_id, work_item_type, title, area_path, iteration_path, tags, description, repro_steps, acceptance_criteria = row
        try:
            intention = extract_intention(
                work_item_type, title, area_path, iteration_path,
                tags, description, repro_steps, acceptance_criteria, prompt
            )
            upsert_intention(work_item_id, intention, AZURE_DEPLOYMENT)
            processed += 1
            print(f"  ✅ [{processed}/{len(tickets)}] #{work_item_id} — {title[:60]}")
        except Exception as e:
            errors += 1
            print(f"  ❌ #{work_item_id} — Error: {e}")

        time.sleep(DELAY_BETWEEN_CALLS)

    elapsed = time.time() - t_start
    print(f"\n{'='*50}")
    print(f"✅ Completado en {elapsed:.1f}s")
    print(f"   Procesados: {processed}")
    print(f"   Errores:    {errors}")
    print(f"   Modelo:     {AZURE_DEPLOYMENT}")


if __name__ == "__main__":
    main()
