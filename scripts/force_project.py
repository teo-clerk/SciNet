#!/usr/bin/env python
"""Rebuild the map from scratch, and re-tag what needs it.

The projection normally refits itself when the stored fit stops representing
the corpus. This is for when you want it now, or want it regardless: after
changing the embedding model, after tuning the clustering, or after a large
import that finished in a state the automatic trigger did not catch.

A forced refit is not a cheap operation and it is not a destructive one. It
computes a whole new layout into an inactive ``projection_runs`` row, aligns it
onto the current one so the map settles rather than scrambling, and swaps
``is_active`` in a single statement — so a reader sees the old map or the new
one, never a half-written mixture, and a refit that dies leaves the previous
map exactly as it was.

    uv run python ../scripts/force_project.py            # what it would do
    uv run python ../scripts/force_project.py --apply    # enqueue it
    uv run python ../scripts/force_project.py --apply --run

``--apply`` only enqueues, which is safe while the worker is running — the API
enqueues jobs the same way. ``--run`` drains them in this process instead, and
refuses to start if a worker is alive: two writers is the one thing the SQLite
queue is not built for.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import func, select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import session_scope  # noqa: E402
from app.models import (  # noqa: E402
    DocVector,
    Job,
    JobKind,
    JobState,
    Paper,
    PaperMeta,
    PaperStatus,
)
from app.workers.queue import enqueue  # noqa: E402


def _worker_pids() -> list[int]:
    """Any running worker, found by what it is executing.

    Matched on the binary rather than on the command line, for the reason
    ``app/cli/stop.py`` explains at length: a pattern that matches the worker
    also matches the shell that went looking for it.
    """
    from app.cli.stop import _own_lineage, _process_table, select_workers

    return sorted(select_workers(_process_table(), _own_lineage()))


def _summarise(session) -> tuple[int, int]:
    """How many documents are embedded, and how many still have no summary."""
    embedded = session.scalar(select(func.count()).select_from(DocVector)) or 0
    untagged = (
        session.scalar(
            select(func.count())
            .select_from(Paper)
            .join(DocVector, DocVector.paper_id == Paper.id)
            .outerjoin(PaperMeta, PaperMeta.paper_id == Paper.id)
            .where(PaperMeta.summary.is_(None))
        )
        or 0
    )
    return embedded, untagged


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="enqueue the jobs")
    parser.add_argument(
        "--run",
        action="store_true",
        help="drain the jobs now instead of leaving them for the worker",
    )
    parser.add_argument(
        "--no-tag",
        action="store_true",
        help="rebuild the map only; leave summaries and tags alone",
    )
    args = parser.parse_args()
    settings = get_settings()

    if args.run and (running := _worker_pids()):
        print(f"a worker is already running (pid {', '.join(map(str, running))}).")
        print("It will pick these jobs up by itself — drop --run, or stop it first:")
        print("  cd backend && uv run scinet-stop")
        return 2

    with session_scope() as session:
        embedded, untagged = _summarise(session)
        if embedded == 0:
            print("no document vectors yet — run the worker's embed stage first")
            return 1

        print(f"{embedded} embedded document(s)")
        print("  project : full refit (new layout, clusters, names, bridges)")
        if args.no_tag:
            print("  tag     : skipped (--no-tag)")
        else:
            print(f"  tag     : {untagged} paper(s) with no summary yet")
        # Enrichment is opt-in and reaches the network; it is never enqueued
        # here on the operator's behalf. Turning it on is a privacy decision,
        # and the metadata stage queues it by itself once it is.
        state = "on" if settings.enrichment_enabled else "off"
        print(f"  enrich  : {state} (SCINET_ENRICHMENT_ENABLED)")

        if not args.apply:
            print("\ndry run — pass --apply to enqueue")
            return 0

        enqueue(session, JobKind.PROJECT, paper_id=None, payload={"force_refit": True})
        if not args.no_tag:
            rows = session.scalars(
                select(Paper.id)
                .join(DocVector, DocVector.paper_id == Paper.id)
                .outerjoin(PaperMeta, PaperMeta.paper_id == Paper.id)
                .where(PaperMeta.summary.is_(None))
            ).all()
            for paper_id in rows:
                enqueue(session, JobKind.TAG, paper_id=paper_id)
        session.commit()

    print("\nenqueued.")
    if not args.run:
        print("Start the worker to run it:")
        print("  cd backend && uv run python -m app.workers.runner")
        return 0

    return _drain(settings, tag=not args.no_tag)


def _drain(settings, *, tag: bool) -> int:
    """Run the enqueued stages here. The worker must not be running."""
    from app.workers.runner import Worker

    worker = Worker(settings)
    stages = [JobKind.PROJECT] + ([JobKind.TAG] if tag else [])
    for kind in stages:
        print(f"\n--- {kind.value} ---")
        worker.drain_stage(kind)

    with session_scope() as session:
        dead = session.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.state == JobState.DEAD, Job.kind.in_(stages))
        )
        ready = session.scalar(
            select(func.count())
            .select_from(Paper)
            .where(Paper.status == PaperStatus.READY)
        )
    print(f"\n{ready} paper(s) ready; {dead or 0} job(s) dead")
    return 1 if dead else 0


if __name__ == "__main__":
    raise SystemExit(main())
