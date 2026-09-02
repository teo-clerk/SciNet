"""Per-machine measured facts about local models.

The static registry in ``core/models_registry.py`` is the *shipped* knowledge:
the reference machine's measurements, the known-good defaults, the rejected
candidates. This table is what the running system *learns* — a model measured
on this card, a model discovered in the user's system Ollama, a footprint
re-measured after a quantization change. Before it existed, measurement ended
with ``download_models.py`` printing "update vram_mib in models_registry.py
by hand", which is a loop nobody closes twice.

``verdict`` records the measurement's *outcome* (was the model fully
GPU-resident when warmed?), not a per-card fit prediction — fit against the
current card's budget is computed at read time from ``vram_mib``, because the
same profile row must stay true when the checkout moves to a bigger machine.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.types import UtcDateTime, utcnow
from app.models.enums import ModelVerdict, ProfileSource


class ModelProfile(Base):
    __tablename__ = "model_profiles"

    #: The runtime's own name for the model (ollama tag or HF repo id) — the
    #: natural key everything else already uses.
    reference: Mapped[str] = mapped_column(String(128), primary_key=True)
    runtime: Mapped[str] = mapped_column(String(16))  # ollama | huggingface
    role: Mapped[str] = mapped_column(String(16))  # Role value: ocr|vision|embed|tag

    disk_mib: Mapped[int | None] = mapped_column(Integer)
    #: Measured resident footprint. None = never measured = unproven; the
    #: house rule is that unproven is not assumed to fit.
    vram_mib: Mapped[int | None] = mapped_column(Integer)
    tok_per_s: Mapped[float | None] = mapped_column(Float)
    #: Embed models only: the dimension the model actually produces.
    embed_dim: Mapped[int | None] = mapped_column(Integer)
    #: Per-task benchmark scores, JSON — schema owned by services/models.
    bench_json: Mapped[str | None] = mapped_column(Text)

    verdict: Mapped[str] = mapped_column(
        String(16), default=ModelVerdict.UNPROVEN, index=True
    )
    source: Mapped[str] = mapped_column(String(16), default=ProfileSource.BUILTIN)
    notes: Mapped[str | None] = mapped_column(Text)

    last_measured_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, onupdate=utcnow
    )
