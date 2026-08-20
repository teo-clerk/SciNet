#!/usr/bin/env python
"""Measure what each parse tier actually costs on this machine.

Planning estimates are not good enough here: the difference between 0.25 s/page
and 181 s/page decides whether a 4,000-paper backfill takes an evening or a
fortnight, and the only way to know is to run it.

    uv run python ../scripts/bench_parse.py                    # fixtures
    uv run python ../scripts/bench_parse.py ~/Papers --sample 30
    uv run python ../scripts/bench_parse.py --tiers 0,1
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings  # noqa: E402
from app.core.model_store import configure_environment  # noqa: E402

configure_environment(get_settings())

from app.services.parse import tier0_pymupdf, tier1_marker, tier2_vlm  # noqa: E402
from app.services.parse.quality import assess  # noqa: E402
from app.services.parse.router import ParserUnavailable  # noqa: E402

TIERS = {
    0: ("tier0/pymupdf", tier0_pymupdf.parse),
    1: ("tier1/marker", tier1_marker.parse),
    2: ("tier2/vlm", tier2_vlm.parse),
}


def gpu_mib() -> int:
    try:
        import torch

        if torch.cuda.is_available():
            return torch.cuda.max_memory_allocated() // (1024 * 1024)
    except Exception:  # noqa: BLE001
        pass
    return 0


def reset_gpu_peak() -> None:
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
    except Exception:  # noqa: BLE001
        pass


def bench_tier(tier: int, pdfs: list[Path]) -> None:
    label, parser = TIERS[tier]
    print(f"\n=== {label} ===")

    per_page: list[float] = []
    reset_gpu_peak()
    warm = False

    for pdf in pdfs:
        try:
            started = time.perf_counter()
            result = parser(pdf)
            elapsed = time.perf_counter() - started
        except ParserUnavailable as exc:
            print(f"  unavailable: {exc}")
            return
        except Exception as exc:  # noqa: BLE001
            print(f"  {pdf.name:<28} failed: {type(exc).__name__}: {exc}")
            continue

        pages = max(result.page_count, 1)
        ms = elapsed / pages * 1000
        report = assess(
            __import__(
                "app.services.parse.quality", fromlist=["TextProbe"]
            ).TextProbe(text=result.markdown, page_count=pages, font_count=1)
        )
        note = "" if warm else "  (includes model load)"
        print(
            f"  {pdf.name:<28} {pages:>2}pg  {elapsed:>7.2f}s  "
            f"{ms:>8.0f} ms/pg  {len(result.markdown):>7} chars  "
            f"quality={'pass' if report.passed else 'FAIL'}{note}"
        )
        if warm:
            per_page.append(ms)
        warm = True

    if per_page:
        print(
            f"  warm median: {statistics.median(per_page):.0f} ms/page  "
            f"(peak GPU {gpu_mib()} MiB)"
        )
        for corpus in (500, 4000):
            hours = statistics.median(per_page) / 1000 * 12 * corpus / 3600
            print(f"    {corpus:>5} papers x 12pg -> {hours:6.2f} h single-threaded")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path)
    parser.add_argument("--sample", type=int, default=6)
    parser.add_argument("--tiers", default="0,1")
    args = parser.parse_args()

    if args.root:
        pdfs = sorted(p for p in args.root.expanduser().rglob("*.pdf"))[: args.sample]
    else:
        sys.path.insert(0, str(BACKEND))
        from tests.fixtures.generate import build_all

        out = BACKEND / "tests" / "fixtures" / "generated"
        pdfs = list(build_all(out).values())[: args.sample]

    if not pdfs:
        print("no PDFs to benchmark")
        return 1

    print(f"benchmarking {len(pdfs)} PDF(s)")
    print(f"hf cache: {get_settings().hf_cache_dir}")

    for tier in [int(t) for t in args.tiers.split(",")]:
        bench_tier(tier, pdfs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
