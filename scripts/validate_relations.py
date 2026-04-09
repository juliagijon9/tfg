import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

PG_HOST = os.getenv("POSTGRES_HOST", "localhost")
PG_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
PG_DB = os.getenv("POSTGRES_DB")
PG_USER = os.getenv("POSTGRES_USER")
PG_PASS = os.getenv("POSTGRES_PASSWORD")


def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def main():
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASS
    )
    cur = conn.cursor()

    # 1) Total de relaciones
    section("1. Total de relaciones")
    cur.execute("SELECT COUNT(*) FROM ado_work_item_relations")
    total = cur.fetchone()[0]
    print(f"   Total: {total}")

    if total == 0:
        print("\n⚠️  No hay relaciones. Ejecuta link_related.py primero.")
        cur.close()
        conn.close()
        return

    # 2) Distribución por tipo
    section("2. Distribución por relation_type")
    cur.execute("""
        SELECT relation_type, COUNT(*) AS cnt
        FROM ado_work_item_relations
        GROUP BY relation_type
        ORDER BY cnt DESC
    """)
    for rtype, cnt in cur.fetchall():
        pct = cnt / total * 100
        print(f"   {rtype:12s}  {cnt:>6d}  ({pct:.1f}%)")

    # 3) Histograma de scores
    section("3. Histograma de similarity scores")
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
    for bucket, cnt in cur.fetchall():
        bar = "█" * max(1, cnt * 40 // total)
        print(f"   {bucket}  {cnt:>6d}  {bar}")

    # 4) Estadísticas de scores
    section("4. Estadísticas de similarity")
    cur.execute("""
        SELECT
            MIN(similarity),
            MAX(similarity),
            AVG(similarity),
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY similarity)
        FROM ado_work_item_relations
    """)
    mn, mx, avg, median = cur.fetchone()
    print(f"   Min:    {mn:.4f}")
    print(f"   Max:    {mx:.4f}")
    print(f"   Mean:   {avg:.4f}")
    print(f"   Median: {median:.4f}")

    # 5) Top 5 pares más similares
    section("5. Top 5 pares más similares")
    cur.execute("""
        SELECT r.source_id, s.title, r.target_id, t.title,
               r.similarity, r.relation_type
        FROM ado_work_item_relations r
        JOIN ado_work_items s ON s.id = r.source_id
        JOIN ado_work_items t ON t.id = r.target_id
        ORDER BY r.similarity DESC
        LIMIT 5
    """)
    for src_id, src_title, tgt_id, tgt_title, sim, rel in cur.fetchall():
        print(f"   [{rel:9s}] {sim:.4f}  #{src_id} → #{tgt_id}")
        print(f"              SRC: {(src_title or '')[:70]}")
        print(f"              TGT: {(tgt_title or '')[:70]}")
        print()

    # 6) Tickets hub (más relaciones)
    section("6. Top 5 tickets con más relaciones (hubs)")
    cur.execute("""
        SELECT source_id, w.title, COUNT(*) AS cnt
        FROM ado_work_item_relations r
        JOIN ado_work_items w ON w.id = r.source_id
        GROUP BY source_id, w.title
        ORDER BY cnt DESC
        LIMIT 5
    """)
    for sid, title, cnt in cur.fetchall():
        print(f"   #{sid}  ({cnt} relaciones)  {(title or '')[:60]}")

    # 7) Cobertura
    section("7. Cobertura")
    cur.execute("""
        SELECT
            COUNT(DISTINCT r.source_id) AS con_relacion,
            (SELECT COUNT(*) FROM ado_work_items) AS total
        FROM ado_work_item_relations r
    """)
    con_rel, total_tickets = cur.fetchone()
    pct = con_rel / total_tickets * 100 if total_tickets > 0 else 0
    print(f"   Tickets con relaciones: {con_rel} / {total_tickets}  ({pct:.1f}%)")

    # 8) Distribución por work_item_type
    section("8. Relaciones por work_item_type (source)")
    cur.execute("""
        SELECT w.work_item_type, r.relation_type, COUNT(*) AS cnt
        FROM ado_work_item_relations r
        JOIN ado_work_items w ON w.id = r.source_id
        GROUP BY w.work_item_type, r.relation_type
        ORDER BY w.work_item_type, r.relation_type
    """)
    for wtype, rtype, cnt in cur.fetchall():
        print(f"   {(wtype or 'N/A'):20s}  {rtype:12s}  {cnt:>6d}")

    cur.close()
    conn.close()
    print(f"\n{'='*60}")
    print("✅ Validación completada")


if __name__ == "__main__":
    main()
