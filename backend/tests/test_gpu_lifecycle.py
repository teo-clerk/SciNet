"""Freeing the GPU between pipeline stages.

The card holds one large model at a time. Three different runtimes can be
holding memory — an in-process torch cache, a spawned llama-server, and the
private Ollama instance — and each is released differently, so "empty the card"
has to be one explicit operation rather than an assumption.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.core.gpu import GPU, GpuSlot, free_all_models
from app.core.models_registry import REGISTRY, VRAM_BUDGET_MIB, ModelEntry, Role


def entry(vram: int | None, key: str = "granite3.2-vision:2b") -> ModelEntry:
    base = next(e for e in REGISTRY if e.reference == key)
    return replace(base, vram_mib=vram)


def test_a_measured_model_that_fits_is_accepted():
    slot = GpuSlot(total_vram_mib=8188)
    assert slot.fits(entry(3605))


def test_an_oversized_model_is_refused():
    slot = GpuSlot(total_vram_mib=8188)
    assert not slot.fits(entry(13312))


def test_an_unmeasured_model_is_not_assumed_to_fit():
    """Guessing here costs 181 s/page, so refusing to guess is the point."""
    slot = GpuSlot(total_vram_mib=8188)
    assert not slot.fits(entry(None))


def test_holding_an_oversized_model_raises():
    slot = GpuSlot(total_vram_mib=8188)
    with pytest.raises(RuntimeError, match="served from RAM"):
        with slot.hold(entry(13312)):
            pass


def test_switching_models_evicts_the_incumbent():
    slot = GpuSlot(total_vram_mib=8188)
    evicted = []

    with slot.hold(entry(3605), unload=lambda: evicted.append("vision")):
        pass
    assert evicted == [], "same-stage reuse must keep the model warm"

    with slot.hold(entry(5673, key="qwen3:8b")):
        pass
    assert evicted == ["vision"], "a different model must evict the previous one"


def test_the_same_model_twice_stays_warm():
    """Reloading between papers costs more than the per-paper work."""
    slot = GpuSlot(total_vram_mib=8188)
    unloads = []
    spec = entry(3605)

    for _ in range(3):
        with slot.hold(spec, unload=lambda: unloads.append(1)):
            pass
    assert unloads == []


def test_release_runs_the_unload_callback():
    slot = GpuSlot(total_vram_mib=8188)
    freed = []
    with slot.hold(entry(3605), unload=lambda: freed.append(1)):
        pass
    slot.release()
    assert freed == [1]
    assert slot.resident is None


def test_a_failing_unload_does_not_block_the_next_stage():
    """Eviction must never be able to stall the pipeline."""
    slot = GpuSlot(total_vram_mib=8188)

    def boom():
        raise RuntimeError("driver hiccup")

    with slot.hold(entry(3605), unload=boom):
        pass
    slot.release()  # must not raise
    assert slot.resident is None


def test_free_all_models_is_safe_with_nothing_loaded():
    """Called between every stage, including ones that touched no model."""
    free_all_models()
    assert GPU.resident is None


def test_every_registered_model_fits_the_budget_or_is_unmeasured():
    """A regression guard: nothing oversized may be adopted as a default."""
    for model in REGISTRY:
        if model.vram_mib is not None:
            assert model.vram_mib <= VRAM_BUDGET_MIB, (
                f"{model.reference} needs {model.vram_mib} MiB, "
                f"budget is {VRAM_BUDGET_MIB}"
            )


def test_the_two_largest_models_cannot_be_co_resident():
    """The premise the single-slot design rests on."""
    measured = [e.vram_mib for e in REGISTRY if e.vram_mib is not None]
    measured.sort(reverse=True)
    assert len(measured) >= 2
    assert measured[0] + measured[1] > VRAM_BUDGET_MIB


def test_vision_role_is_a_measured_fitting_model():
    vision = next(e for e in REGISTRY if e.role is Role.VISION)
    assert vision.vram_mib is not None, "tier 2's footprint must be measured"
    assert vision.fits_vram is True


# --- model presence detection --------------------------------------------


def test_a_model_is_found_in_either_cache_layout(tmp_path, monkeypatch):
    """huggingface_hub writes to HF_HOME/hub; sentence-transformers to its root.

    A check that knows only one reported an installed model as missing.
    """
    from app.core import preflight

    monkeypatch.setattr(preflight, "hf_dir", lambda: tmp_path)

    (tmp_path / "hub" / "models--org--via-hub").mkdir(parents=True)
    (tmp_path / "models--org--via-st").mkdir(parents=True)

    assert preflight._hf_present("org/via-hub")
    assert preflight._hf_present("org/via-st")
    assert not preflight._hf_present("org/absent")
