import os
import base64
from datetime import datetime, timezone
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
# 0. Obtener la fecha de última modificación sincronizada
# ---------------------------
def get_last_changed_date():
    """Devuelve (fecha_wiql, fecha_dt):
    - fecha_wiql: solo YYYY-MM-DD para la query WIQL (la API no acepta hora)
    - fecha_dt: objeto datetime con microsegundos para comparaciones exactas en BD y en Python
    """
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASS
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(changed_date) FROM ado_work_items")  # Fecha de modificación más reciente en BD
            row = cur.fetchone()
            if row and row[0]:
                return row[0].strftime("%Y-%m-%d"), row[0]  # Devuelve string para WIQL y datetime para comparaciones
            return "1970-01-01", datetime(1970, 1, 1)       # Si la tabla está vacía, descarga todo
    finally:
        conn.close()


# ---------------------------
# 1. Obtener IDs con WIQL
# ---------------------------
def wiql_get_ids(last_changed_wiql: str) -> list[int]:
    """Devuelve todos los IDs modificados desde last_changed_wiql (solo fecha, límite de la API ADO)."""
    url = f"https://dev.azure.com/{ORG}/{PROJECT}/_apis/wit/wiql?api-version={API_VERSION_WIQL}"

    wiql = {
        "query": f"""
        SELECT [System.Id]
        FROM WorkItems
        WHERE [System.TeamProject] = '{PROJECT}'
          AND [System.ChangedDate] >= '{last_changed_wiql}'
          AND [System.WorkItemType] IN ('Bug', 'Feature', 'Product Backlog Item', 'Task', 'Delivery')
        ORDER BY [System.Id] ASC
        """
        # AND [System.ChangedDate] >= '{last_changed_wiql}' → la API solo acepta fecha sin hora
        # AND [System.WorkItemType] IN (...)                → filtra solo los 5 tipos relevantes para el TFG
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
# 1b. Filtrar IDs por fecha+hora exacta
# ---------------------------
def filter_ids_by_changed_date(ids: list[int], last_changed_db) -> list[int]:
    """Descarga solo System.ChangedDate para los IDs del día y filtra los realmente modificados
    después de last_changed_db (objeto datetime con microsegundos)."""
    if not ids:
        return []

    last_dt = last_changed_db.replace(tzinfo=timezone.utc) if last_changed_db.tzinfo is None else last_changed_db

    url = f"https://dev.azure.com/{ORG}/_apis/wit/workitemsbatch?api-version={API_VERSION_BATCH}"
    filtered = []

    for i in range(0, len(ids), 200):
        chunk = ids[i:i + 200]
        payload = {"ids": chunk, "fields": ["System.Id", "System.ChangedDate"]}  # Solo fecha, sin descargar todo el ticket
        r = requests.post(url, headers=get_headers(PAT), json=payload, timeout=60)
        r.raise_for_status()
        for item in r.json().get("value", []):
            changed_str = item.get("fields", {}).get("System.ChangedDate", "")
            if changed_str:
                changed_dt = datetime.fromisoformat(changed_str.replace("Z", "+00:00"))
                if changed_dt > last_dt:  # Solo los modificados después del segundo exacto de la última sync
                    filtered.append(item["id"])
            else:
                filtered.append(item["id"])  # Sin fecha → incluir por seguridad

    print(f"   WIQL devolvió {len(ids)} IDs → tras filtro por hora exacta: {len(filtered)}")
    return filtered


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
        acceptance_criteria = EXCLUDED.acceptance_criteria
    where
    row(coalesce(ado_work_items.work_item_type, ''), coalesce(ado_work_items.title, ''), coalesce(ado_work_items.state, ''), coalesce(ado_work_items.area_path, ''), coalesce(ado_work_items.iteration_path, ''), coalesce(ado_work_items.tags, ''), coalesce(ado_work_items.description, ''), coalesce(ado_work_items.repro_steps, ''), coalesce(ado_work_items.acceptance_criteria, '')) <>
    row(coalesce(EXCLUDED.work_item_type, ''), coalesce(EXCLUDED.title, ''), coalesce(EXCLUDED.state, ''), coalesce(EXCLUDED.area_path, ''), coalesce(EXCLUDED.iteration_path, ''), coalesce(EXCLUDED.tags, ''), coalesce(EXCLUDED.description, ''), coalesce(EXCLUDED.repro_steps, ''), coalesce(EXCLUDED.acceptance_criteria, ''))
    ;
    """

    with conn.cursor() as cur:
        execute_values(cur, sql, rows)  # Inserta todas las filas de golpe, más eficiente que una a una

    conn.commit()


# ---------------------------
# 4. Borrar datos de IA de tickets modificados
# ---------------------------
def delete_ia_data_modified(last_changed: str) -> None:
    """Borra los datos de IA de todos los tickets modificados después de last_changed,
    para que el pipeline los reprocese con el contenido actualizado."""
    conn = psycopg2.connect(host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASS)
    cur = conn.cursor()
    cur.execute("DELETE FROM ado_work_item_tag WHERE work_item_id IN (SELECT id FROM ado_work_items WHERE changed_date > %s)", (last_changed,))
    tags = cur.rowcount
    cur.execute("DELETE FROM ado_work_item_classifications WHERE work_item_id IN (SELECT id FROM ado_work_items WHERE changed_date > %s)", (last_changed,))
    classifications = cur.rowcount
    cur.execute("DELETE FROM ado_work_item_intentions WHERE work_item_id IN (SELECT id FROM ado_work_items WHERE changed_date > %s)", (last_changed,))
    intentions = cur.rowcount
    cur.execute("DELETE FROM ado_work_item_relations WHERE target_id IN (SELECT id FROM ado_work_items WHERE changed_date > %s)", (last_changed,))
    relations_target = cur.rowcount
    cur.execute("DELETE FROM ado_work_item_relations WHERE source_id IN (SELECT id FROM ado_work_items WHERE changed_date > %s)", (last_changed,))
    relations_source = cur.rowcount
    cur.execute("DELETE FROM ado_work_item_embeddings WHERE work_item_id IN (SELECT id FROM ado_work_items WHERE changed_date > %s)", (last_changed,))
    embeddings = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    print(f"🗑️  Datos de IA eliminados para tickets modificados después de {last_changed}:")
    print(f"   Tags: {tags} | Clasificaciones: {classifications} | Intenciones: {intentions}")
    print(f"   Relaciones: {relations_target + relations_source} | Embeddings: {embeddings}")


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

    last_changed_wiql, last_changed_db = get_last_changed_date()    # Paso 1: obtiene la fecha en dos formatos
    print(f"📌 Última fecha de modificación sincronizada: {last_changed_db}")

    ids = wiql_get_ids(last_changed_wiql)                          # Paso 2: obtiene IDs del día (la API no acepta hora)
    ids = filter_ids_by_changed_date(ids, last_changed_db)         # Paso 3: filtra en Python por hora exacta

    if not ids:
        print("✅ No hay tickets nuevos ni modificados desde la última sincronización.")
        conn.close()
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
        "System.Description",
        "Microsoft.VSTS.TCM.ReproSteps",
        "Microsoft.VSTS.Common.AcceptanceCriteria"
    ]

    items = get_work_items_batch(ids, fields)                      # Paso 4: descarga el detalle solo de los filtrados
    print(f"Items descargados: {len(items)}")

    upsert_items(conn, items)                                      # Paso 5: inserta/actualiza tickets (actualiza changed_date en BD)
    conn.close()
    delete_ia_data_modified(last_changed_db)                       # Paso 6: borra IA después del upsert, ya con changed_date actualizada en BD
    print("✅ Sincronización completada")


if __name__ == "__main__":
    main()
