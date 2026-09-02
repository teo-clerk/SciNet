"""The machine is probed, never assumed — and never probed at import.

Two failures guarded here. First, the doctor once passed its probe under the
wrong keyword (TypeError) and, called correctly, would still have judged fit
against the reference machine's 8188 MiB constant — a 24 GB card was told its
models were CPU-only. Second, ``GPU = GpuSlot()`` runs at module import in
every process; a probed default that spawned nvidia-smi at import time would
put a subprocess in the API's startup, the worker's, and every test's.
"""

from __future__ import annotations

import dataclasses
import subprocess

import pytest

from app.core import hardware
from app.core.gpu import GpuSlot
from app.core.models_registry import (
    REGISTRY,
    TOTAL_VRAM_MIB,
    VRAM_SAFETY_MARGIN_MIB,
)
from app.core.preflight import ModelStatus


@pytest.fixture(autouse=True)
def fresh_probe_cache():
    """total_vram_mib is lru_cached per process; tests must not share it."""
    hardware.total_vram_mib.cache_clear()
    yield
    hardware.total_vram_mib.cache_clear()


def entry(vram: int | None):
    return dataclasses.replace(REGISTRY[1], vram_mib=vram)


# --- detection ---------------------------------------------------------------


def test_no_nvidia_smi_reads_as_no_gpu(monkeypatch) -> None:
    monkeypatch.setattr(hardware.shutil, "which", lambda name: None)
    assert hardware.detected_vram() is None


def test_nvidia_smi_output_is_parsed_to_mib_and_name(monkeypatch) -> None:
    monkeypatch.setattr(hardware.shutil, "which", lambda name: "/usr/bin/nvidia-smi")

    class Out:
        stdout = "24564, NVIDIA GeForce RTX 4090\n"

    monkeypatch.setattr(hardware.subprocess, "run", lambda *a, **k: Out())
    assert hardware.detected_vram() == (24564, "NVIDIA GeForce RTX 4090")


def test_a_broken_driver_reads_as_no_gpu(monkeypatch) -> None:
    monkeypatch.setattr(hardware.shutil, "which", lambda name: "/usr/bin/nvidia-smi")

    def boom(*a, **k):
        raise subprocess.CalledProcessError(1, "nvidia-smi")

    monkeypatch.setattr(hardware.subprocess, "run", boom)
    assert hardware.detected_vram() is None


def test_probe_never_raises_and_reports_what_it_could_learn(monkeypatch) -> None:
    monkeypatch.setattr(hardware, "detected_vram", lambda: None)
    report = hardware.probe()
    assert report.vram_total_mib is None and report.gpu_name is None
    # RAM and disk are facts on any Linux box; None is allowed elsewhere.
    assert report.ram_total_mib is None or report.ram_total_mib > 0


# --- the fallback ------------------------------------------------------------


def test_without_a_gpu_the_reference_machine_is_the_fallback(monkeypatch) -> None:
    monkeypatch.setattr(hardware, "detected_vram", lambda: None)
    assert hardware.total_vram_mib() == TOTAL_VRAM_MIB
    assert hardware.vram_budget_mib() == TOTAL_VRAM_MIB - VRAM_SAFETY_MARGIN_MIB


def test_a_probed_card_overrides_the_constant(monkeypatch) -> None:
    monkeypatch.setattr(hardware, "detected_vram", lambda: (24564, "RTX 4090"))
    assert hardware.total_vram_mib() == 24564


# --- the slot ----------------------------------------------------------------


def test_constructing_the_slot_probes_nothing(monkeypatch) -> None:
    def forbidden():
        raise AssertionError("GpuSlot() must not probe at construction")

    monkeypatch.setattr(hardware, "total_vram_mib", forbidden)
    GpuSlot()  # only *using* the slot may resolve the total


def test_an_unconfigured_slot_uses_the_probed_card(monkeypatch) -> None:
    monkeypatch.setattr(hardware, "total_vram_mib", lambda: 24564)
    slot = GpuSlot()
    # 13.6 GiB was CPU-only on the reference machine; on a 24 GB card it fits.
    assert slot.fits(entry(13619)) is True


def test_an_explicit_total_still_wins(monkeypatch) -> None:
    monkeypatch.setattr(hardware, "total_vram_mib", lambda: 24564)
    assert GpuSlot(total_vram_mib=8188).fits(entry(13619)) is False


# --- the verdict carries its budget ------------------------------------------


def test_fit_is_judged_against_the_budget_the_check_ran_with() -> None:
    big_card = ModelStatus(entry(13619), True, "", vram_budget_mib=24052)
    small_card = ModelStatus(entry(13619), True, "", vram_budget_mib=7676)
    assert big_card.fits_vram is True and big_card.ok
    assert small_card.fits_vram is False and not small_card.ok


def test_the_doctor_keyword_regression_cannot_recur() -> None:
    """check_models(settings, vram_budget_mib=...) is the signature the doctor
    calls; a rename on either side dies here instead of in the field."""
    import inspect

    from app.core.preflight import check_models

    assert "vram_budget_mib" in inspect.signature(check_models).parameters
