"""Re-derive stored titles through the real extraction path.

Titles are heuristic, and improving the heuristic leaves the corpus behind:
rows keep whatever the rules said on the day they were parsed. This re-runs
``extract_from_document`` — the same entry point the pipeline uses — over papers
that already have Markdown, and writes back only the title.

It goes through ``extract_from_document`` on purpose. An earlier version of this
script called ``guess_title`` directly, skipping the embedded-metadata
precedence that runs ahead of it, and overwrote 29 correct titles with page
furniture. Anything that reimplements the precedence will drift from it.

Dry run by default; pass ``--apply`` to write. The worker must be stopped:
it is the sole writer of paper data.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import text  # noqa: E402

from app.core.db import session_scope  # noqa: E402
from app.services.metadata.extract import extract_from_document  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="write the changes (default: dry run)"
    )
    args = parser.parse_args()

    with session_scope() as session:
        rows = session.execute(
            text(
                """
                SELECT p.id, p.pdf_path, m.md_path, pm.title
                FROM papers p
                LEFT JOIN markdown_docs m ON m.paper_id = p.id
                LEFT JOIN paper_meta pm ON pm.paper_id = p.id
                ORDER BY p.id
                """
            )
        ).all()

        changes: list[tuple[int, str | None, str]] = []
        failures: list[tuple[int, str]] = []

        for paper_id, pdf_path, md_path, stored in rows:
            parsed = None
            if md_path and Path(md_path).exists():
                parsed = Path(md_path).read_text(errors="replace")
            try:
                fresh = extract_from_document(pdf_path, parsed_text=parsed).title
            except Exception as exc:  # noqa: BLE001 - one bad PDF must not stop the run
                failures.append((paper_id, str(exc)))
                continue
            # A heuristic that now finds nothing is not a reason to erase a
            # title that is already on the map.
            if fresh and fresh != stored:
                changes.append((paper_id, stored, fresh))

        for paper_id, old, new in changes:
            print(f"#{paper_id}\n  - {old!r}\n  + {new!r}")
        for paper_id, error in failures:
            print(f"#{paper_id}: could not read - {error}", file=sys.stderr)

        print(
            f"\n{len(rows)} papers, {len(changes)} titles differ, "
            f"{len(failures)} unreadable"
        )

        if not args.apply:
            print("dry run; pass --apply to write")
            return 0

        for paper_id, _old, new in changes:
            session.execute(
                text("UPDATE paper_meta SET title = :t WHERE paper_id = :i"),
                {"t": new, "i": paper_id},
            )
        print(f"wrote {len(changes)} titles")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
