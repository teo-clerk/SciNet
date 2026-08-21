#!/usr/bin/env python
"""Take non-papers and duplicate files out of the library.

Finds three things and removes them: files that claim to be documents but are
not (HTML paywall pages saved as ``.pdf``, truncated downloads), files too
small to hold a paper, and exact byte-identical copies of a paper already
present.

Files are **moved to ``data/quarantine/<timestamp>/``** with a manifest saying
why, not deleted — these are heuristics running against the operator's own
library, and a false positive on a real paper is unrecoverable. Pass
``--purge`` to delete outright once you trust it.

    uv run python ../scripts/clean_library.py --dry-run   # report only
    uv run python ../scripts/clean_library.py             # quarantine
    uv run python ../scripts/clean_library.py --purge     # delete
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from sqlalchemy import select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import session_scope  # noqa: E402
from app.models import Paper  # noqa: E402
from app.services.ingest.cleanup import Reason, clean_library  # noqa: E402


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(message)s")
    settings = get_settings()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "root",
        nargs="?",
        type=Path,
        default=settings.library_dir,
        help="directory to clean (default: the configured library)",
    )
    parser.add_argument("--dry-run", action="store_true", help="report, change nothing")
    parser.add_argument(
        "--purge",
        action="store_true",
        help="delete permanently instead of moving to quarantine",
    )
    args = parser.parse_args()

    root = args.root.expanduser().resolve()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2

    with session_scope() as session:
        known = {
            str(Path(p).resolve()) for p in session.scalars(select(Paper.pdf_path))
        }

    findings, quarantine = clean_library(
        root, keep_paths=known, purge=args.purge, dry_run=args.dry_run
    )

    if not findings:
        print(f"{root}: nothing to clean")
        return 0

    for reason in Reason:
        group = [f for f in findings if f.reason is reason]
        if not group:
            continue
        print(f"\n{reason.value} ({len(group)}):")
        for finding in group:
            print(f"  {finding.path.relative_to(root)}")
            print(f"      {finding.detail}")

    print()
    if args.dry_run:
        print(f"{len(findings)} file(s) would be removed; nothing was changed")
    elif args.purge:
        print(f"deleted {len(findings)} file(s)")
    else:
        print(f"moved {len(findings)} file(s) to {quarantine}")
        print("Review them there; the directory can be deleted once you are happy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
