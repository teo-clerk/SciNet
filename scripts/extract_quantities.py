#!/usr/bin/env python
"""Queue quantity extraction for the papers already in the library.

New papers get an EXTRACT job automatically after embedding; this backfills
the ones ingested before the stage existed. The worker does the work — this
script only queues it, so it is safe beside a running pipeline.

    uv run python ../scripts/extract_quantities.py            # report only
    uv run python ../scripts/extract_quantities.py --apply    # queue the jobs
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from sqlalchemy import func, select  # noqa: E402

from app.core.db import session_scope  # noqa: E402
from app.models import Chunk, JobKind, Quantity  # noqa: E402
from app.workers.queue import enqueue  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="queue jobs; default reports only"
    )
    args = parser.parse_args()

    with session_scope() as session:
        with_chunks = session.scalars(
            select(Chunk.paper_id).group_by(Chunk.paper_id)
        ).all()
        existing = session.scalar(select(func.count(Quantity.id))) or 0
        print(f"{len(with_chunks)} papers hold chunks; {existing} quantity rows exist")

        if not args.apply:
            print("\nwould queue one EXTRACT job per paper. Re-run with --apply.")
            return 0

        for paper_id in with_chunks:
            enqueue(session, JobKind.EXTRACT, paper_id=paper_id)
        print(
            f"queued {len(with_chunks)} extract job(s); the worker takes it from here"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
