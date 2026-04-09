import os
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
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "128"))


def get_embedding(text: str) -> list[float]:
    """
    Genera un embedding para el texto dado.

    - EMBEDDING_MODEL="dummy" → vector aleatorio determinista (para validar pipeline)
    - EMBEDDING_MODEL="text-embedding-ada-002" → TODO: llamar a Azure OpenAI
      Requiere: AZURE_OPENAI_ENDPOINT y AZURE_OPENAI_KEY en .env
      Ejemplo:
        import openai
        client = openai.AzureOpenAI(
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            api_key=os.getenv("AZURE_OPENAI_KEY"),
            api_version="2024-02-01"
        )
        response = client.embeddings.create(input=text, model=EMBEDDING_MODEL)
        return response.data[0].embedding
    """
    if EMBEDDING_MODEL != "dummy":
        raise NotImplementedError(
            f"Modelo '{EMBEDDING_MODEL}' no implementado aún. "
            "Usa EMBEDDING_MODEL=dummy o implementa la llamada a Azure OpenAI."
        )
    np.random.seed(abs(hash(text)) % (2**32))
    return np.random.rand(EMBEDDING_DIM).tolist()


def main():
    conn = psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DB,
        user=PG_USER,
        password=PG_PASS
    )
    cur = conn.cursor()

    # Seleccionar tickets sin embedding todavía
    cur.execute("""
        SELECT id, title, description
        FROM ado_work_items
        WHERE id NOT IN (
            SELECT work_item_id FROM ado_work_item_embeddings
        )
    """)
    rows = cur.fetchall()

    print(f"Tickets pendientes de embedding: {len(rows)}")

    for i, (ticket_id, title, description) in enumerate(rows, start=1):
        text = f"{title or ''}\n\n{description or ''}".strip()

        if not text:
            continue

        embedding = get_embedding(text)

        cur.execute("""
            INSERT INTO ado_work_item_embeddings
                (work_item_id, embedding, model)
            VALUES (%s, %s, %s)
            ON CONFLICT (work_item_id) DO NOTHING
        """, (ticket_id, embedding, EMBEDDING_MODEL))

        if i % 100 == 0:
            conn.commit()
            print(f"Procesados {i} tickets...")

    conn.commit()
    cur.close()
    conn.close()

    print("✅ Embeddings generados y guardados correctamente")


if __name__ == "__main__":
    main()
