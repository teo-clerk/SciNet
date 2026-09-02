#!/usr/bin/env python
"""Reports whether this machine can actually run the configured pipeline.

Run this before a large backfill. The failure it exists to catch is silent:
Ollama serves a model that does not fit in VRAM by running it on the CPU, with
no error and no warning — just pages that take minutes instead of seconds.

    uv run python ../scripts/doctor.py

Hardware detection lives in ``app.core.hardware`` so the API and the worker
judge fit against the same probed card this report shows. (An earlier version
kept the probe here, then passed it under the wrong keyword — the doctor
crashed with a TypeError — and even called correctly it would have judged fit
against the reference machine's constant rather than the probed number.)
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from app.core import hardware  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.preflight import check_models, format_report  # noqa: E402


def main() -> int:
    settings = get_settings()
    report = hardware.probe(settings.models_dir)

    if report.vram_total_mib:
        print(f"GPU           : {report.gpu_name} ({report.vram_total_mib} MiB)")
        print(f"VRAM budget   : {hardware.vram_budget_mib()} MiB after safety margin")
    else:
        print("GPU           : no NVIDIA GPU found (models will run on CPU)")
    if report.ram_total_mib:
        print(f"system RAM    : {report.ram_total_mib} MiB")
    if report.disk_free_gib is not None:
        print(f"disk free     : {report.disk_free_gib} GiB (models drive)")
    print(f"library       : {settings.library_dir}")
    print(f"database      : {settings.db_path}")
    egress = "ON" if settings.enrichment_enabled else "off (local only)"
    print(f"enrichment    : {egress}")
    print()

    statuses = check_models(settings)
    print(format_report(statuses))
    print()

    problems = [s for s in statuses if not s.ok]
    if not problems:
        print("all configured models are installed and fit in VRAM")
        return 0

    print(f"{len(problems)} model(s) need attention (see notes above)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
