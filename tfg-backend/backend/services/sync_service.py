import os
import base64
import requests
from psycopg2.extras import execute_values

from backend.db import get_connection

API_VERSION_WIQL = "7.1-preview.2"
API_VERSION_BATCH = "7.1-preview.1"


def _get_headers(pat: str) -> dict:
    token = base64.b64encode(f":{pat}".encode()).decode()
    return {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
    }


def _wiql_get_ids(org: str, project: str, pat: str) -> list[int]:
    url = f"https://dev.azure.com/{org}/{project}/_apis/wit/wiql?api-version={API_VERSION_WIQL}"
    wiql = {
        "query": f"""
        SELECT [System.Id]
        FROM WorkItems
        WHERE [System.TeamProject] = '{project}'
          AND [System.State] <> 'Closed'
          AND [System.ChangedDate] >= @Today - 180
        ORDER BY [System.ChangedDate] DESC
        """
    }
    r = requests.post(url, headers=_get_headers(pat), json=wiql, timeout=30)
    r.raise_for_status()
    return [item["id"] for item in r.json().get("workItems", [])]


def _get_work_items_batch(ids: list[int], fields: list[str], org: str, pat: str) -> list[dict]:
    url = f"https://dev.azure.com/{org}/_apis/wit/workitemsbatch?api-version={API_VERSION_BATCH}"
    items: list[dict] = []
    for i in range(0, len(ids), 200):
        chunk = ids[i : i + 200]
        payload = {"ids": chunk, "fields": fields}
        r = requests.post(url, headers=_get_headers(pat), json=payload, timeout=60)
        r.raise_for_status()
        items.extend(r.json().get("value", []))
    return items


def _upsert_items(conn, items: list[dict]):
    rows = []
    for it in items:
        fields = it.get("fields", {})
        assigned = fields.get("System.AssignedTo")
        if isinstance(assigned, dict):
            assigned = assigned.get("displayName")
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
        description = EXCLUDED.description,
        repro_steps = EXCLUDED.repro_steps,
        acceptance_criteria = EXCLUDED.acceptance_criteria;
    """
    with conn.cursor() as cur:
        execute_values(cur, sql, rows)
    conn.commit()


FIELDS = [
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
    "Microsoft.VSTS.Common.AcceptanceCriteria",
]


def run_sync() -> dict:
    """Run full ADO → PostgreSQL sync. Returns stats dict."""
    org = os.getenv("ADO_ORG")
    project = os.getenv("ADO_PROJECT")
    pat = os.getenv("ADO_PAT")

    if not all([org, project, pat]):
        raise ValueError("Missing ADO credentials: ADO_ORG, ADO_PROJECT, ADO_PAT")

    ids = _wiql_get_ids(org, project, pat)
    items = _get_work_items_batch(ids, FIELDS, org, pat)

    conn = get_connection()
    try:
        _upsert_items(conn, items)
    finally:
        conn.close()

    return {"ids_found": len(ids), "items_upserted": len(items)}
