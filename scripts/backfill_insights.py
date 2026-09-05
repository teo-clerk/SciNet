#!/usr/bin/env python
"""Queue the plain-English reading for the works already in the library.

New works get an INSIGHT job automatically after embedding; this backfills
the ones ingested before the stage existed, or re-reads them after a change
of model. The worker does the work — this script only queues it, so it is
safe beside a running pipeline. Budget about seven seconds a work on an 8B
model: a five-hundred-work library is an hour of the LLM era, once.

    uv run python ../scripts/backfill_insights.py             # report only
    uv run python ../scripts/backfill_insights.py --apply     # queue the jobs
    uv run python ../scripts/backfill_insights.py --refresh   # also re-read works
                                                              # written by another model
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import session_scope  # noqa: E402
from app.models import JobKind, MarkdownDoc, PaperInsight  # noqa: E402
from app.workers.queue import enqueue  # noqa: E402

SECONDS_PER_WORK = 7


def works_needing_a_reading(
    session: Session, *, refresh_model: str | None = None
) -> list[int]:
    """Parsed works without a reading, plus — with ``refresh_model`` — the
    ones whose reading was written by a different model than the one named."""
    parsed = list(session.scalars(select(MarkdownDoc.paper_id)))
    readings = {
        row.paper_id: row.model_id
        for row in session.scalars(select(PaperInsight)).all()
    }
    return [
        pid
        for pid in parsed
        if pid not in readings
        or (refresh_model is not None and readings[pid] != refresh_model)
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="queue jobs; default reports only"
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="also re-read works whose reading came from a different model",
    )
    args = parser.parse_args()
    settings = get_settings()

    with session_scope() as session:
        pending = works_needing_a_reading(
            session, refresh_model=settings.llm_model if args.refresh else None
        )
        minutes = round(len(pending) * SECONDS_PER_WORK / 60)
        print(
            f"{len(pending)} work(s) need a plain-English reading "
            f"(about {minutes} min of LLM time with {settings.llm_model})"
        )

        if not args.apply:
            print("\nwould queue one INSIGHT job per work. Re-run with --apply.")
            return 0

        for paper_id in pending:
            enqueue(session, JobKind.INSIGHT, paper_id=paper_id)
        print(f"queued {len(pending)} insight job(s); the worker takes it from here")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
