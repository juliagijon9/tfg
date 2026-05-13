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
SOURCE_ID = os.getenv("SOURCE_ID")       # None → batch mode
TOP_K = int(os.getenv("TOP_K", "10"))
RELATED_THRESHOLD = float(os.getenv("RELATED_THRESHOLD", "0.80"))
DUPLICATE_THRESHOLD = float(os.getenv("DUPLICATE_THRESHOLD", "0.90"))
MAX_SOURCES = int(os.getenv("MAX_SOURCES", "1000"))
COMMIT_EVERY = int(os.getenv("COMMIT_EVERY", "100"))
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"

# target_id = 0 significa "procesado pero sin relación encontrada"
NO_RELATION_TARGET_ID = 0


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
    sims = [
        (wid, cosine_similarity(source_vec, vec))
        for wid, vec in emb_map.items()
        if wid != source_id
    ]
    sims.sort(key=lambda x: x[1], reverse=True)
    return sims[:TOP_K]


# ---------------------------
# BD: escritura de relaciones
# ---------------------------
def save_relation(cur, source_id: int, target_id: int, relation_type: str, score: float) -> None:
    cur.execute("""
        INSERT INTO ado_work_item_relations (source_id, target_id, relation_type, similarity)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (source_id, target_id) DO UPDATE SET
            relation_type = EXCLUDED.relation_type,
            similarity    = EXCLUDED.similarity,
            created_at    = CURRENT_TIMESTAMP
    """, (source_id, target_id, relation_type, score))


def save_no_relation_marker(cur, source_id: int) -> None:
    """Marca el ticket como procesado sin relación (target_id = 0)."""
    cur.execute("""
        INSERT INTO ado_work_item_relations (source_id, target_id, relation_type, similarity)
        VALUES (%s, %s, NULL, NULL)
        ON CONFLICT (source_id, target_id) DO NOTHING
    """, (source_id, NO_RELATION_TARGET_ID))


def clear_no_relation_marker(cur, ticket_id: int) -> None:
    """Elimina el marcador sin-relación de un ticket para que se reprocese."""
    cur.execute(
        "DELETE FROM ado_work_item_relations WHERE source_id = %s AND target_id = %s",
        (ticket_id, NO_RELATION_TARGET_ID),
    )


# ---------------------------
# Procesamiento de un ticket
# ---------------------------
def process_single(source_id: int, emb_map: dict[int, np.ndarray], cur) -> dict:
    """Calcula similares de source_id y guarda relaciones. Devuelve stats."""
    stats = {"duplicates": 0, "related": 0, "skipped": 0}

    top = find_top_k(source_id, emb_map)

    if not top:
        # Sin embedding propio → marcamos como procesado sin relación
        if not DRY_RUN:
            save_no_relation_marker(cur, source_id)
        return stats

    saved = 0
    for wid, score in top:
        rel = decide_relation(score)
        if rel is None:
            stats["skipped"] += 1
            continue

        if DRY_RUN:
            print(f"  [DRY-RUN] {source_id} → {wid}  {rel}  score={score:.4f}")
        else:
            save_relation(cur, source_id, wid, rel, score)
            # Si el ticket relacionado tenía marcador sin-relación, limpiarlo:
            # ahora tiene un vecino (source_id) que puede relacionarse con él
            # en la próxima ejecución.
            clear_no_relation_marker(cur, wid)

        if rel == "duplicate":
            stats["duplicates"] += 1
        else:
            stats["related"] += 1
        saved += 1

    if saved == 0:
        # Ningún vecino superó los umbrales → marcar sin relación
        if DRY_RUN:
            print(f"  [DRY-RUN] {source_id} → sin relación encontrada")
        else:
            save_no_relation_marker(cur, source_id)

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

    # Cargar todos los embeddings una sola vez
    emb_map = load_all_embeddings(cur)

    # Determinar los tickets a procesar
    if SOURCE_ID is not None:
        source_ids = [int(SOURCE_ID)]
        print(f"🔍 Modo single — SOURCE_ID={SOURCE_ID}")
    else:
        # Tickets con embedding que aún no tienen ninguna fila en relations
        # (ni relaciones reales ni marcador sin-relación).
        cur.execute("""
            SELECT i.id
            FROM ado_work_items i
            JOIN ado_work_item_embeddings ie ON ie.work_item_id = i.id
            LEFT JOIN ado_work_item_relations ir ON ir.source_id = i.id
            WHERE ir.source_id IS NULL
            ORDER BY i.changed_date DESC
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
    total = {"duplicates": 0, "related": 0, "skipped": 0}

    for i, sid in enumerate(source_ids, start=1):
        stats = process_single(sid, emb_map, cur)
        total["duplicates"] += stats["duplicates"]
        total["related"] += stats["related"]
        total["skipped"] += stats["skipped"]

        if i % COMMIT_EVERY == 0:
            if not DRY_RUN:
                conn.commit()
            print(f"  Progreso: {i}/{len(source_ids)} tickets procesados...")

    if not DRY_RUN:
        conn.commit()

    cur.close()
    conn.close()

    elapsed = time.time() - t_start
    print(f"\n{'='*60}")
    print(f"✅ Completado en {elapsed:.2f}s")
    print(f"   Tickets procesados: {len(source_ids)}")
    print(f"   Relaciones duplicate: {total['duplicates']}")
    print(f"   Relaciones related:   {total['related']}")
    print(f"   Pares descartados:    {total['skipped']}")
    print(f"   Umbrales: related>={RELATED_THRESHOLD} / duplicate>={DUPLICATE_THRESHOLD}")
    print(f"   TOP_K={TOP_K}  DRY_RUN={DRY_RUN}")


if __name__ == "__main__":
    main()
