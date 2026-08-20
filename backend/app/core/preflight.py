"""Reports whether the configured models are provisioned and will fit in VRAM.

The failure this catches is silent and expensive. Ollama serves a model that
exceeds VRAM by running it from system RAM — no error, no warning, roughly a
twentyfold latency penalty. The first symptom is a backfill that looks hung.
So "installed" is not enough; a model is only healthy if its *measured* resident
footprint is known and fits.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings
from app.core.model_store import PrivateOllama, hf_dir
from app.core.models_registry import (
    REGISTRY,
    VRAM_BUDGET_MIB,
    ModelEntry,
    Runtime,
)


@dataclass(frozen=True)
class ModelStatus:
    entry: ModelEntry
    installed: bool
    note: str

    @property
    def fits_vram(self) -> bool | None:
        return self.entry.fits_vram

    @property
    def ok(self) -> bool:
        return self.installed and self.entry.fits_vram is not False


def _hf_present(reference: str) -> bool:
    marker = hf_dir() / "hub" / f"models--{reference.replace('/', '--')}"
    return marker.exists()


def check_models(
    settings: Settings, vram_budget_mib: int = VRAM_BUDGET_MIB
) -> list[ModelStatus]:
    server = PrivateOllama(settings)
    # Never starts a server just to look: an unreachable one simply means the
    # project's store has nothing loaded yet.
    installed_tags = server.installed() if server.is_running() else set()
    server_up = server.is_running()

    statuses: list[ModelStatus] = []
    for entry in REGISTRY:
        if entry.runtime is Runtime.OLLAMA:
            installed = entry.reference in installed_tags
            if not server_up:
                note = "project model server is not running"
            elif not installed:
                note = "run scripts/download_models.py"
            else:
                note = ""
        else:
            installed = _hf_present(entry.reference)
            note = "" if installed else "run scripts/download_models.py"

        if entry.vram_mib is None:
            note = note or "resident footprint not measured on this machine"
        elif entry.vram_mib > vram_budget_mib:
            note = (
                f"needs ~{entry.vram_mib} MiB but only {vram_budget_mib} MiB is "
                "usable; it will be served from RAM and be ~20x slower"
            )

        statuses.append(ModelStatus(entry, installed, note))
    return statuses


def format_report(statuses: list[ModelStatus]) -> str:
    lines = [f"{'role':<8} {'model':<30} {'vram':>7}  status"]
    for s in statuses:
        if not s.installed:
            state = "missing"
        elif s.fits_vram is False:
            state = "CPU-ONLY"
        elif s.fits_vram is None:
            state = "unverified"
        else:
            state = "ok"
        vram = "?" if s.entry.vram_mib is None else f"{s.entry.vram_mib / 1024:.2f}G"
        lines.append(
            f"{s.entry.role.value:<8} {s.entry.reference:<30} {vram:>7}  {state}"
        )
        if s.note:
            lines.append(f"         -> {s.note}")
    return "\n".join(lines)
