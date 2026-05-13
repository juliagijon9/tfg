"""API FastAPI del asistente de triaje de tickets de Iberia Express."""

import json
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from backend.db import get_connection

# ---------------------------
# Configuración
# ---------------------------
SCRIPTS_DIR = "/scripts"

# Minutos mínimos en estado 'running' para poder forzar el fallo manualmente
MIN_MINUTES_TO_FORCE_FAIL = 30

PIPELINE_STEPS = [
    "sync_ado_to_postgres.py",
    "generate_embeddings.py",
    "link_related.py",
    "extract_intention.py",
    "classify_tickets.py",
    "tag_tickets.py",
]


# ---------------------------
# Gestión de jobs en PostgreSQL
# ---------------------------

def _has_running_job() -> dict | None:
    """Devuelve el job en curso si existe, None si no hay ninguno."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT job_id, type FROM pipeline_jobs WHERE status = 'running' LIMIT 1"
            )
            row = cur.fetchone()
    finally:
        conn.close()
    return {"job_id": row[0], "type": row[1]} if row else None


def _create_job(job_type: str) -> str:
    job_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pipeline_jobs (job_id, type, status, started_at) VALUES (%s, %s, 'pending', NOW())",
                (job_id, job_type)
            )
        conn.commit()
    finally:
        conn.close()
    return job_id


def _update_job(job_id: str, status: str, result: dict | None = None, error: str | None = None) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            finished_at = datetime.now(timezone.utc) if status in ("completed", "failed") else None
            cur.execute("""
                UPDATE pipeline_jobs
                SET status = %s,
                    result = %s,
                    error  = %s,
                    finished_at = %s
                WHERE job_id = %s
            """, (status, json.dumps(result) if result else None, error, finished_at, job_id))
        conn.commit()
    finally:
        conn.close()


def _get_job(job_id: str) -> dict | None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT job_id, type, status, result, error, started_at, finished_at
                FROM pipeline_jobs WHERE job_id = %s
            """, (job_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {
        "job_id": row[0], "type": row[1], "status": row[2],
        "result": row[3], "error": row[4],
        "started_at": row[5].isoformat() if row[5] else None,
        "finished_at": row[6].isoformat() if row[6] else None,
    }


def _list_jobs(limit: int = 20) -> list[dict]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT job_id, type, status, error, started_at, finished_at
                FROM pipeline_jobs
                ORDER BY started_at DESC
                LIMIT %s
            """, (limit,))
            rows = cur.fetchall()
    finally:
        conn.close()
    return [
        {
            "job_id": r[0], "type": r[1], "status": r[2], "error": r[3],
            "started_at": r[4].isoformat() if r[4] else None,
            "finished_at": r[5].isoformat() if r[5] else None,
        }
        for r in rows
    ]


def _cleanup_orphan_jobs() -> None:
    """Al arrancar, marca como failed los jobs que quedaron en running."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE pipeline_jobs
                SET status = 'failed',
                    error  = 'Proceso interrumpido (backend reiniciado)',
                    finished_at = NOW()
                WHERE status = 'running'
            """)
            count = cur.rowcount
        conn.commit()
        if count > 0:
            print(f"⚠️  {count} job(s) huérfanos marcados como failed al arrancar.")
    finally:
        conn.close()


# ---------------------------
# Startup
# ---------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    _cleanup_orphan_jobs()
    yield


app = FastAPI(title="TFG Triage API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------
# Helpers internos
# ---------------------------

def _run_script(script_name: str) -> tuple[bool, str]:
    path = f"{SCRIPTS_DIR}/{script_name}"
    result = subprocess.run([sys.executable, path], capture_output=True, text=True)
    output = result.stdout
    if result.stderr:
        output += f"\n[STDERR]\n{result.stderr}"
    return result.returncode == 0, output


def _run_steps_job(job_id: str, steps: list[str]) -> None:
    _update_job(job_id, status="running")
    steps_output = []
    for step in steps:
        success, output = _run_script(step)
        steps_output.append({"step": step, "output": output, "ok": success})
        if not success:
            _update_job(job_id, status="failed",
                        result={"steps": steps_output},
                        error=f"Falló el paso: {step}")
            return
    _update_job(job_id, status="completed", result={"steps": steps_output})


# ---------------------------
# Health
# ---------------------------

@app.get("/health")
def health():
    try:
        conn = get_connection()
        conn.close()
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok", "db_connected": db_ok}


# ---------------------------
# Jobs
# ---------------------------

@app.get("/jobs")
def list_jobs(limit: int = 20):
    """Lista los últimos jobs ejecutados."""
    return _list_jobs(limit)


@app.get("/jobs/{job_id}")
def job_status(job_id: str):
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job no encontrado")
    return job


@app.patch("/jobs/{job_id}/fail")
def force_fail_job(job_id: str):
    """Fuerza el estado a failed. Solo permitido si lleva más de MIN_MINUTES_TO_FORCE_FAIL minutos en running."""
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job no encontrado")
    if job["status"] != "running":
        raise HTTPException(status_code=400, detail=f"El job no está en running (estado: {job['status']})")

    started = datetime.fromisoformat(job["started_at"])
    elapsed_minutes = (datetime.now(timezone.utc) - started).total_seconds() / 60
    if elapsed_minutes < MIN_MINUTES_TO_FORCE_FAIL:
        remaining = int(MIN_MINUTES_TO_FORCE_FAIL - elapsed_minutes)
        raise HTTPException(
            status_code=400,
            detail=f"El job lleva solo {int(elapsed_minutes)} min en running. "
                   f"Espera {remaining} min más antes de forzar el fallo."
        )

    _update_job(job_id, status="failed", error="Marcado como fallido manualmente por el usuario")
    return {"ok": True, "job_id": job_id}


# ---------------------------
# Pipeline — pasos individuales
# ---------------------------

def _check_no_running():
    """Lanza 409 si hay un job en curso."""
    running = _has_running_job()
    if running:
        raise HTTPException(
            status_code=409,
            detail=f"Ya hay un proceso en curso ({running['type']}, job {running['job_id'][:8]}…). Espera a que termine."
        )

@app.post("/pipeline/sync")
def pipeline_sync(bg: BackgroundTasks):
    _check_no_running()
    job_id = _create_job("sync")
    bg.add_task(_run_steps_job, job_id, ["sync_ado_to_postgres.py"])
    return {"job_id": job_id}


@app.post("/pipeline/embeddings")
def pipeline_embeddings(bg: BackgroundTasks):
    _check_no_running()
    job_id = _create_job("embeddings")
    bg.add_task(_run_steps_job, job_id, ["generate_embeddings.py"])
    return {"job_id": job_id}


@app.post("/pipeline/link-related")
def pipeline_link_related(bg: BackgroundTasks):
    _check_no_running()
    job_id = _create_job("link-related")
    bg.add_task(_run_steps_job, job_id, ["link_related.py"])
    return {"job_id": job_id}


@app.post("/pipeline/extract-intention")
def pipeline_extract_intention(bg: BackgroundTasks):
    _check_no_running()
    job_id = _create_job("extract-intention")
    bg.add_task(_run_steps_job, job_id, ["extract_intention.py"])
    return {"job_id": job_id}


@app.post("/pipeline/classify")
def pipeline_classify(bg: BackgroundTasks):
    _check_no_running()
    job_id = _create_job("classify")
    bg.add_task(_run_steps_job, job_id, ["classify_tickets.py"])
    return {"job_id": job_id}


@app.post("/pipeline/tag")
def pipeline_tag(bg: BackgroundTasks):
    _check_no_running()
    job_id = _create_job("tag")
    bg.add_task(_run_steps_job, job_id, ["tag_tickets.py"])
    return {"job_id": job_id}


@app.post("/pipeline/run-all")
def pipeline_run_all(bg: BackgroundTasks):
    _check_no_running()
    job_id = _create_job("run-all")
    bg.add_task(_run_steps_job, job_id, PIPELINE_STEPS)
    return {"job_id": job_id}


# ---------------------------
# Prompts
# ---------------------------

@app.get("/prompts")
def list_prompts():
    """Lista todos los prompts con su versión más alta."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT prompt_name, MAX(version) as latest_version, MAX(created_at) as updated_at
                FROM public.ado_config_prompt
                GROUP BY prompt_name
                ORDER BY prompt_name
            """)
            rows = cur.fetchall()
    finally:
        conn.close()
    return [
        {"name": r[0], "latest_version": r[1], "updated_at": r[2].isoformat() if r[2] else None}
        for r in rows
    ]


@app.get("/prompts/{name}/versions")
def list_prompt_versions(name: str):
    """Lista todas las versiones de un prompt."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT version, created_at
                FROM public.ado_config_prompt
                WHERE prompt_name = %s
                ORDER BY version DESC
            """, (name,))
            rows = cur.fetchall()
    finally:
        conn.close()
    if not rows:
        raise HTTPException(status_code=404, detail=f"Prompt '{name}' no encontrado")
    return [{"version": r[0], "created_at": r[1].isoformat() if r[1] else None} for r in rows]


@app.get("/prompts/{name}/{version}")
def get_prompt_version(name: str, version: int):
    """Devuelve el texto de una versión concreta de un prompt."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT prompt_text, created_at
                FROM public.ado_config_prompt
                WHERE prompt_name = %s AND version = %s
            """, (name, version))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail=f"Prompt '{name}' v{version} no encontrado")
    return {"name": name, "version": version, "prompt_text": row[0], "created_at": row[1].isoformat() if row[1] else None}


@app.post("/prompts/{name}")
def create_prompt_version(name: str, body: dict):
    """Inserta una nueva versión del prompt. Devuelve la versión creada."""
    prompt_text = body.get("prompt_text", "").strip()
    if not prompt_text:
        raise HTTPException(status_code=400, detail="El texto del prompt no puede estar vacío")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO public.ado_config_prompt (prompt_name, prompt_text) VALUES (%s, %s) RETURNING version",
                (name, prompt_text)
            )
            new_version = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()
    return {"name": name, "version": new_version, "ok": True}


# ---------------------------
# Tickets
# ---------------------------

@app.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT i.id, i.work_item_type, i.title, i.state,
                       i.created_date, i.changed_date, i.area_path, i.iteration_path,
                       i.assigned_to, i.tags, i.description, i.repro_steps, i.acceptance_criteria
                FROM public.ado_work_items i
                WHERE i.id = %s AND i.created_date > '2026-04-30'
            """, (ticket_id,))
            row = cur.fetchone()
            cols = [d[0] for d in cur.description]
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail=f"Ticket {ticket_id} no encontrado")
    data = dict(zip(cols, row))
    for k in ("created_date", "changed_date"):
        if data.get(k):
            data[k] = data[k].isoformat()
    return data


