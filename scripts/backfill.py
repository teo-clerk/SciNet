#!/usr/bin/env python
"""Import an existing PDF library.

Resumable by construction: registration is idempotent on the content hash and
the queue is durable, so interrupting this and running it again picks up where
it left off rather than starting over. That matters because the first run over
a few thousand papers is measured in hours.

    uv run python ../scripts/backfill.py                    # the library root
    uv run python ../scripts/backfill.py ~/Papers --limit 20
    uv run python ../scripts/backfill.py --scan-only        # register, do not parse
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings  # noqa: E402
from app.core.db import session_scope  # noqa: E402
from app.models import JobState, Paper  # noqa: E402
from app.services.ingest.registrar import register_pdf  # noqa: E402
from app.workers.queue import pending_counts  # noqa: E402

logger = logging.getLogger("backfill")


def discover(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.pdf") if p.is_file())


def register_all(paths: list[Path]) -> dict[str, int]:
    tally: dict[str, int] = {}
    started = time.perf_counter()

    for index, path in enumerate(paths, start=1):
        try:
            with session_scope() as session:
                result = register_pdf(session, path)
            outcome = result.outcome.value
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the run
            logger.warning("skipping %s: %s", path.name, exc)
            outcome = "error"

        tally[outcome] = tally.get(outcome, 0) + 1

        if index % 25 == 0 or index == len(paths):
            rate = index / max(time.perf_counter() - started, 1e-6)
            print(
                f"  {index}/{len(paths)} registered  ({rate:.1f}/s)  {tally}",
                flush=True,
            )
    return tally


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    settings = get_settings()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "root",
        nargs="?",
        type=Path,
        default=settings.library_dir,
        help="directory to scan (default: the configured library)",
    )
    parser.add_argument("--limit", type=int, help="stop after this many files")
    parser.add_argument(
        "--scan-only",
        action="store_true",
        help="register files without enqueuing parse jobs",
    )
    args = parser.parse_args()

    root = args.root.expanduser().resolve()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2

    settings.ensure_dirs()
    print(f"scanning {root}")
    paths = discover(root)
    if args.limit:
        paths = paths[: args.limit]
    if not paths:
        print("no PDFs found")
        return 0

    print(f"found {len(paths)} PDF(s)")
    with session_scope() as session:
        already = session.query(Paper).count()

    tally = register_all(paths)

    with session_scope() as session:
        total = session.query(Paper).count()
        counts = pending_counts(session)

    queued = sum(n for (_, state), n in counts.items() if state == JobState.QUEUED)

    print()
    print(f"papers: {already} -> {total}")
    print(f"outcomes: {tally}")
    print(f"jobs queued: {queued}")
    if not args.scan_only and queued:
        print()
        print("Start the worker to process them:")
        print("  cd backend && uv run python -m app.workers.runner")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
