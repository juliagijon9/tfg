import os
import time
import psycopg2
import numpy as np
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
# Relation parameters
# ---------------------------
SOURCE_ID = os.getenv("SOURCE_ID")                                    # Si se define, procesa solo ese ticket (modo individual)
TOP_K = int(os.getenv("TOP_K", "10"))                                 # Número máximo de vecinos más similares a considerar por ticket
RELATED_THRESHOLD = float(os.getenv("RELATED_THRESHOLD", "0.80"))     # Similitud mínima para considerar dos tickets como "relacionados"
DUPLICATE_THRESHOLD = float(os.getenv("DUPLICATE_THRESHOLD", "0.90")) # Similitud mínima para considerar dos tickets como "duplicados"
MAX_SOURCES = int(os.getenv("MAX_SOURCES", "1000"))                   # Máximo de tickets a procesar por ejecución en modo batch
COMMIT_EVERY = int(os.getenv("COMMIT_EVERY", "100"))                  # Frecuencia de commits para no perder todo si falla a mitad
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"                           # Si es True, calcula relaciones pero no guarda nada en BD

# target_id = 0 significa "ticket procesado pero sin ninguna relación encontrada"
NO_RELATION_TARGET_ID = 0


# ---------------------------
# Helpers
# ---------------------------
def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b)  # Producto de las normas (magnitudes) de ambos vectores
    if denom == 0:
        return 0.0                                   # Evita división por cero si algún vector es nulo
    return float(np.dot(a, b) / denom)              # Producto escalar dividido entre las magnitudes: da valor entre -1 y 1


def decide_relation(score: float) -> str | None:
    if score >= DUPLICATE_THRESHOLD:  # >= 0.90 → los tickets son prácticamente iguales
        return "duplicate"
    if score >= RELATED_THRESHOLD:    # >= 0.80 → los tickets tienen contenido similar pero no son iguales
        return "related"
    return None                       # < 0.80 → no hay relación relevante, se descarta


def load_all_embeddings(cur) -> dict[int, np.ndarray]:
    """Carga todos los embeddings en memoria una sola vez para evitar consultas repetidas a BD."""
    t0 = time.time()
    cur.execute("SELECT work_item_id, embedding FROM ado_work_item_embeddings")
    emb_map = {wid: np.array(emb, dtype=np.float64) for wid, emb in cur.fetchall()}  # Convierte cada embedding a array numpy
    print(f"📦 Cargados {len(emb_map)} embeddings en memoria ({time.time() - t0:.2f}s)")
    return emb_map


def find_top_k(source_id: int, emb_map: dict[int, np.ndarray]) -> list[tuple[int, float]]:
    """Calcula similitud coseno entre source_id y todos los demás tickets, devuelve los TOP_K más similares."""
    source_vec = emb_map.get(source_id)
    if source_vec is None:
        return []                                                    # El ticket no tiene embedding, no se puede comparar
    sims = [
        (wid, cosine_similarity(source_vec, vec))
        for wid, vec in emb_map.items()
        if wid != source_id                                          # Se excluye el ticket consigo mismo
    ]
    sims.sort(key=lambda x: x[1], reverse=True)                     # Ordena de mayor a menor similitud
    return sims[:TOP_K]                                              # Devuelve solo los TOP_K más similares


# ---------------------------
# BD: escritura de relaciones
# ---------------------------
def save_relation(cur, source_id: int, target_id: int, relation_type: str, score: float) -> None:
    cur.execute("""
        INSERT INTO ado_work_item_relations (source_id, target_id, relation_type, similarity)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (source_id, target_id) DO UPDATE SET    -- Si ya existe la relación, actualiza tipo y similitud
            relation_type = EXCLUDED.relation_type,
            similarity    = EXCLUDED.similarity,
            created_at    = CURRENT_TIMESTAMP
    """, (source_id, target_id, relation_type, score))
    cur.execute("""
        INSERT INTO ado_work_item_relations (source_id, target_id, relation_type, similarity)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (source_id, target_id) DO UPDATE SET    -- Inserta también la dirección inversa (la similitud es simétrica)
            relation_type = EXCLUDED.relation_type,
            similarity    = EXCLUDED.similarity,
            created_at    = CURRENT_TIMESTAMP
    """, (target_id, source_id, relation_type, score))


