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
CUTOFF_DATE = "2026-04-30"   # Procesa tickets creados después de esta fecha
DELAY_BETWEEN_CALLS = 0.3    # Segundos entre llamadas a la API (evita rate limit)

# ---------------------------
# Prompt de extracción
# ---------------------------
INTENT_EXTRACTION_PROMPT = """
Eres un agente experto en interpretar y clarificar tickets de
Azure DevOps de Iberia Express. Tu única tarea es extraer la
INTENCIÓN real que reside detrás del contenido de un ticket,
redactada en una sentencia clara, técnica y concreta que pueda
ser utilizada por otro sistema de IA en una etapa posterior
(clasificación, detección de duplicados o asignación).

Reglas estrictas:
- NO resumas el ticket. CLARIFICA su intención.
- La intención puede ser MÁS LARGA que el texto original si eso
  ayuda a desambiguar. La extensión típica óptima estará entre
  150 y 400 caracteres. NUNCA superes los 600 caracteres.
- Traduce lenguaje sintomático a lenguaje del problema real.
- Elimina saludos, despedidas, agradecimientos, firmas, contexto
  irrelevante, anécdotas y cualquier información que no aporte
  al problema.
- Corrige implícitamente faltas de ortografía y ambigüedades
  léxicas si entorpecen la interpretación.
- Mantén la información técnica relevante: nombres de sistemas,
  módulos, endpoints, identificadores, mensajes de error,
  pantallas o flujos concretos mencionados.
- Si el ticket es ambiguo y no se puede inferir la intención
  con razonable certeza, devuelve la intención más conservadora
  posible y márcalo añadiendo al final el texto "[INTENCIÓN INCIERTA]".
- NO inventes datos. NO añadas conclusiones, hipótesis ni
  recomendaciones de solución. SOLO la intención.
- Responde SIEMPRE en español, independientemente del idioma
  del ticket de entrada.

Contexto operativo del entorno (Iberia Express, plataformas digitales):
- Existen áreas funcionales como ecommerce (compra web, checkout,
  pagos), aplicación móvil VISEO, sistemas aeronáuticos (vuelos,
  flota, horarios), business intelligence (informes, dashboards),
  backend financiero (facturación, BFM), marketing digital y QA/validación.
- Los tickets pueden ser bugs, peticiones de desarrollo (deliveries),
  tareas de QA u otros tipos.

Formato de salida obligatorio (JSON estricto):
{
  "intention": "<sentencia clara, técnica, en español, ≤ 600 caracteres, sin saltos de línea>"
}
"""


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
def fetch_tickets(cutoff_date):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, work_item_type, title, description, repro_steps, acceptance_criteria, tags
        FROM public.ado_work_items
        WHERE created_date > %s
        ORDER BY created_date DESC
    """, (cutoff_date,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    print(f"📋 Tickets a procesar: {len(rows)}")
    return rows


# ---------------------------
# 2. Llamar a Azure OpenAI
# ---------------------------
def extract_intention(work_item_type, title, description, repro_steps, acceptance_criteria, tags):
    client = AzureOpenAI(
        azure_endpoint=AZURE_ENDPOINT,
        api_key=AZURE_KEY,
        api_version=AZURE_API_VERSION,
    )

    user_payload = (
        f"Tipo: {work_item_type}\n"
        f"Título: {title}\n"
        f"Etiquetas: {tags or '(ninguna)'}\n"
        f"Descripción: {strip_html(description) or '(sin descripción)'}\n"
        f"Pasos para reproducir: {strip_html(repro_steps) or '(no aplican)'}\n"
        f"Criterios de aceptación: {strip_html(acceptance_criteria) or '(no aplican)'}"
    )

    response = client.chat.completions.create(
        model=AZURE_DEPLOYMENT,
        messages=[
            {"role": "system", "content": INTENT_EXTRACTION_PROMPT},
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
    print(f"📅 Fecha de corte: {CUTOFF_DATE}")

    tickets = fetch_tickets(CUTOFF_DATE)
    if not tickets:
        print("✅ Nada que procesar.")
        return

    processed = 0
    errors = 0

    for row in tickets:
        work_item_id, work_item_type, title, description, repro_steps, acceptance_criteria, tags = row
        try:
            intention = extract_intention(
                work_item_type, title, description,
                repro_steps, acceptance_criteria, tags
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
