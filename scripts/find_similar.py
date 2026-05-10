import os
import numpy as np
import psycopg2
from dotenv import load_dotenv

load_dotenv()

PG_HOST = os.getenv("POSTGRES_HOST", "localhost")
PG_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
PG_DB = os.getenv("POSTGRES_DB")
PG_USER = os.getenv("POSTGRES_USER")
PG_PASS = os.getenv("POSTGRES_PASSWORD")

TOP_K = int(os.getenv("TOP_K", "10"))
SOURCE_ID = int(os.getenv("SOURCE_ID", "174132"))  # cambia si quieres

def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = (np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)

def main():
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASS
    )
    cur = conn.cursor()

    # embedding origen
    cur.execute("""
        SELECT distinct embedding
        FROM ado_work_item_embeddings
        WHERE work_item_id = %s
    """, (SOURCE_ID,))
    row = cur.fetchone()
    if not row:
        print(f"❌ No existe embedding para work_item_id={SOURCE_ID}")
        return

    source_vec = np.array(row[0], dtype=np.float64)

    # embeddings resto
    cur.execute("""
        SELECT work_item_id, embedding
        FROM ado_work_item_embeddings
        WHERE work_item_id <> %s
    """, (SOURCE_ID,))
    all_rows = cur.fetchall()

    sims = []
    for wid, emb in all_rows:
        v = np.array(emb, dtype=np.float64)
        sims.append((wid, cosine_similarity(source_vec, v)))

    sims.sort(key=lambda x: x[1], reverse=True)
    top = sims[:TOP_K]

    print(f"✅ Top {TOP_K} similares para {SOURCE_ID}:")
    for wid, score in top:
        print(f"  - {wid}: {score:.4f}")

    cur.close()
    conn.close()

if __name__ == "__main__":
    main()