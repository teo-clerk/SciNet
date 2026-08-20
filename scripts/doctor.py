#!/usr/bin/env python
"""Reports whether this machine can actually run the configured pipeline.

Run this before a large backfill. The failure it exists to catch is silent:
Ollama serves a model that does not fit in VRAM by running it on the CPU, with
no error and no warning — just pages that take minutes instead of seconds.

    uv run python ../scripts/doctor.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings  # noqa: E402
from app.core.preflight import check_models, format_report  # noqa: E402


def detected_vram_mib() -> int | None:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return int(out.stdout.strip().splitlines()[0])
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    settings = get_settings()

    vram = detected_vram_mib()
    print(
        f"GPU VRAM      : {vram} MiB" if vram else "GPU VRAM      : no NVIDIA GPU found"
    )
    print(f"library       : {settings.library_dir}")
    print(f"database      : {settings.db_path}")
    egress = "ON" if settings.enrichment_enabled else "off (local only)"
    print(f"enrichment    : {egress}")
    print()

    statuses = check_models(settings, total_vram_mib=vram or 0)
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
