#!/usr/bin/env python
"""Import an existing document library.

Resumable by construction: registration is idempotent on the content hash and
the queue is durable, so interrupting this and running it again picks up where
it left off rather than starting over. That matters because the first run over
a few thousand papers is measured in hours.

    uv run python ../scripts/backfill.py                    # the library root
    uv run python ../scripts/backfill.py ~/Papers --limit 20
    uv run python ../scripts/backfill.py --scan-only        # register, do not parse
    uv run python ../scripts/backfill.py --no-clean         # keep junk in place
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from sqlalchemy import select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import session_scope  # noqa: E402
from app.models import JobState, Paper  # noqa: E402
from app.services.ingest.cleanup import clean_library  # noqa: E402
from app.services.ingest.registrar import register_document  # noqa: E402
from app.workers.queue import pending_counts  # noqa: E402

logger = logging.getLogger("backfill")


def discover(root: Path) -> list[Path]:
    """Every ingestible document under root, by content rather than extension.

    Papers saved from arXiv often carry the bare identifier as a filename with
    no extension; globbing for *.pdf skips them silently.
    """
    from app.services.ingest.registrar import library_documents

    return library_documents(root)


def register_all(paths: list[Path]) -> dict[str, int]:
    tally: dict[str, int] = {}
    started = time.perf_counter()

    for index, path in enumerate(paths, start=1):
        try:
            with session_scope() as session:
                result = register_document(session, path)
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
        "--no-clean",
        action="store_true",
        help="do not quarantine broken files and duplicates before scanning",
    )
    parser.add_argument(
        "--purge",
        action="store_true",
        help="delete broken files and duplicates instead of quarantining them",
    )
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

    if not args.no_clean:
        # Before scanning, not after: a duplicate removed now is a paper that
        # never gets registered, hashed, parsed, and then explained away.
        with session_scope() as session:
            known = {
                str(Path(p).resolve()) for p in session.scalars(select(Paper.pdf_path))
            }
        findings, quarantine = clean_library(root, keep_paths=known, purge=args.purge)
        if findings:
            summary: dict[str, int] = {}
            for finding in findings:
                summary[finding.reason.value] = summary.get(finding.reason.value, 0) + 1
            verb = "purged" if args.purge else "quarantined"
            print(f"{verb} {len(findings)} file(s): {summary}")
            if quarantine:
                print(f"  -> {quarantine}")

    print(f"scanning {root}")
    paths = discover(root)
    if args.limit:
        paths = paths[: args.limit]
    if not paths:
        print("no documents found")
        return 0

    print(f"found {len(paths)} document(s)")
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
