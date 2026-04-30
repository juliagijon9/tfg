from datetime import datetime, timezone

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.db import get_connection
from backend.jobs import create_job, update_job, get_job
from backend.services.sync_service import run_sync
from backend.services.embedding_service import run_generate_embeddings
from backend.services.similarity_service import find_top_k, save_relations
from backend.services.metrics_service import get_metrics

app = FastAPI(title="TFG Backend API")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Permite todos los orígenes (ajusta para producción)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----- Schemas -----

class FindSimilarRequest(BaseModel):
    source_id: int
    top_k: int = 10


class RelationItem(BaseModel):
    target_id: int
    relation_type: str
    similarity: float


class SaveRelationsRequest(BaseModel):
    source_id: int
    relations: list[RelationItem]


# ----- Health -----

@app.get("/health")
def health():
    try:
        conn = get_connection()
        conn.close()
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok", "db_connected": db_ok}


@app.get("/work-items/count")
def work_items_count():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM ado_work_items")
        count = cur.fetchone()[0]
        cur.close()
        return {"count": count}
    finally:
        conn.close()


# ----- Background job helpers -----

def _run_sync_job(job_id: str):
    update_job(job_id, status="running")
    try:
        result = run_sync()
        update_job(
            job_id,
            status="completed",
            result=result,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as e:
        update_job(
            job_id,
            status="failed",
            error=str(e),
            finished_at=datetime.now(timezone.utc).isoformat(),
        )


def _run_embeddings_job(job_id: str):
    update_job(job_id, status="running")
    try:
        result = run_generate_embeddings()
        update_job(
            job_id,
            status="completed",
            result=result,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as e:
        update_job(
            job_id,
            status="failed",
            error=str(e),
            finished_at=datetime.now(timezone.utc).isoformat(),
        )


# ----- Jobs -----

@app.post("/jobs/sync")
def start_sync(bg: BackgroundTasks):
    job_id = create_job("sync")
    bg.add_task(_run_sync_job, job_id)
    return {"job_id": job_id}


@app.post("/jobs/embeddings")
def start_embeddings(bg: BackgroundTasks):
    job_id = create_job("embeddings")
    bg.add_task(_run_embeddings_job, job_id)
    return {"job_id": job_id}


@app.get("/jobs/{job_id}")
def job_status(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# ----- Similarity -----

@app.post("/similarity/find")
def similarity_find(req: FindSimilarRequest):
    results = find_top_k(req.source_id, req.top_k)
    if results is None:
        raise HTTPException(
            status_code=404,
            detail=f"No embedding found for work_item_id={req.source_id}",
        )
    return {"source_id": req.source_id, "results": results}


# ----- Relations -----

@app.post("/relations/save")
def relations_save(req: SaveRelationsRequest):
    rels = [r.model_dump() for r in req.relations]
    result = save_relations(req.source_id, rels)
    return result


# ----- Metrics -----

@app.get("/metrics")
def metrics():
    return get_metrics()