def save_no_relation_marker(cur, source_id: int) -> None:
    """Guarda target_id=0 para marcar que el ticket ya fue procesado aunque no tenga relaciones."""
    cur.execute("""
        INSERT INTO ado_work_item_relations (source_id, target_id, relation_type, similarity)
        VALUES (%s, %s, NULL, NULL)
        ON CONFLICT (source_id, target_id) DO NOTHING  -- Si el marcador ya existe, no hace nada
    """, (source_id, NO_RELATION_TARGET_ID))


def clear_no_relation_marker(cur, ticket_id: int) -> None:
    """Elimina el marcador sin-relación de un ticket porque ahora sí tiene al menos una relación."""
    cur.execute(
        "DELETE FROM ado_work_item_relations WHERE source_id = %s AND target_id = %s",
        (ticket_id, NO_RELATION_TARGET_ID),
    )


# ---------------------------
# Procesamiento de un ticket
# ---------------------------
def process_single(source_id: int, emb_map: dict[int, np.ndarray], cur) -> dict:
    """Calcula los tickets más similares a source_id y guarda las relaciones que superen los umbrales."""
    stats = {"duplicates": 0, "related": 0, "skipped": 0}

    top = find_top_k(source_id, emb_map)  # Obtiene los TOP_K tickets más similares

    if not top:
        # El ticket no tiene embedding propio → lo marcamos como procesado sin relación
        if not DRY_RUN:
            save_no_relation_marker(cur, source_id)
        return stats

    saved = 0
    for wid, score in top:
        rel = decide_relation(score)
        if rel is None:
            stats["skipped"] += 1  # La similitud no supera ningún umbral, se descarta este par
            continue

        if DRY_RUN:
            print(f"  [DRY-RUN] {source_id} → {wid}  {rel}  score={score:.4f}")
        else:
            save_relation(cur, source_id, wid, rel, score)          # Guarda la relación en ambas direcciones
            clear_no_relation_marker(cur, wid)                      # Si el ticket destino tenía marcador sin-relación, lo borra

        if rel == "duplicate":
            stats["duplicates"] += 1
        else:
            stats["related"] += 1
        saved += 1

    if saved == 0:
        # Ningún vecino superó los umbrales → marca el ticket como procesado sin relaciones
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

    emb_map = load_all_embeddings(cur)  # Carga todos los embeddings en memoria de una sola vez

    # Determina qué tickets procesar
    if SOURCE_ID is not None:
        source_ids = [int(SOURCE_ID)]  # Modo individual: procesa solo el ticket especificado
        print(f"🔍 Modo single — SOURCE_ID={SOURCE_ID}")
    else:
        # Modo batch: tickets con embedding que aún no tienen ninguna fila en relations
        # (ni relaciones reales ni marcador sin-relación con target_id=0)
        cur.execute("""
            SELECT i.id
            FROM ado_work_items i
            JOIN ado_work_item_embeddings ie ON ie.work_item_id = i.id
            LEFT JOIN ado_work_item_relations ir ON ir.source_id = i.id
            WHERE ir.source_id IS NULL          -- Solo tickets que no han sido procesados todavía
            ORDER BY i.changed_date DESC        -- Prioriza los modificados más recientemente
            LIMIT %s
        """, (MAX_SOURCES,))
        source_ids = [row[0] for row in cur.fetchall()]
        print(f"📋 Modo batch — {len(source_ids)} tickets pendientes (MAX_SOURCES={MAX_SOURCES})")
tfg-frontend
    if not source_ids:
        print("✅ No hay tickets pendientes de procesar")
        cur.close()
        conn.close()
        return

    if DRY_RUN:
        print("⚠️  DRY_RUN activado — no se insertará nada en BD")

    total = {"duplicates": 0, "related": 0, "skipped": 0}

    for i, sid in enumerate(source_ids, start=1):
        stats = process_single(sid, emb_map, cur)   # Procesa cada ticket: calcula similitudes y guarda relaciones
        total["duplicates"] += stats["duplicates"]
        total["related"] += stats["related"]
        total["skipped"] += stats["skipped"]

        if i % COMMIT_EVERY == 0:
            if not DRY_RUN:
                conn.commit()  # Commit periódico para no perder todo si falla a mitad del batch
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
