"""SQLite-backed job queue.

This exists instead of Celery/RQ so the application has no background daemon to
install or keep running — a Redis dependency is the first thing that breaks
after a reboot on a personal machine.

The properties that matter are the ones a broker would normally provide:

* **Atomic claim.** ``UPDATE ... WHERE id = (SELECT ... LIMIT 1) RETURNING``
  is a single statement, so two workers can never take the same row.
* **Retry with a ceiling.** Failures requeue until ``max_attempts``, then land
  in ``dead`` rather than looping forever.
* **Crash recovery.** Rows stranded in ``running`` by a killed worker are
  requeued by ``requeue_stale`` on startup.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.types import to_storage, utcnow
from app.models import PRIORITY, Job, JobKind, JobState

# A job still 'running' after this long belongs to a worker that died.
STALE_AFTER_SECONDS = 30 * 60


def enqueue(
    session: Session,
    kind: JobKind | str,
    *,
    paper_id: int | None = None,
    priority: int | None = None,
    payload: dict[str, Any] | None = None,
    max_attempts: int = 3,
) -> Job:
    """Add a job, unless an equivalent one is already outstanding.

    Idempotency is deliberate: re-scanning the library or restarting the watcher
    must not pile up duplicate work for the same paper. A job that already
    finished does not block a new one — that is how re-parsing works.
    """
    kind = JobKind(kind)

    # Collapse onto an outstanding equivalent job. For per-paper work that
    # means the same paper; for corpus-wide work (projection) it means *any*
    # outstanding job of that kind, because a second full-corpus pass computes
    # exactly the same thing. Without this, a 500-paper backfill enqueues 500
    # projections and runs a full pass after every single embedding.
    conditions = [
        Job.kind == kind,
        Job.state.in_([JobState.QUEUED, JobState.RUNNING]),
    ]
    if paper_id is not None:
        conditions.append(Job.paper_id == paper_id)
    else:
        conditions.append(Job.paper_id.is_(None))

    existing = session.scalar(select(Job).where(*conditions))
    if existing is not None:
        return existing

    job = Job(
        kind=kind,
        paper_id=paper_id,
        priority=priority if priority is not None else PRIORITY[kind],
        state=JobState.QUEUED,
        max_attempts=max_attempts,
        payload_json=json.dumps(payload) if payload else None,
    )
    session.add(job)
    session.flush()
    return job


def claim_next(
    session: Session,
    kinds: Sequence[JobKind | str] | None = None,
) -> Job | None:
    """Atomically take the next queued job, or return None if there is none.

    ``kinds`` lets the worker drain one stage at a time. That is not cosmetic:
    with 8 GB of VRAM only one model is resident at a time, so interleaving
    kinds would reload models every few seconds.
    """
    where_kind = ""
    params: dict[str, Any] = {"now": to_storage(utcnow())}

    if kinds:
        names = [JobKind(k).value for k in kinds]
        placeholders = ", ".join(f":k{i}" for i in range(len(names)))
        where_kind = f"AND kind IN ({placeholders})"
        params.update({f"k{i}": n for i, n in enumerate(names)})

    # One statement, so the select-and-take cannot be interleaved by another
    # worker. SQLite serialises writers, which makes this safe by construction.
    stmt = text(
        f"""
        UPDATE jobs
           SET state = 'running',
               started_at = :now,
               attempts = attempts + 1
         WHERE id = (
               SELECT id FROM jobs
                WHERE state = 'queued' {where_kind}
             ORDER BY priority ASC, id ASC
                LIMIT 1
         )
     RETURNING id
        """
    )
    row = session.execute(stmt, params).first()
    if row is None:
        return None

    session.commit()
    return session.get(Job, row[0])


def complete(session: Session, job: Job) -> Job:
    job.state = JobState.DONE
    job.finished_at = utcnow()
    job.last_error = None
    session.add(job)
    return job


def fail(session: Session, job: Job, error: str, *, fatal: bool = False) -> Job:
    """Record a failure: requeue if attempts remain, otherwise mark it dead.

    ``fatal`` skips the remaining attempts. Retrying is worth it for a model
    server that was restarting; for a DRM-locked book the second and third
    attempts re-render the same pages to reach the same conclusion, and because
    a requeued job is re-claimed ahead of the ones behind it, that delay is
    paid by its neighbours too. See ``services.parse.errors``.
    """
    job.last_error = error[:4000]
    if fatal or job.attempts >= job.max_attempts:
        job.state = JobState.DEAD
        job.finished_at = utcnow()
    else:
        job.state = JobState.QUEUED
        job.started_at = None
    session.add(job)
    return job


def requeue_stale(
    session: Session, older_than_seconds: int = STALE_AFTER_SECONDS
) -> int:
    """Return jobs abandoned by a dead worker to the queue.

    Called on worker startup. Without it, a ``kill -9`` mid-batch strands those
    rows in ``running`` and the papers never finish.
    """
    cutoff = utcnow() - timedelta(seconds=older_than_seconds)
    stale = session.scalars(
        select(Job).where(
            Job.state == JobState.RUNNING,
            Job.started_at.isnot(None),
            Job.started_at <= cutoff,
        )
    ).all()

    for job in stale:
        # Attempts already counted at claim time, so a job that reliably kills
        # the worker still exhausts its retries rather than looping forever.
        if job.attempts >= job.max_attempts:
            job.state = JobState.DEAD
            job.last_error = "worker died and retries were exhausted"
            job.finished_at = utcnow()
        else:
            job.state = JobState.QUEUED
            job.started_at = None
            job.last_error = "requeued after worker died"
        session.add(job)

    return len(stale)


def pending_counts(session: Session) -> dict[tuple[str, str], int]:
    """(kind, state) -> count. Backs the jobs drawer and the health check."""
    rows = session.execute(
        select(Job.kind, Job.state, func.count()).group_by(Job.kind, Job.state)
    ).all()
    return {(kind, state): count for kind, state, count in rows}


def payload_of(job: Job) -> dict[str, Any]:
    return json.loads(job.payload_json) if job.payload_json else {}
