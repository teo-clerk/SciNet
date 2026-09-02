"""What this machine actually is.

``TOTAL_VRAM_MIB = 8188`` in models_registry.py describes the *reference
machine* — the laptop every measured number in this repository came from. It
stops being a fact the moment the checkout runs anywhere else: a 24 GB card
told it has 8 GiB refuses models that would fly, and a 6 GB card told the
opposite silently serves them from system RAM at ~20x. Detection is one
nvidia-smi call, cached for the process; the registry constants remain the
fallback, because a machine with no NVIDIA GPU still needs a number to
reason with, and the reference machine's is the only honest one available.

Nothing here probes at import time. ``GPU = GpuSlot()`` runs when
app.core.gpu loads — in the API, the worker, and every test — and spawning
subprocesses as an import side effect is against everything this codebase
does. The probe happens on first *use*.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.core.models_registry import TOTAL_VRAM_MIB, VRAM_SAFETY_MARGIN_MIB

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class HardwareReport:
    """One probe's worth of facts. None means "could not be determined"."""

    vram_total_mib: int | None
    gpu_name: str | None
    ram_total_mib: int | None
    disk_free_gib: float | None


def detected_vram() -> tuple[int, str] | None:
    """(total MiB, card name) from nvidia-smi, or None.

    None covers "no NVIDIA GPU" and "driver broken" alike — both mean the
    card cannot be planned against, which is the only question asked here.
    """
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.total,name",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        first = out.stdout.strip().splitlines()[0]
        mib, _, name = first.partition(",")
        return int(mib.strip()), name.strip()
    except Exception:  # noqa: BLE001 - any failure reads as "no usable GPU"
        return None


def _ram_total_mib() -> int | None:
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return int(pages * page_size / (1024 * 1024))
    except (ValueError, OSError, AttributeError):
        # Windows has no sysconf; a psutil dependency is not worth one field.
        return None


def probe(disk_path: Path | None = None) -> HardwareReport:
    """Everything worth knowing, in one call. Never raises."""
    gpu = detected_vram()
    try:
        free = shutil.disk_usage(disk_path or REPO_ROOT).free
        disk_free_gib: float | None = round(free / 1024**3, 1)
    except OSError:
        disk_free_gib = None
    return HardwareReport(
        vram_total_mib=gpu[0] if gpu else None,
        gpu_name=gpu[1] if gpu else None,
        ram_total_mib=_ram_total_mib(),
        disk_free_gib=disk_free_gib,
    )


@lru_cache(maxsize=1)
def total_vram_mib() -> int:
    """The card's real size, or the reference machine's as the fallback."""
    gpu = detected_vram()
    return gpu[0] if gpu else TOTAL_VRAM_MIB


def vram_budget_mib() -> int:
    """What may be planned against: the total minus the safety margin —
    the display server and the desktop live on the same card."""
    return total_vram_mib() - VRAM_SAFETY_MARGIN_MIB
