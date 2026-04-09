from backend.db import get_connection


def get_metrics() -> dict:
    """Return all validation metrics as a JSON-serializable dict."""
    conn = get_connection()
    try:
        cur = conn.cursor()

        # 1) Total relations
        cur.execute("SELECT COUNT(*) FROM ado_work_item_relations")
        total_relations = cur.fetchone()[0]

        if total_relations == 0:
            return {"total_relations": 0, "message": "No relations found. Run linking first."}

        # 2) Distribution by type
        cur.execute("""
            SELECT relation_type, COUNT(*) AS cnt
            FROM ado_work_item_relations
            GROUP BY relation_type
            ORDER BY cnt DESC
        """)
        by_type = [
            {"relation_type": rtype, "count": cnt, "pct": round(cnt / total_relations * 100, 1)}
            for rtype, cnt in cur.fetchall()
        ]

        # 3) Histogram of similarity scores
        cur.execute("""
            SELECT
                CASE
                    WHEN similarity >= 0.95 THEN '0.95-1.00'
                    WHEN similarity >= 0.90 THEN '0.90-0.95'
                    WHEN similarity >= 0.85 THEN '0.85-0.90'
                    ELSE                         '0.80-0.85'
                END AS bucket,
                COUNT(*) AS cnt
            FROM ado_work_item_relations
            GROUP BY bucket
            ORDER BY bucket
        """)
        histogram = [{"bucket": bucket, "count": cnt} for bucket, cnt in cur.fetchall()]

        # 4) Statistics
        cur.execute("""
            SELECT
                MIN(similarity),
                MAX(similarity),
                AVG(similarity),
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY similarity)
            FROM ado_work_item_relations
        """)
        mn, mx, avg, median = cur.fetchone()
        stats = {
            "min": round(float(mn), 4),
            "max": round(float(mx), 4),
            "avg": round(float(avg), 4),
            "median": round(float(median), 4),
        }

        # 5) Top 5 most similar pairs
        cur.execute("""
            SELECT r.source_id, s.title, r.target_id, t.title,
                   r.similarity, r.relation_type
            FROM ado_work_item_relations r
            JOIN ado_work_items s ON s.id = r.source_id
            JOIN ado_work_items t ON t.id = r.target_id
            ORDER BY r.similarity DESC
            LIMIT 5
        """)
        top_pairs = [
            {
                "source_id": src_id,
                "source_title": src_title or "",
                "target_id": tgt_id,
                "target_title": tgt_title or "",
                "similarity": round(float(sim), 4),
                "relation_type": rel,
            }
            for src_id, src_title, tgt_id, tgt_title, sim, rel in cur.fetchall()
        ]

        # 6) Top 5 hubs
        cur.execute("""
            SELECT source_id, w.title, COUNT(*) AS cnt
            FROM ado_work_item_relations r
            JOIN ado_work_items w ON w.id = r.source_id
            GROUP BY source_id, w.title
            ORDER BY cnt DESC
            LIMIT 5
        """)
        hubs = [
            {"source_id": sid, "title": title or "", "count": cnt}
            for sid, title, cnt in cur.fetchall()
        ]

        # 7) Coverage
        cur.execute("""
            SELECT
                COUNT(DISTINCT r.source_id) AS with_relations,
                (SELECT COUNT(*) FROM ado_work_items) AS total
            FROM ado_work_item_relations r
        """)
        with_rel, total_items = cur.fetchone()
        coverage = {
            "with_relations": with_rel,
            "total": total_items,
            "pct": round(with_rel / total_items * 100, 1) if total_items > 0 else 0,
        }

        # 8) Distribution by work_item_type
        cur.execute("""
            SELECT w.work_item_type, r.relation_type, COUNT(*) AS cnt
            FROM ado_work_item_relations r
            JOIN ado_work_items w ON w.id = r.source_id
            GROUP BY w.work_item_type, r.relation_type
            ORDER BY w.work_item_type, r.relation_type
        """)
        by_work_item_type = [
            {"work_item_type": wtype or "N/A", "relation_type": rtype, "count": cnt}
            for wtype, rtype, cnt in cur.fetchall()
        ]

        cur.close()

        return {
            "total_relations": total_relations,
            "by_type": by_type,
            "histogram": histogram,
            "stats": stats,
            "top_pairs": top_pairs,
            "hubs": hubs,
            "coverage": coverage,
            "by_work_item_type": by_work_item_type,
        }
    finally:
        conn.close()
