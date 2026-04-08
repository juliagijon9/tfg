import os
import base64
import requests
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

ORG = os.getenv("ADO_ORG")
PROJECT = os.getenv("ADO_PROJECT")
PAT = os.getenv("ADO_PAT")

PG_HOST = os.getenv("POSTGRES_HOST", "localhost")
PG_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
PG_DB = os.getenv("POSTGRES_DB")
PG_USER = os.getenv("POSTGRES_USER")
PG_PASS = os.getenv("POSTGRES_PASSWORD")

API_VERSION = "7.1-preview.2"

def get_headers(pat: str) -> dict:
    token = base64.b64encode(f":{pat}".encode()).decode()
    return {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json"
    }

def wiql_get_ids() -> listurl = f"https://dev.azure.com/{ORG}/{PROJECT}/_apis/wit/wiql?api-version={API_VERSION}"

    wiql = {
        "query": f"""
        SELECT [System.Id]
        FROM WorkItems
        WHERE [System.TeamProject] = '{PROJECT}'
          AND [System.State] <> 'Closed'
          AND [System.ChangedDate] >= @Today - 180
        ORDER BY [System.ChangedDate] DESC
        """
    }

    r = requests.post(url, headers=get_headers(PAT), json=wiql, timeout=30)
    r.raise_for_status()
    data = r.json()
    return [x["id"] for x in data.get("workItems", [])]

def get_work_items_batch(ids: list[int], fields: list[str]) -> listurl = f"https://dev.azure.com/{ORG}/_apis/wit/workitemsbatch?api-version={API_VERSION}"
    items = []

    for i in range(0, len(ids), 200):
        chunk = ids[i:i+200]
        payload = {"ids": chunk, "fields": fields}
        r = requests.post(url, headers=get_headers(PAT), json=payload, timeout=60)
        r.raise_for_status()
        items.extend(r.json().get("value", []))

    return items

def ensure_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS ado_work_items (
          id BIGINT PRIMARY KEY,
          work_item_type TEXT,
          title TEXT,
          state TEXT,
          created_date TIMESTAMP,
          changed_date TIMESTAMP,
          area_path TEXT,
          iteration_path TEXT,
          assigned_to TEXT,
          tags TEXT,
          description TEXT
        );
        """)
    conn.commit()

def upsert_items(conn, items: list[dict]):
    rows = []
    for it in items:
        f = it.get("fields", {})
        assigned_to = f.get("System.AssignedTo")
        if isinstance(assigned_to, dict):
            assigned_to = assigned_to.get("displayName")

        rows.append((
            it.get("id"),
            f.get("System.WorkItemType"),
            f.get("System.Title"),
            f.get("System.State"),
            f.get("System.CreatedDate"),
            f.get("System.ChangedDate"),
            f.get("System.AreaPath"),
            f.get("System.IterationPath"),
            assigned_to,
            f.get("System.Tags"),
            f.get("System.Description"),
        ))

    sql = """
    INSERT INTO ado_work_items
    (id, work_item_type, title, state, created_date, changed_date, area_path, iteration_path, assigned_to, tags, description)
    VALUES %s
    ON CONFLICT (id) DO UPDATE SET
      work_item_type = EXCLUDED.work_item_type,
      title = EXCLUDED.title,
      state = EXCLUDED.state,
      created_date = EXCLUDED.created_date,
      changed_date = EXCLUDED.changed_date,
      area_path = EXCLUDED.area_path,
      iteration_path = EXCLUDED.iteration_path,
      assigned_to = EXCLUDED.assigned_to,
      tags = EXCLUDED.tags,
      description = EXCLUDED.description;
    """

    with conn.cursor() as cur:
        execute_values(cur, sql, rows)
    conn.commit()

def main():
    if not all([ORG, PROJECT, PAT]):
        raise RuntimeError("Faltan ADO_ORG / ADO_PROJECT / ADO_PAT en el .env")

    if not all([PG_DB, PG_USER, PG_PASS]):
        raise RuntimeError("Faltan POSTGRES_DB / POSTGRES_USER / POSTGRES_PASSWORD en el .env")

    conn = psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DB,
        user=PG_USER,
        password=PG_PASS,
    )

    ensure_table(conn)

    ids = wiql_get_ids()
    print(f"IDs encontrados: {len(ids)}")
    if not ids:
        print("No hay items para guardar.")
        return

    fields = [
        "System.Id",
        "System.WorkItemType",
        "System.Title",
        "System.State",
        "System.CreatedDate",
        "System.ChangedDate",
        "System.AreaPath",
        "System.IterationPath",
        "System.AssignedTo",
        "System.Tags",
        "System.Description"
    ]

    items = get_work_items_batch(ids, fields)
    print(f"Items descargados: {len(items)}")

    upsert_items(conn, items)
    print("✅ Guardado/actualización en Postgres completado.")

    conn.close()

if __name__ == "__main__":
    main()