@app.get("/tickets/{ticket_id}/duplicates")
def get_ticket_duplicates(ticket_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT
                    r.target_id, r.relation_type, r.similarity,
                    i2.id, i2.work_item_type, i2.title, i2.state,
                    i2.created_date, i2.changed_date, i2.area_path,
                    i2.iteration_path, i2.assigned_to, i2.tags,
                    i2.description, i2.repro_steps, i2.acceptance_criteria
                FROM public.ado_work_items i
                LEFT JOIN ado_work_item_embeddings e ON e.work_item_id = i.id
                LEFT JOIN ado_work_item_relations r ON r.source_id = i.id
                LEFT JOIN public.ado_work_items i2 ON i2.id = r.target_id
                WHERE i.id = %s
                  AND i.created_date > '2026-04-30'
                  AND e.work_item_id IS NOT NULL
                  AND r.source_id IS NOT NULL
                  AND i2.id IS NOT NULL
                ORDER BY r.similarity DESC NULLS LAST
            """, (ticket_id,))
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
    finally:
        conn.close()
    result = []
    for row in rows:
        d = dict(zip(cols, row))
        for k in ("created_date", "changed_date"):
            if d.get(k):
                d[k] = d[k].isoformat()
        result.append(d)
    return result


@app.get("/tickets/{ticket_id}/triage")
def get_ticket_triage(ticket_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT
                    ii.intention, ii.extracted_at,
                    ic.area, ic.justification, ic.model, ic.classified_at,
                    it.lista_tag, it.extracted_tag_at
                FROM public.ado_work_items i
                LEFT JOIN ado_work_item_intentions ii ON ii.work_item_id = i.id
                LEFT JOIN public.ado_work_item_classifications ic ON ic.work_item_id = i.id
                LEFT JOIN (
                    SELECT work_item_id,
                           string_agg(tag, '|') AS lista_tag,
                           max(extracted_tag_at) AS extracted_tag_at
                    FROM ado_work_item_tag
                    GROUP BY work_item_id
                ) it ON it.work_item_id = i.id
                WHERE i.id = %s
                  AND i.created_date > '2026-04-30'
                  AND ii.work_item_id IS NOT NULL
                  AND ic.work_item_id IS NOT NULL
            """, (ticket_id,))
            row = cur.fetchone()
            cols = [d[0] for d in cur.description]
    finally:
        conn.close()
    if not row:
        return {}
    d = dict(zip(cols, row))
    for k in ("extracted_at", "classified_at", "extracted_tag_at"):
        if d.get(k):
            d[k] = d[k].isoformat()
    if d.get("lista_tag"):
        d["tags"] = d["lista_tag"].split("|")
    return d


# ---------------------------
# Estadísticas
# ---------------------------

@app.get("/stats")
def stats():
    conn = get_connection()
    try:
        cur = conn.cursor()
        result = {}
        tables = {
            "tickets": "ado_work_items",
            "embeddings": "ado_work_item_embeddings",
            "intentions": "ado_work_item_intentions",
            "classifications": "ado_work_item_classifications",
            "tags": "ado_work_item_tag",
            "relations": "ado_work_item_relations",
        }
        for key, table in tables.items():
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            result[key] = cur.fetchone()[0]
        cur.close()
        return result
    finally:
        conn.close()
