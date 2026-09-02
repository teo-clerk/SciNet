"""Pipeline status: what is queued, running, and stuck."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import Job, JobState
from app.schemas.paper import JobCounts
from app.workers import lease
from app.workers.queue import pending_counts

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("", response_model=JobCounts)
def job_counts(db: Session = Depends(get_db)) -> JobCounts:
    grouped = pending_counts(db)
    flat = {f"{kind}:{state}": n for (kind, state), n in grouped.items()}

    def total(state: JobState) -> int:
        return sum(n for (_, s), n in grouped.items() if s == state)

    return JobCounts(
        counts=flat,
        queued=total(JobState.QUEUED),
        running=total(JobState.RUNNING),
        failed=total(JobState.FAILED),
        dead=total(JobState.DEAD),
    )


@router.get("/dead")
def dead_jobs(db: Session = Depends(get_db), limit: int = 50) -> list[dict]:
    """Jobs that exhausted their retries — the queue's error report."""
    rows = db.scalars(
        select(Job)
        .where(Job.state == JobState.DEAD)
        .order_by(Job.finished_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": j.id,
            "kind": j.kind,
            "paper_id": j.paper_id,
            "attempts": j.attempts,
            "last_error": j.last_error,
        }
        for j in rows
    ]


@router.post("/dead/retry")
def retry_dead(db: Session = Depends(get_db)) -> dict[str, int]:
    """Put every dead job back in the queue with a fresh attempt budget."""
    rows = db.scalars(select(Job).where(Job.state == JobState.DEAD)).all()
    for job in rows:
        job.state = JobState.QUEUED
        job.attempts = 0
        job.started_at = None
        job.finished_at = None
        db.add(job)
    db.commit()
    return {"requeued": len(rows)}


@router.post("/pause")
def pause_worker(db: Session = Depends(get_db)) -> dict:
    """Ask the worker to stop claiming at its next job boundary.

    One mechanism for two callers: the librarian takes short self-refreshing
    leases; this endpoint takes a long one, because a human pressed a button
    and a human will unpress it. The API writing a settings row is
    precedented — it already writes the jobs table (reproject).
    """
    held = lease.take(
        db, ttl_seconds=lease.USER_TTL_SECONDS, reason="user", keep_warm=None
    )
    db.commit()
    return {"paused_until": held.until.isoformat(), "reason": held.reason}


@router.delete("/pause")
def resume_worker(db: Session = Depends(get_db)) -> dict:
    """The resume button: end whatever lease exists, expressly."""
    lease.release_any(db)
    db.commit()
    return {"paused": False}


@router.get("/pause")
def pause_state(db: Session = Depends(get_db)) -> dict:
    held = lease.active(db)
    if held is None:
        return {"paused": False}
    return {
        "paused": True,
        "reason": held.reason,
        "until": held.until.isoformat(),
        "acked": lease.acked(db, held),
    }
