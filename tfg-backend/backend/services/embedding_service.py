import os
import re
import numpy as np

from backend.db import get_connection

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "dummy")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "128"))


def get_embedding(text: str) -> list[float]:
    """Generate embedding for text. Uses Azure OpenAI if configured, else dummy."""
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    key = os.getenv("AZURE_OPENAI_API_KEY")
    deployment = os.getenv("AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT")
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")

    if endpoint and key and deployment:
        import openai

        client = openai.AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=key,
            api_version=api_version,
        )
        response = client.embeddings.create(input=text, model=deployment)
        return response.data[0].embedding

    # Fallback: deterministic dummy embedding
    np.random.seed(abs(hash(text)) % (2**32))
    return np.random.rand(EMBEDDING_DIM).tolist()


def _strip_html(text: str) -> str:
    """Elimina etiquetas HTML de un texto."""
    if not text:
        return ""
    return re.sub(r'<[^>]+>', '', text).strip()


def build_embedding_text(work_item_type, title, description, repro_steps=None, acceptance_criteria=None) -> str:
    """Construye el texto para generar el embedding según el tipo de work item."""
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


def run_generate_embeddings() -> dict:
    """Generate embeddings for all work items that don't have one yet."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, title, description, work_item_type, repro_steps, acceptance_criteria
            FROM ado_work_items
            WHERE id NOT IN (
                SELECT work_item_id FROM ado_work_item_embeddings
            )
        """)
        rows = cur.fetchall()

        model_used = "azure-openai" if (
            os.getenv("AZURE_OPENAI_ENDPOINT") and os.getenv("AZURE_OPENAI_API_KEY")
        ) else "dummy"

        processed = 0
        for i, (ticket_id, title, description, work_item_type, repro_steps, acceptance_criteria) in enumerate(rows, start=1):
            text = build_embedding_text(work_item_type, title, description, repro_steps, acceptance_criteria)
            if not text:
                continue

            embedding = get_embedding(text)

            cur.execute("""
                INSERT INTO ado_work_item_embeddings (work_item_id, embedding, model)
                VALUES (%s, %s, %s)
                ON CONFLICT (work_item_id) DO NOTHING
            """, (ticket_id, embedding, model_used))
            processed += 1

            if i % 100 == 0:
                conn.commit()

        conn.commit()
        cur.close()
        return {"total_pending": len(rows), "processed": processed, "model": model_used}
    finally:
        conn.close()
