#!/usr/bin/env python
"""Put back the jobs that died of something since fixed.

``dead`` means the queue gave up: three attempts, no success. That is the right
answer for a document nothing can read, and the wrong one for a stage that
failed because the machine was missing a library — every attempt hit the same
ImportError, and once the dependency is installed those jobs are perfectly
runnable. Nothing retries them, because being out of retries is exactly what
``dead`` records.

This is not a general "retry everything" button. It revives only jobs whose
recorded error matches a pattern known to be environmental, so a DRM-locked
book stays dead where it belongs. The check runs against the live environment
first: reviving 449 jobs into an interpreter that still cannot import the
module would spend three more attempts each to reach the same place.

    uv run python ../scripts/revive_jobs.py            # show what would return
    uv run python ../scripts/revive_jobs.py --apply    # requeue them

The worker must be stopped: it is the sole writer of paper data.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import select  # noqa: E402

from app.core.db import session_scope  # noqa: E402
from app.models import Job, JobState, Paper, PaperStatus  # noqa: E402


@dataclass(frozen=True)
class Environmental:
    """A failure caused by the machine rather than by the document."""

    #: Substring of the recorded error. Matched rather than parsed: the error
    #: is stored as text and the exception is long gone.
    marker: str
    #: What has to be importable before reviving is worth doing.
    module: str | None
    explanation: str


CAUSES = (
    Environmental(
        marker="No module named 'sentence_transformers'",
        module="sentence_transformers",
        explanation="the embedding model's library was not installed",
    ),
    Environmental(
        marker="No module named 'torch'",
        module="torch",
        explanation="torch was not installed",
    ),
    Environmental(
        marker="No module named 'marker'",
        module="marker",
        explanation="tier 1 was not installed",
    ),
)


def _cause_of(error: str | None) -> Environmental | None:
    if not error:
        return None
    return next((c for c in CAUSES if c.marker in error), None)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="requeue them (default: dry run)"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="revive even if the missing module still cannot be imported",
    )
    args = parser.parse_args()

    revived: dict[str, int] = {}
    blocked: dict[str, int] = {}

    with session_scope() as session:
        dead = session.scalars(
            select(Job).where(Job.state == JobState.DEAD).order_by(Job.id)
        ).all()

        for job in dead:
            cause = _cause_of(job.last_error)
            if cause is None:
                continue

            usable = cause.module is None or (
                importlib.util.find_spec(cause.module) is not None
            )
            if not usable and not args.force:
                blocked[cause.module or "?"] = blocked.get(cause.module or "?", 0) + 1
                continue

            key = f"{job.kind}: {cause.explanation}"
            revived[key] = revived.get(key, 0) + 1

            if args.apply:
                job.state = JobState.QUEUED
                job.attempts = 0
                job.started_at = None
                job.finished_at = None
                job.last_error = None
                session.add(job)
                _unfail_paper(session, job.paper_id)

        if not args.apply:
            session.rollback()

    for module, count in sorted(blocked.items()):
        print(
            f"skipped {count} job(s): {module} still cannot be imported. "
            "Install it, or pass --force."
        )
    if not revived:
        print("nothing to revive")
        return 0

    for key, count in sorted(revived.items()):
        print(f"  {count:5d}  {key}")
    total = sum(revived.values())
    if args.apply:
        print(f"\nrequeued {total} job(s). Start the worker:")
        print("  cd backend && uv run python -m app.workers.runner")
    else:
        print(f"\n{total} job(s) would be requeued — dry run. Pass --apply.")
    return 0


def _unfail_paper(session, paper_id: int | None) -> None:
    """Give the paper its status back, but never resurrect a quarantined file.

    A quarantined paper's file has been moved out of the library; putting the
    row back to `parsed` would point it at a path that is no longer there.
    """
    if paper_id is None:
        return
    paper = session.get(Paper, paper_id)
    if paper is None or paper.status == PaperStatus.QUARANTINED:
        return
    if paper.status == PaperStatus.FAILED:
        paper.status = PaperStatus.PARSED
        paper.last_error = None
        session.add(paper)


if __name__ == "__main__":
    raise SystemExit(main())
