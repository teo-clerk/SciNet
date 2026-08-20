#!/usr/bin/env python
"""Predict what a corpus will cost to ingest, without ingesting it.

Probing a PDF's text layer costs ~2 ms/page and needs no GPU, so the whole
tiering decision for a library can be computed in seconds. That turns "is tier 1
worth installing?" from a guess into a number: if 95% of your papers pass on
tier 0, the middle tier buys very little; if 30% escalate, it is the difference
between an evening and a weekend.

    uv run python ../scripts/survey_corpus.py ~/Papers
    uv run python ../scripts/survey_corpus.py ~/Papers --sample 200 --verbose
"""

from __future__ import annotations

import argparse
import collections
import random
import statistics
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings  # noqa: E402
from app.services.parse.quality import assess  # noqa: E402
from app.services.parse.tier0_pymupdf import probe_pdf  # noqa: E402

# Measured on an Intel Ultra 9 185H / RTX 4060 Laptop. Override if your numbers
# differ; scripts/bench_parse.py is what produces them.
TIER0_MS_PER_PAGE = 298
TIER1_MS_PER_PAGE = 2000
TIER2_MS_PER_PAGE = 8900


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "root", nargs="?", type=Path, default=get_settings().library_dir
    )
    parser.add_argument("--sample", type=int, help="probe a random subset")
    parser.add_argument("--verbose", action="store_true", help="list every escalation")
    parser.add_argument("--seed", type=int, default=17)
    args = parser.parse_args()

    root = args.root.expanduser().resolve()
    pdfs = sorted(p for p in root.rglob("*.pdf") if p.is_file())
    if not pdfs:
        print(f"no PDFs under {root}")
        return 1

    population = len(pdfs)
    if args.sample and args.sample < population:
        random.Random(args.seed).shuffle(pdfs)
        pdfs = pdfs[: args.sample]

    print(f"surveying {len(pdfs)} of {population} PDF(s) under {root}\n")

    passed = 0
    reasons: collections.Counter[str] = collections.Counter()
    pages_pass: list[int] = []
    pages_fail: list[int] = []
    failures: list[tuple[str, int, tuple[str, ...]]] = []
    unreadable: list[str] = []

    started = time.perf_counter()
    for index, pdf in enumerate(pdfs, start=1):
        try:
            report = assess(probe_pdf(pdf))
        except Exception as exc:  # noqa: BLE001 - a corrupt file is a finding
            unreadable.append(f"{pdf.name}: {type(exc).__name__}")
            continue

        pages = int(report.metrics.get("chars_per_page", 0) > 0) or 1
        try:
            import pymupdf

            with pymupdf.open(pdf) as doc:
                pages = doc.page_count
        except Exception:  # noqa: BLE001
            pass

        if report.passed:
            passed += 1
            pages_pass.append(pages)
        else:
            pages_fail.append(pages)
            reasons.update(report.reasons)
            failures.append((pdf.name, pages, report.reasons))

        if index % 100 == 0:
            print(f"  probed {index}/{len(pdfs)} ...", flush=True)

    elapsed = time.perf_counter() - started
    total = passed + len(pages_fail)
    if not total:
        print("nothing could be probed")
        return 1

    escalating = len(pages_fail)
    all_pages = sum(pages_pass) + sum(pages_fail)

    print(f"\nprobed {total} file(s) in {elapsed:.1f}s "
          f"({elapsed / total * 1000:.0f} ms each)")
    if unreadable:
        print(f"unreadable: {len(unreadable)}")
        for line in unreadable[:10]:
            print(f"  {line}")

    print("\ntier distribution")
    print(f"  tier 0 (text layer) : {passed:>5}  {100 * passed / total:5.1f}%"
          f"   {sum(pages_pass):>6} pages")
    print(f"  escalating          : {escalating:>5}  {100 * escalating / total:5.1f}%"
          f"   {sum(pages_fail):>6} pages")
    if all_pages:
        median_pages = statistics.median(pages_pass + pages_fail)
        print(f"  median pages/paper  : {median_pages:.0f}")

    if reasons:
        print("\nwhy papers escalate")
        for reason, count in reasons.most_common():
            share = 100 * count / escalating
            print(f"  {reason:<26} {count:>5}  ({share:.1f}% of them)")

    print("\nprojected ingestion cost for this corpus")
    t0_h = sum(pages_pass) * TIER0_MS_PER_PAGE / 1000 / 3600
    esc_pages = sum(pages_fail)
    with_t1 = t0_h + esc_pages * TIER1_MS_PER_PAGE / 1000 / 3600
    without_t1 = t0_h + esc_pages * TIER2_MS_PER_PAGE / 1000 / 3600
    print(f"  tier 0 work              : {t0_h:6.2f} h")
    print(f"  with tier 1 (~2 s/pg)    : {with_t1:6.2f} h total")
    print(f"  without tier 1 (~8.9s/pg): {without_t1:6.2f} h total")
    saved = without_t1 - with_t1
    print(f"  tier 1 saves             : {saved:6.2f} h "
          f"({100 * saved / without_t1:.0f}% of the run)" if without_t1 else "")
    print("\n  (single-threaded; tier 0 parallelises across CPU workers)")

    if args.verbose and failures:
        print("\nescalating papers")
        for name, pages, why in failures:
            print(f"  {name:<52} {pages:>3}pg  {', '.join(why)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
