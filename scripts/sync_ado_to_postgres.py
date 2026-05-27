import os
import base64
import requests
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()  # Carga las variables del fichero .env

# ---------------------------
# Azure DevOps configuration
# ---------------------------
ORG = os.getenv("ADO_ORG")           # Nombre de la organización en Azure DevOps
PROJECT = os.getenv("ADO_PROJECT")   # Nombre del proyecto dentro de la organización
PAT = os.getenv("ADO_PAT")           # Personal Access Token para autenticarse con la API de ADO
API_VERSION_WIQL = "7.1-preview.2"   # Versión de la API para queries WIQL (búsqueda de IDs)
API_VERSION_BATCH = "7.1-preview.1"  # Versión de la API para descarga en batch (detalle de tickets)

# ---------------------------
# PostgreSQL configuration
# ---------------------------
PG_HOST = os.getenv("POSTGRES_HOST", "localhost")
PG_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
PG_DB = os.getenv("POSTGRES_DB")
PG_USER = os.getenv("POSTGRES_USER")
PG_PASS = os.getenv("POSTGRES_PASSWORD")


# ---------------------------
# Helpers
# ---------------------------
def get_headers(pat: str) -> dict:
    token = base64.b64encode(f":{pat}".encode()).decode()  # Codifica el PAT en Base64 para autenticación Basic Auth
    return {
        "Authorization": f"Basic {token}",  # Cabecera requerida por la API de Azure DevOps
        "Content-Type": "application/json"
    }


# ---------------------------
# 0. Obtener el max ID ya sincronizado
# ---------------------------
def get_max_synced_id() -> int:
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASS
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(id) FROM ado_work_items")  # Busca el ID más alto ya guardado en BD
            row = cur.fetchone()
            return row[0] if row and row[0] else 0  # Si la tabla está vacía devuelve 0 para traer todo
    finally:
        conn.close()


# ---------------------------
# 1. Obtener IDs con WIQL
# ---------------------------
def wiql_get_ids(min_id: int) -> list[int]:
    url = f"https://dev.azure.com/{ORG}/{PROJECT}/_apis/wit/wiql?api-version={API_VERSION_WIQL}"

    wiql = {
        "query": f"""
        SELECT [System.Id]
        FROM WorkItems
        WHERE [System.TeamProject] = '{PROJECT}'
          AND [System.Id] > {min_id}
          AND [System.WorkItemType] IN ('Bug', 'Feature', 'Product Backlog Item', 'Task', 'Delivery')
        ORDER BY [System.Id] ASC
        """
        # AND [System.Id] > {min_id}               → sincronización incremental: solo IDs nuevos
        # AND [System.WorkItemType] IN (...)        → filtra solo los 5 tipos relevantes para el TFG
    }

    r = requests.post(url, headers=get_headers(PAT), json=wiql, timeout=30)  # Ejecuta la query WIQL en ADO

    if r.status_code != 200:
        print("❌ ERROR ejecutando WIQL")
        print("Status:", r.status_code)
        print("Respuesta:", r.text)
        r.raise_for_status()

    data = r.json()
    return [item["id"] for item in data.get("workItems", [])]  # Devuelve solo la lista de IDs, sin detalle


# ---------------------------
# 2. Descargar work items por batch
# ---------------------------
def get_work_items_batch(ids: list[int], fields: list[str]) -> list[dict]:
    url = f"https://dev.azure.com/{ORG}/_apis/wit/workitemsbatch?api-version={API_VERSION_BATCH}"
    items: list[dict] = []

    for i in range(0, len(ids), 200):       # Divide los IDs en grupos de 200 (límite máximo de la API)
        chunk = ids[i:i + 200]
        payload = {
            "ids": chunk,
            "fields": fields                # Solo descarga los campos especificados, no el ticket completo
        }

        r = requests.post(url, headers=get_headers(PAT), json=payload, timeout=60)

        if r.status_code != 200:
            print("❌ ERROR en workitemsbatch")
            print("Status:", r.status_code)
            print("Respuesta:", r.text[:2000])
            print("IDs problemáticos (ejemplo):", chunk[:5])
            r.raise_for_status()

        items.extend(r.json().get("value", []))  # Acumula los tickets de cada batch en la lista total

    return items


# ---------------------------
# 3. Base de datos
# ---------------------------

def upsert_items(conn, items: list[dict]):
    rows = []

    for it in items:
        fields = it.get("fields", {})
        assigned = fields.get("System.AssignedTo")
        if isinstance(assigned, dict):
            assigned = assigned.get("displayName")  # AssignedTo viene como objeto; extraemos solo el nombre

        rows.append((
            it.get("id"),
            fields.get("System.WorkItemType"),
            fields.get("System.Title"),
            fields.get("System.State"),
            fields.get("System.CreatedDate"),
            fields.get("System.ChangedDate"),
            fields.get("System.AreaPath"),
            fields.get("System.IterationPath"),
            assigned,
            fields.get("System.Tags"),
            fields.get("System.Description"),
            fields.get("Microsoft.VSTS.TCM.ReproSteps"),
            fields.get("Microsoft.VSTS.Common.AcceptanceCriteria"),
        ))

    sql = """
    INSERT INTO ado_work_items
    (id, work_item_type, title, state, created_date, changed_date,
     area_path, iteration_path, assigned_to, tags, description,
     repro_steps, acceptance_criteria)
    VALUES %s
    ON CONFLICT (id) DO UPDATE SET          -- Si el ticket ya existe, actualiza todos sus campos con los nuevos valores
        work_item_type = EXCLUDED.work_item_type,
        title = EXCLUDED.title,
        state = EXCLUDED.state,
        created_date = EXCLUDED.created_date,
        changed_date = EXCLUDED.changed_date,
        area_path = EXCLUDED.area_path,
        iteration_path = EXCLUDED.iteration_path,
        assigned_to = EXCLUDED.assigned_to,
        tags = EXCLUDED.tags,
        description = EXCLUDED.description,
        repro_steps = EXCLUDED.repro_steps,
        acceptance_criteria = EXCLUDED.acceptance_criteria;
    """

    with conn.cursor() as cur:
        execute_values(cur, sql, rows)  # Inserta todas las filas de golpe, más eficiente que una a una

    conn.commit()


# ---------------------------
# Main
# ---------------------------
def main():
    conn = psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DB,
        user=PG_USER,
        password=PG_PASS
    )

    max_id = get_max_synced_id()           # Paso 1: obtiene el último ID ya sincronizado
    print(f"📌 Max ID sincronizado: {max_id}")

    ids = wiql_get_ids(max_id)             # Paso 2: consulta ADO para obtener solo los IDs nuevos
    print(f"IDs encontrados: {len(ids)}")

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
        "System.Description",
        "Microsoft.VSTS.TCM.ReproSteps",
        "Microsoft.VSTS.Common.AcceptanceCriteria"
    ]

    items = get_work_items_batch(ids, fields)  # Paso 3: descarga el detalle de cada ticket en batches
    print(f"Items descargados: {len(items)}")

    upsert_items(conn, items)              # Paso 4: inserta o actualiza los tickets en PostgreSQL
    conn.close()
    print("✅ Sincronización completada")


if __name__ == "__main__":
    main()
