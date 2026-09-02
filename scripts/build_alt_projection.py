#!/usr/bin/env python
"""Project the library under a second embedding model, for the morph view.

The active map never moves: the alternate projection is encoded, fitted, and
Procrustes-aligned onto the current layout, then stored as an *inactive*
run. In the Model Lab's morph slider, the nodes that travel farthest are the
papers the two embedders disagree about.

    # report only:
    uv run python ../scripts/build_alt_projection.py allenai/specter2_base
    # build it:
    uv run python ../scripts/build_alt_projection.py allenai/specter2_base --apply

Encoding runs on the CPU by default so a busy worker keeps its card; expect
roughly a second per paper. The model must already be provisioned
(scripts/download_models.py) — this script never downloads.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from sqlalchemy import select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import session_scope  # noqa: E402
from app.models import Projection, ProjectionRun  # noqa: E402
from app.services.project.alt_run import build_alt_run  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", help="embedding model reference (HF repo id)")
    parser.add_argument(
        "--apply", action="store_true", help="actually build; default reports only"
    )
    args = parser.parse_args()

    settings = get_settings()
    with session_scope() as session:
        active = session.scalar(
            select(ProjectionRun).where(ProjectionRun.is_active.is_(True))
        )
        if active is None:
            print("no active projection — build the map first", file=sys.stderr)
            return 1
        rows = session.scalar(
            select(Projection.paper_id).where(Projection.run_id == active.id).limit(1)
        )
        count = session.query(Projection).filter(Projection.run_id == active.id).count()
        print(f"active run {active.id} ({active.model_id}): {count} papers")
        print(f"alternate model: {args.model}")

        if not args.apply:
            print(
                f"\nwould encode {count} documents on the CPU (~{count}s), fit, "
                "align, and store an inactive run.\nRe-run with --apply."
            )
            _ = rows
            return 0

        started = time.perf_counter()
        result = build_alt_run(session, settings, args.model)
        elapsed = time.perf_counter() - started

    print(
        f"\nrun {result.run_id}: {result.rows} papers under {result.model_id} "
        f"({result.dim}-dim) in {elapsed:.0f}s"
    )
    print(
        f"mean node displacement vs the active layout: {result.mean_displacement:.2f}"
    )
    print("open the Model Lab and pick the run in the morph slider")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
