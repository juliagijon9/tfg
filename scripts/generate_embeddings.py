import os
import re
import time
import psycopg2
import numpy as np
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
# Embeddings configuration
# ---------------------------
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "dummy")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "128"))  # solo aplica a modo dummy

# Azure OpenAI
AZURE_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_DEPLOYMENT = os.getenv("AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT")
AZURE_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")

BATCH_SIZE = 16  # textos por llamada a la API de embeddings
MAX_CHARS = 8000  # ~2000 tokens por texto, seguro para batch de 16 (~32K tokens max vs 8192 por texto)


def _get_azure_client():
    """Inicializa el cliente de Azure OpenAI."""
    import openai
    return openai.AzureOpenAI(
        azure_endpoint=AZURE_ENDPOINT,
        api_key=AZURE_KEY,
        api_version=AZURE_API_VERSION,
    )


def _truncate(text: str) -> str:
    """Trunca texto largo para no exceder el límite de tokens de la API."""
    if len(text) <= MAX_CHARS:
        return text
    return text[:MAX_CHARS]


def _strip_html(text: str) -> str:
    """Elimina etiquetas HTML de un texto."""
    if not text:
        return ""
    return re.sub(r'<[^>]+>', '', text).strip()


def build_embedding_text(work_item_type, title, description, repro_steps=None, acceptance_criteria=None) -> str:
    """Construye el texto para generar el embedding según el tipo de work item.
    - Bug: title + repro_steps + acceptance_criteria (+ description si tiene contenido)
    - Resto: title + description
    """
    parts = [title or '']

    if work_item_type == 'Bug':
        rs = _strip_html(repro_steps)
        ac = _strip_html(acceptance_criteria)
        if rs:
            parts.append(rs)
        if ac:
            parts.append(ac)
        desc = _strip_html(description)
        if desc:
            parts.append(desc)
    else:
        desc = description or ''
        if desc:
            parts.append(desc)

    return '\n\n'.join(parts).strip()


def get_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """
    Genera embeddings para una lista de textos.
    - EMBEDDING_MODEL="dummy" → vectores aleatorios deterministas
    - Cualquier otro valor → Azure OpenAI (text-embedding-3-large)
    """
    if EMBEDDING_MODEL == "dummy":
        result = []
        for text in texts:
            np.random.seed(abs(hash(text)) % (2**32))
            result.append(np.random.rand(EMBEDDING_DIM).tolist())
        return result

    # Azure OpenAI - batch request
    client = _get_azure_client()
    truncated = [_truncate(t) for t in texts]
    response = client.embeddings.create(
        input=truncated,
        model=AZURE_DEPLOYMENT,
    )
    return [item.embedding for item in response.data]


def main():
    t_start = time.time()

    is_azure = EMBEDDING_MODEL != "dummy"
    model_label = AZURE_DEPLOYMENT if is_azure else "dummy"

    print(f"🔧 Modelo: {model_label}")
    if is_azure:
        print(f"   Endpoint: {AZURE_ENDPOINT}")
        print(f"   Deployment: {AZURE_DEPLOYMENT}")
        print(f"   Batch size: {BATCH_SIZE}")

    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASS
    )
    cur = conn.cursor()

    cur.execute("""
        SELECT distinct i.id, i.title, i.description, i.work_item_type, i.repro_steps, i.acceptance_criteria
        FROM ado_work_items i
        left join ado_work_item_embeddings ie on ie.work_item_id = i.id
        where ie.work_item_id is null
    """)
    rows = cur.fetchall()
    print(f"📋 Tickets pendientes de embedding: {len(rows)}")

    if not rows:
        print("✅ Nada que procesar")
        cur.close()
        conn.close()
        return

    # Preparar textos
    tickets = []
    for ticket_id, title, description, work_item_type, repro_steps, acceptance_criteria in rows:
        text = build_embedding_text(work_item_type, title, description, repro_steps, acceptance_criteria)
        if text:
            tickets.append((ticket_id, text))

    # Procesar en batches
    total = len(tickets)
    processed = 0
    errors = 0

    for i in range(0, total, BATCH_SIZE):
        batch = tickets[i:i + BATCH_SIZE]
        batch_ids = [t[0] for t in batch]
        batch_texts = [t[1] for t in batch]

        retries = 0
        max_retries = 3
        embeddings = None
        while retries <= max_retries:
            try:
                embeddings = get_embeddings_batch(batch_texts)
                break
            except Exception as e:
                if "429" in str(e) and retries < max_retries:
                    wait = 2 ** retries  # 1s, 2s, 4s
                    retries += 1
                    print(f"   ⚠️  Rate limit en batch {i//BATCH_SIZE + 1}, reintento {retries}/{max_retries} en {wait}s...")
                    time.sleep(wait)
                else:
                    errors += len(batch)
                    print(f"   ❌ Error en batch {i//BATCH_SIZE + 1}: {e}")
                    time.sleep(5)
                    break

        if embeddings is None:
            continue

        for ticket_id, embedding in zip(batch_ids, embeddings):
            cur.execute("""
                INSERT INTO ado_work_item_embeddings (work_item_id, embedding, model)
                VALUES (%s, %s, %s)
                ON CONFLICT (work_item_id) DO UPDATE SET
                    embedding = EXCLUDED.embedding,
                    model = EXCLUDED.model,
                    created_at = CURRENT_TIMESTAMP
            """, (ticket_id, embedding, model_label))

        processed += len(batch)
        conn.commit()

        if processed % 100 <= BATCH_SIZE:
            elapsed = time.time() - t_start
            print(f"   Procesados {processed}/{total} ({elapsed:.1f}s)")

        # Rate limiting para Azure OpenAI
        if is_azure and i + BATCH_SIZE < total:
            time.sleep(0.5)

    conn.commit()
    cur.close()
    conn.close()

    elapsed = time.time() - t_start
    print(f"\n{'='*50}")
    print(f"✅ Completado en {elapsed:.1f}s")
    print(f"   Procesados: {processed}")
    print(f"   Errores: {errors}")
    print(f"   Modelo: {model_label}")
    if processed > 0 and is_azure:
        dim = len(embeddings[0]) if embeddings else "?"
        print(f"   Dimensión: {dim}")


if __name__ == "__main__":
    main()
