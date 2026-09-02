"""The model-profile catalog: seeded from the registry, updated by measurement.

Seeding is idempotent and never overwrites — a measurement taken on this
machine outranks the shipped number, so re-seeding after an upgrade must not
clobber it. ``REJECTED_MODELS`` gets seeded too, as ``cpu-only`` rows carrying
their original verdicts ("13.3 GiB resident from a 2.98 GiB download"): the
registry kept that table as an anti-regression memo with zero consumers, and
a catalog whose job is warning people off bad candidates is its first one.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.models_registry import REGISTRY, REJECTED_MODELS, Role, Runtime
from app.core.types import utcnow
from app.models import ModelProfile, ModelVerdict, ProfileSource

#: The measurement classifier's thresholds, shared with download_models.py:
#: a model >90% on the card is GPU; >0% is split; 0% with a nonzero total is
#: the silent CPU-serving signature this whole subsystem exists to catch.
GPU_FRACTION = 0.90


def classify(total_mib: int, vram_mib: int) -> ModelVerdict:
    if total_mib <= 0:
        return ModelVerdict.UNPROVEN
    fraction = vram_mib / total_mib
    if fraction > GPU_FRACTION:
        return ModelVerdict.GPU
    if vram_mib > 0:
        return ModelVerdict.PARTIAL
    return ModelVerdict.CPU_ONLY


def seed_profiles(session: Session) -> int:
    """Insert whatever the catalog is missing. Returns how many rows landed."""
    added = 0
    for entry in REGISTRY:
        if session.get(ModelProfile, entry.reference) is not None:
            continue
        session.add(
            ModelProfile(
                reference=entry.reference,
                runtime=entry.runtime.value,
                role=entry.role.value,
                disk_mib=entry.disk_mib,
                vram_mib=entry.vram_mib,
                # The shipped numbers were measured fully-resident on the
                # reference machine; an unmeasured entry stays unproven.
                verdict=(ModelVerdict.GPU if entry.vram_mib else ModelVerdict.UNPROVEN),
                source=ProfileSource.BUILTIN,
                notes=entry.purpose,
            )
        )
        added += 1

    for reference, (disk_mib, resident_mib, why) in REJECTED_MODELS.items():
        if session.get(ModelProfile, reference) is not None:
            continue
        session.add(
            ModelProfile(
                reference=reference,
                runtime=Runtime.OLLAMA.value,
                role=Role.VISION.value,
                disk_mib=disk_mib,
                vram_mib=resident_mib,
                verdict=ModelVerdict.CPU_ONLY,
                source=ProfileSource.BUILTIN,
                notes=why,
            )
        )
        added += 1
    session.flush()
    return added


def record_measurement(
    session: Session,
    reference: str,
    *,
    total_mib: int,
    vram_mib: int,
    tok_per_s: float | None = None,
    role: Role = Role.TAG,
    runtime: Runtime = Runtime.OLLAMA,
    note: str | None = None,
) -> ModelProfile:
    """Write a measurement into the catalog, updating in place.

    A known reference keeps its identity (source, role, notes) and gains the
    measured facts; an unknown one enters as ``discovered`` — measuring a
    model is the strongest possible evidence that it exists.
    """
    profile = session.get(ModelProfile, reference)
    if profile is None:
        profile = ModelProfile(
            reference=reference,
            runtime=runtime.value,
            role=role.value,
            source=ProfileSource.DISCOVERED,
        )
        session.add(profile)

    profile.vram_mib = vram_mib
    profile.disk_mib = profile.disk_mib or total_mib
    profile.verdict = classify(total_mib, vram_mib)
    if tok_per_s is not None:
        profile.tok_per_s = round(tok_per_s, 1)
    if note:
        profile.notes = note
    profile.last_measured_at = utcnow()
    session.flush()
    return profile
