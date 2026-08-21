"""Re-derive stored abstracts through the real extraction path.

The abstract is the single most informative field on the map, and it is
heuristic — so improving the heuristic leaves the corpus behind: rows keep
whatever the rules said on the day they were parsed. On the reference corpus
ten of fifty-seven abstracts were not prose at all but the artefacts the rules
now reject: a keywords table converted to Markdown pipes, a header block of
DOIs and received-dates, a figure caption, a copyright line, and a fragment
starting mid-sentence. Each of those is embedded into the document vector and
helps decide where its paper sits.

Only rows whose current abstract fails the prose test are touched, so a good
abstract is never replaced by a differently-derived one. Papers that have no
abstract are left alone: this fixes what is wrong, it does not fill gaps.

**Changing an abstract changes the document vector.** The new text is not
reflected on the map until those papers are re-embedded and the corpus is
re-projected:

    cd backend && uv run python -m app.workers.runner

Dry run by default; pass ``--apply`` to write. The worker must be stopped
first: it is the sole writer of paper data.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import select  # noqa: E402

from app.core.db import session_scope  # noqa: E402
from app.models import MarkdownDoc, MetaSource, PaperMeta  # noqa: E402
from app.services.metadata.synopsis import (  # noqa: E402
    Strategy,
    find_synopsis,
    is_prose,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="write the changes (default: dry run)"
    )
    args = parser.parse_args()

    changed = skipped = 0
    with session_scope() as session:
        for meta in session.scalars(select(PaperMeta).order_by(PaperMeta.paper_id)):
            if not meta.abstract or is_prose(meta.abstract):
                continue

            doc = session.get(MarkdownDoc, meta.paper_id)
            if doc is None or not Path(doc.md_path).exists():
                print(f"[{meta.paper_id}] no markdown on disk; skipped")
                skipped += 1
                continue

            found = find_synopsis(Path(doc.md_path).read_text(encoding="utf-8"))
            if found is None or found.text == meta.abstract:
                print(f"[{meta.paper_id}] nothing better available; kept")
                skipped += 1
                continue

            print(f"[{meta.paper_id}] {found.strategy.value}")
            print(f"    was: {meta.abstract[:96]!r}")
            print(f"    now: {found.text[:96]!r}")
            changed += 1

            if args.apply:
                meta.abstract = found.text
                sources = (
                    json.loads(meta.field_sources_json)
                    if meta.field_sources_json
                    else {}
                )
                sources["abstract"] = str(
                    MetaSource.EXTRACTED_DIGEST
                    if found.strategy is Strategy.DIGEST
                    else MetaSource.HEURISTIC
                )
                meta.field_sources_json = json.dumps(sources)
                session.add(meta)

    print()
    if args.apply:
        print(f"rewrote {changed} abstract(s); {skipped} skipped")
        print("Re-embed and re-project so the map reflects them:")
        print("  cd backend && uv run python -m app.workers.runner")
    else:
        print(f"{changed} abstract(s) would change, {skipped} skipped — dry run")
        print("Pass --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
