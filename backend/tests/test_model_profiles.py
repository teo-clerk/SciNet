"""The catalog: seeded once, updated by measurement, never forked.

The loop this closes: download_models.py --measure used to end by telling a
human to copy numbers back into models_registry.py by hand. A catalog that
forks a second row per measurement, or clobbers a local measurement on
re-seed, would reopen it.
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.models_registry import REGISTRY, REJECTED_MODELS
from app.models import ModelProfile, ModelVerdict, ProfileSource
from app.services.models.profiles import classify, record_measurement, seed_profiles


def test_seeding_is_idempotent(db) -> None:
    first = seed_profiles(db)
    second = seed_profiles(db)
    assert first == len(REGISTRY) + len(REJECTED_MODELS)
    assert second == 0
    assert len(db.scalars(select(ModelProfile)).all()) == first


def test_the_rejected_models_finally_have_a_consumer(db) -> None:
    """REJECTED_MODELS sat in the registry as a memo nobody read. Seeded as
    cpu-only rows, the catalog can warn people off the 13 GiB download."""
    seed_profiles(db)
    rejected = db.get(ModelProfile, "qwen2.5vl:7b")
    assert rejected is not None
    assert rejected.verdict == ModelVerdict.CPU_ONLY
    assert "CPU-only" in (rejected.notes or "")
    assert rejected.vram_mib == 13619


def test_measured_builtins_seed_as_gpu_and_unmeasured_as_unproven(db) -> None:
    seed_profiles(db)
    assert db.get(ModelProfile, "qwen3:8b").verdict == ModelVerdict.GPU
    surya = db.get(ModelProfile, "datalab-to/surya-ocr-2-gguf")
    assert surya.verdict == ModelVerdict.UNPROVEN
    assert surya.vram_mib is None


def test_a_measurement_updates_the_builtin_row_in_place(db) -> None:
    seed_profiles(db)
    before = len(db.scalars(select(ModelProfile)).all())
    profile = record_measurement(
        db, "qwen3:8b", total_mib=5673, vram_mib=5673, tok_per_s=42.5
    )
    assert len(db.scalars(select(ModelProfile)).all()) == before, "no fork"
    assert profile.source == ProfileSource.BUILTIN, "identity survives"
    assert profile.tok_per_s == 42.5
    assert profile.last_measured_at is not None


def test_measuring_an_unknown_model_discovers_it(db) -> None:
    profile = record_measurement(db, "somebody/else:7b", total_mib=100, vram_mib=100)
    assert profile.source == ProfileSource.DISCOVERED
    assert profile.verdict == ModelVerdict.GPU


def test_the_classifier_matches_the_measure_scripts_thresholds() -> None:
    assert classify(1000, 1000) == ModelVerdict.GPU
    assert classify(1000, 901) == ModelVerdict.GPU
    assert classify(1000, 500) == ModelVerdict.PARTIAL
    # 0% on the card with a nonzero total: the silent CPU-serving signature.
    assert classify(13619, 0) == ModelVerdict.CPU_ONLY
    assert classify(0, 0) == ModelVerdict.UNPROVEN
