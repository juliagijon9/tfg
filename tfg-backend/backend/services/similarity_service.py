import os
import numpy as np

from backend.db import get_connection

TOP_K = int(os.getenv("TOP_K", "10"))
RELATED_THRESHOLD = float(os.getenv("RELATED_THRESHOLD", "0.80"))
DUPLICATE_THRESHOLD = float(os.getenv("DUPLICATE_THRESHOLD", "0.90"))


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


def find_top_k(source_id: int, top_k: int | None = None) -> list[dict]:
    """Find top-K most similar work items for a given source_id.

    Returns list of dicts with keys: work_item_id, title, score, relation_type.
    """
    if top_k is None:
        top_k = TOP_K

    conn = get_connection()
    try:
        cur = conn.cursor()

        # Get source embedding
        cur.execute(
            "SELECT embedding FROM ado_work_item_embeddings WHERE work_item_id = %s",
            (source_id,),
        )
        row = cur.fetchone()
        if not row:
            return None  # signals "not found"

        source_vec = np.array(row[0], dtype=np.float64)

        # Get all other embeddings
        cur.execute(
            "SELECT work_item_id, embedding FROM ado_work_item_embeddings WHERE work_item_id <> %s",
            (source_id,),
        )
        all_rows = cur.fetchall()

        sims = []
        for wid, emb in all_rows:
            v = np.array(emb, dtype=np.float64)
            sims.append((wid, cosine_similarity(source_vec, v)))

        sims.sort(key=lambda x: x[1], reverse=True)
        top = sims[:top_k]

        # Fetch titles for top results
        top_ids = [wid for wid, _ in top]
        if top_ids:
            cur.execute(
                "SELECT id, title FROM ado_work_items WHERE id = ANY(%s)",
                (top_ids,),
            )
            title_map = dict(cur.fetchall())
        else:
            title_map = {}

        cur.close()

        results = []
        for wid, score in top:
            results.append({
                "work_item_id": wid,
                "title": title_map.get(wid, ""),
                "score": round(score, 4),
                "relation_type": decide_relation(score),
            })
        return results
    finally:
        conn.close()


def save_relations(source_id: int, relations: list[dict]) -> dict:
    """Save relations to ado_work_item_relations.

    relations: list of dicts with keys: target_id, relation_type, similarity.
    Returns: {saved: int}
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        saved = 0
        for rel in relations:
            if rel.get("relation_type") is None:
                continue
            cur.execute("""
                INSERT INTO ado_work_item_relations (source_id, target_id, relation_type, similarity)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (source_id, target_id) DO UPDATE SET
                    relation_type = EXCLUDED.relation_type,
                    similarity = EXCLUDED.similarity,
                    created_at = CURRENT_TIMESTAMP
            """, (source_id, rel["target_id"], rel["relation_type"], rel["similarity"]))
            saved += 1
        conn.commit()
        cur.close()
        return {"saved": saved}
    finally:
        conn.close()
