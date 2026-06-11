import json
import os
import re
import html as html_module
import time

import psycopg2
from openai import AzureOpenAI
from dotenv import load_dotenv

load_dotenv()  # Carga las variables del fichero .env

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
AZURE_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "chat-tfg")  # Deployment del modelo de chat (no embeddings)

# ---------------------------
# Script configuration
# ---------------------------
DELAY_BETWEEN_CALLS = 0.3  # Segundos de pausa entre llamadas a la API para no superar el rate limit

# ---------------------------
# Carga del prompt desde BD
# ---------------------------
def load_prompt(prompt_name: str) -> tuple[str, str]:
    """Devuelve (prompt_text, deployment) de la versión más reciente.
    Si el prompt no tiene modelo asociado, usa el deployment del .env como fallback."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT p.prompt_text, m.deployment
                FROM public.ado_config_prompt p
                LEFT JOIN public.ado_config_models m ON m.id = p.model_id
                WHERE p.prompt_name = %s
                  AND p.version = (
                      SELECT MAX(version) FROM public.ado_config_prompt
                      WHERE prompt_name = %s
                  );
            """, (prompt_name, prompt_name))  # Carga siempre la versión más reciente del prompt
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        print(f"❌ No se encontró el prompt '{prompt_name}' en ado_config_prompt")
        import sys; sys.exit(1)
    prompt_text, deployment = row
    return prompt_text, deployment or AZURE_DEPLOYMENT  # Fallback al .env si no hay modelo asociado


# ---------------------------
# Helpers
# ---------------------------
def strip_html(text):
    """Elimina etiquetas HTML y decodifica entidades para que el LLM reciba texto plano."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)     # Elimina etiquetas HTML (ej. <div>, <p>, <br>)
    text = html_module.unescape(text)          # Convierte entidades HTML (ej. &amp; → &, &lt; → <)
    return re.sub(r"\s+", " ", text).strip()  # Colapsa espacios múltiples en uno solo


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
        WHERE ii.work_item_id IS NULL          -- Solo tickets sin intención extraída todavía (incremental)
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
def extract_intention(work_item_type, title, area_path, iteration_path, tags, description, repro_steps, acceptance_criteria, prompt: str, deployment: str):
    client = AzureOpenAI(
        azure_endpoint=AZURE_ENDPOINT,
        api_key=AZURE_KEY,
        api_version=AZURE_API_VERSION,
    )

    # Construye el texto que se manda al LLM con todos los campos del ticket en texto plano
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
        model=deployment,  # Deployment configurado en el prompt activo (o fallback al .env)
        messages=[
            {"role": "system", "content": prompt},    # El prompt del sistema define las instrucciones al LLM
            {"role": "user", "content": user_payload}, # El contenido del ticket es el mensaje del usuario
        ],
        temperature=0.1,                               # Temperatura baja para respuestas más consistentes y deterministas
        max_completion_tokens=300,                     # Límite de tokens en la respuesta para controlar coste
        response_format={"type": "json_object"},       # Fuerza al LLM a devolver JSON válido siempre
    )

    try:
        raw = response.choices[0].message.content or ""
        data = json.loads(raw)                                                                            # Parsea el JSON de la respuesta
        intention = re.sub(r"\n+", " ", str(data.get("intention", "")).strip()).strip()[:600]             # Limpia saltos de línea y limita a 600 chars
        nivel_confianza = int(data.get("nivel_confianza", 1))
        if nivel_confianza not in (1, 2, 3, 4):
            nivel_confianza = 1                                                                           # Si el LLM devuelve un valor fuera de rango, se fuerza a 1
        nivel_confianza_justificacion = str(data.get("nivel_confianza_justificacion", "")).strip()[:200]  # Limita la justificación a 200 chars
    except Exception:
        intention = "[ERROR]"          # Si el JSON es inválido o falta algún campo, se guarda como error
        nivel_confianza = 1
        nivel_confianza_justificacion = ""
    return intention, nivel_confianza, nivel_confianza_justificacion


# ---------------------------
# 3. Guardar en la tabla
# ---------------------------
def upsert_intention(work_item_id, intention, model, nivel_confianza, nivel_confianza_justificacion):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO ado_work_item_intentions
            (work_item_id, intention, model, extracted_at, nivel_confianza, nivel_confianza_justificacion)
        VALUES (%s, %s, %s, CURRENT_TIMESTAMP, %s, %s)
        ON CONFLICT (work_item_id) DO UPDATE SET    -- Si ya existe (ej. tras Recalcular IA), actualiza todos los campos
            intention                     = EXCLUDED.intention,
            model                         = EXCLUDED.model,
            extracted_at                  = CURRENT_TIMESTAMP,
            nivel_confianza               = EXCLUDED.nivel_confianza,
            nivel_confianza_justificacion = EXCLUDED.nivel_confianza_justificacion
    """, (work_item_id, intention, model, nivel_confianza, nivel_confianza_justificacion))
    conn.commit()
    cur.close()
    conn.close()


# ---------------------------
# Main
# ---------------------------
def main():
    t_start = time.time()

    prompt, deployment = load_prompt("prompt_intention")  # Carga prompt y deployment de la versión más reciente
    print(f"🔧 Modelo: {deployment}")

    tickets = fetch_tickets()                 # Obtiene los tickets pendientes de procesar
    if not tickets:
        print("✅ Nada que procesar.")
        return

    processed = 0
    errors = 0

    for row in tickets:
        work_item_id, work_item_type, title, area_path, iteration_path, tags, description, repro_steps, acceptance_criteria = row
        try:
            intention, nivel_confianza, nivel_confianza_justificacion = extract_intention(
                work_item_type, title, area_path, iteration_path,
                tags, description, repro_steps, acceptance_criteria, prompt, deployment
            )
            upsert_intention(work_item_id, intention, deployment, nivel_confianza, nivel_confianza_justificacion)
            processed += 1
            print(f"  ✅ [{processed}/{len(tickets)}] #{work_item_id} — {title[:60]} (confianza: {nivel_confianza} — {nivel_confianza_justificacion[:60]})")
        except Exception as e:
            errors += 1
            print(f"  ❌ #{work_item_id} — Error: {e}")

        time.sleep(DELAY_BETWEEN_CALLS)  # Pausa entre tickets para no superar el rate limit de la API

    elapsed = time.time() - t_start
    print(f"\n{'='*50}")
    print(f"✅ Completado en {elapsed:.1f}s")
    print(f"   Procesados: {processed}")
    print(f"   Errores:    {errors}")
    print(f"   Modelo:     {deployment}")


if __name__ == "__main__":
    main()
