import uuid
from datetime import datetime, timezone
from pydantic import BaseModel


class JobStatus(BaseModel):
    job_id: str
    type: str
    status: str  # pending | running | completed | failed
    result: dict | None = None
    error: str | None = None
    started_at: str
    finished_at: str | None = None


_jobs: dict[str, JobStatus] = {}


def create_job(job_type: str) -> str:
    job_id = str(uuid.uuid4())
    _jobs[job_id] = JobStatus(
        job_id=job_id,
        type=job_type,
        status="pending",
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    return job_id


def update_job(job_id: str, **kwargs):
    if job_id not in _jobs:
        return
    job = _jobs[job_id]
    for k, v in kwargs.items():
        setattr(job, k, v)


def get_job(job_id: str) -> JobStatus | None:
    return _jobs.get(job_id)
