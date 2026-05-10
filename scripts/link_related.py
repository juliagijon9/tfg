import os
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
# Relation parameters
# ---------------------------
SOURCE_ID = os.getenv("SOURCE_ID")  # None → batch mode
TOP_K = int(os.getenv("TOP_K", "10"))
RELATED_THRESHOLD = float(os.getenv("RELATED_THRESHOLD", "0.80"))
DUPLICATE_THRESHOLD = float(os.getenv("DUPLICATE_THRESHOLD", "0.90"))
MAX_SOURCES = int(os.getenv("MAX_SOURCES", "100"))
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"


# ---------------------------
# Helpers
# ---------------------------
def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def decide_relation(score: float) -> str | None:
    if score >= DUPLICATE_THRESHOLD:
        return "duplicate"
    if score >= RELATED_THRESHOLD:
        return "related"
    return None


def load_all_embeddings(cur) -> dict[int, np.ndarray]:
    """Carga todos los embeddings en memoria una sola vez."""
    t0 = time.time()
    cur.execute("SELECT work_item_id, embedding FROM ado_work_item_embeddings")
    emb_map = {wid: np.array(emb, dtype=np.float64) for wid, emb in cur.fetchall()}
    print(f"📦 Cargados {len(emb_map)} embeddings en memoria ({time.time() - t0:.2f}s)")
    return emb_map


def find_top_k(source_id: int, emb_map: dict[int, np.ndarray]) -> list[tuple[int, float]]:
    """Calcula top-K tickets más similares a source_id."""
    source_vec = emb_map.get(source_id)
    if source_vec is None:
        return []

    sims = []
    for wid, vec in emb_map.items():
        if wid == source_id:
            continue
        sims.append((wid, cosine_similarity(source_vec, vec)))

    sims.sort(key=lambda x: x[1], reverse=True)
    return sims[:TOP_K]


def process_single(source_id: int, emb_map: dict[int, np.ndarray], cur) -> dict:
    """Procesa un ticket: calcula similares y guarda relaciones. Devuelve stats."""
    stats = {"duplicates": 0, "related": 0, "skipped": 0}

    top = find_top_k(source_id, emb_map)
    if not top:
        return stats

    for wid, score in top:
        rel = decide_relation(score)
        if rel is None:
            stats["skipped"] += 1
            continue

        if rel == "duplicate":
            stats["duplicates"] += 1
        else:
            stats["related"] += 1

        if DRY_RUN:
            print(f"  [DRY-RUN] {source_id} → {wid}  {rel}  score={score:.4f}")
            continue

        cur.execute("""
            INSERT INTO ado_work_item_relations (source_id, target_id, relation_type, similarity)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (source_id, target_id) DO UPDATE SET
                relation_type = EXCLUDED.relation_type,
                similarity    = EXCLUDED.similarity,
                created_at    = CURRENT_TIMESTAMP
        """, (source_id, wid, rel, score))

    return stats


# ---------------------------
# Main
# ---------------------------
def main():
    t_start = time.time()

    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASS
    )
    cur = conn.cursor()

    # Cargar todos los embeddings una vez
    emb_map = load_all_embeddings(cur)

    # Determinar modo: single o batch
    if SOURCE_ID is not None:
        source_ids = [int(SOURCE_ID)]
        print(f"🔍 Modo single — SOURCE_ID={SOURCE_ID}")
    else:
        cur.execute("""
            SELECT distinct w.id
            FROM ado_work_items w
            JOIN ado_work_item_embeddings e ON e.work_item_id = w.id
            WHERE w.id NOT IN (SELECT DISTINCT source_id FROM ado_work_item_relations)
            ORDER BY w.changed_date DESC
            LIMIT %s
        """, (MAX_SOURCES,))
        source_ids = [row[0] for row in cur.fetchall()]
        print(f"📋 Modo batch — {len(source_ids)} tickets pendientes (MAX_SOURCES={MAX_SOURCES})")

    if not source_ids:
        print("✅ No hay tickets pendientes de procesar")
        cur.close()
        conn.close()
        return

    if DRY_RUN:
        print("⚠️  DRY_RUN activado — no se insertará nada en BD")

    # Procesar
    total_stats = {"duplicates": 0, "related": 0, "skipped": 0}

    for i, sid in enumerate(source_ids, start=1):
        stats = process_single(sid, emb_map, cur)
        total_stats["duplicates"] += stats["duplicates"]
        total_stats["related"] += stats["related"]
        total_stats["skipped"] += stats["skipped"]

        if i % 50 == 0:
            if not DRY_RUN:
                conn.commit()
            print(f"  Progreso: {i}/{len(source_ids)} tickets procesados...")

    if not DRY_RUN:
        conn.commit()

    cur.close()
    conn.close()

    elapsed = time.time() - t_start
    print(f"\n{'='*50}")
    print(f"✅ Completado en {elapsed:.2f}s")
    print(f"   Tickets procesados: {len(source_ids)}")
    print(f"   Relaciones duplicate: {total_stats['duplicates']}")
    print(f"   Relaciones related:   {total_stats['related']}")
    print(f"   Pares descartados:    {total_stats['skipped']}")
    print(f"   Umbrales: related>={RELATED_THRESHOLD} / duplicate>={DUPLICATE_THRESHOLD}")
    print(f"   TOP_K={TOP_K}  DRY_RUN={DRY_RUN}")


if __name__ == "__main__":
    main()
