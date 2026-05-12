"""API FastAPI del asistente de triaje de tickets de Iberia Express."""

import subprocess
import sys
from datetime import datetime, timezone

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from backend.db import get_connection
from backend.jobs import create_job, update_job, get_job

app = FastAPI(title="TFG Triage API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ruta donde se montan los scripts en el contenedor
SCRIPTS_DIR = "/scripts"

# Orden y nombres de los pasos del pipeline
PIPELINE_STEPS = [
    "sync_ado_to_postgres.py",
    "generate_embeddings.py",
    "link_related.py",
    "extract_intention.py",
    "classify_tickets.py",
    "tag_tickets.py",
]


# ---------------------------
# Helpers internos
# ---------------------------

def _run_script(script_name: str) -> tuple[bool, str]:
    """Ejecuta un script Python y devuelve (éxito, salida)."""
    path = f"{SCRIPTS_DIR}/{script_name}"
    result = subprocess.run(
        [sys.executable, path],
        capture_output=True,
        text=True,
    )
    output = result.stdout
    if result.stderr:
        output += f"\n[STDERR]\n{result.stderr}"
    return result.returncode == 0, output


def _run_steps_job(job_id: str, steps: list[str]) -> None:
    """Ejecuta una lista de pasos secuencialmente y actualiza el job."""
    update_job(job_id, status="running")
    steps_output = []

    for step in steps:
        success, output = _run_script(step)
        steps_output.append({"step": step, "output": output, "ok": success})

        if not success:
            update_job(
                job_id,
                status="failed",
                error=f"Falló el paso: {step}",
                result={"steps": steps_output},
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            return

    update_job(
        job_id,
        status="completed",
        result={"steps": steps_output},
        finished_at=datetime.now(timezone.utc).isoformat(),
    )


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

@app.get("/jobs/{job_id}")
def job_status(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job no encontrado")
    return job


# ---------------------------
# Pipeline — pasos individuales
# ---------------------------

@app.post("/pipeline/sync")
def pipeline_sync(bg: BackgroundTasks):
    """Sincroniza tickets de Azure DevOps → PostgreSQL."""
    job_id = create_job("sync")
    bg.add_task(_run_steps_job, job_id, ["sync_ado_to_postgres.py"])
    return {"job_id": job_id}


@app.post("/pipeline/embeddings")
def pipeline_embeddings(bg: BackgroundTasks):
    """Genera embeddings para los tickets sin embedding."""
    job_id = create_job("embeddings")
    bg.add_task(_run_steps_job, job_id, ["generate_embeddings.py"])
    return {"job_id": job_id}


@app.post("/pipeline/link-related")
def pipeline_link_related(bg: BackgroundTasks):
    """Detecta tickets duplicados y relacionados por similitud."""
    job_id = create_job("link-related")
    bg.add_task(_run_steps_job, job_id, ["link_related.py"])
    return {"job_id": job_id}


@app.post("/pipeline/extract-intention")
def pipeline_extract_intention(bg: BackgroundTasks):
    """Extrae la intención de los tickets sin intención."""
    job_id = create_job("extract-intention")
    bg.add_task(_run_steps_job, job_id, ["extract_intention.py"])
    return {"job_id": job_id}


@app.post("/pipeline/classify")
def pipeline_classify(bg: BackgroundTasks):
    """Clasifica los tickets con intención extraída."""
    job_id = create_job("classify")
    bg.add_task(_run_steps_job, job_id, ["classify_tickets.py"])
    return {"job_id": job_id}


@app.post("/pipeline/tag")
def pipeline_tag(bg: BackgroundTasks):
    """Asigna tags a los tickets clasificados."""
    job_id = create_job("tag")
    bg.add_task(_run_steps_job, job_id, ["tag_tickets.py"])
    return {"job_id": job_id}


# ---------------------------
# Pipeline — ejecución completa
# ---------------------------

@app.post("/pipeline/run-all")
def pipeline_run_all(bg: BackgroundTasks):
    """Ejecuta el pipeline completo de triaje en orden."""
    job_id = create_job("run-all")
    bg.add_task(_run_steps_job, job_id, PIPELINE_STEPS)
    return {"job_id": job_id}


# ---------------------------
# Estadísticas básicas
# ---------------------------

@app.get("/stats")
def stats():
    """Devuelve contadores de las tablas principales."""
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
