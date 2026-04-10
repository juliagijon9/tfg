import os
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


def run_generate_embeddings() -> dict:
    """Generate embeddings for all work items that don't have one yet."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, title, description
            FROM ado_work_items
            WHERE id NOT IN (
                SELECT work_item_id FROM ado_work_item_embeddings
            )
        """)
        rows = cur.fetchall()

        model_used = "azure-openai" if (
            os.getenv("AZURE_OPENAI_ENDPOINT") and os.getenv("AZURE_OPENAI_KEY")
        ) else "dummy"

        processed = 0
        for i, (ticket_id, title, description) in enumerate(rows, start=1):
            text = f"{title or ''}\n\n{description or ''}".strip()
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
