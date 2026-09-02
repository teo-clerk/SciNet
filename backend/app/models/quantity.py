"""Measured values extracted from the library's prose.

Technical literature is full of load-bearing numbers — an orbit's altitude,
a sensor's resolution, a thruster's specific impulse — and this table is
what makes them queryable: each row is one value, SI-normalised, carrying
the *verbatim sentence* it came from. Verbatim because chunks are replaced
wholesale on re-parse and hold no character offsets: the sentence is the
only provenance that survives its own source moving.

Rows key their position as ``(paper_id, chunk_ord)`` with no foreign key to
the chunk row itself — chunk ids are disposable, papers are not. A re-parse
deletes and re-extracts by paper, the same wholesale rule chunks follow.

``value_si`` is nullable: an ambiguous hit (a unit the allowlist does not
know) is recorded for adjudication rather than guessed at — a wrong unit is
worse than a missing one, everywhere this table will be used.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.types import UtcDateTime, utcnow
from app.models.enums import QuantityStatus


class Quantity(Base):
    __tablename__ = "quantities"

    id: Mapped[int] = mapped_column(primary_key=True)
    paper_id: Mapped[int] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), index=True
    )
    chunk_ord: Mapped[int] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(Text)

    #: A dimension family (length, time, frequency, …), not a semantic role —
    #: whether 300 s is an Isp or a timeout is the reader's call, honestly.
    quantity_kind: Mapped[str] = mapped_column(String(32))
    value_si: Mapped[float | None] = mapped_column(Float)
    unit_si: Mapped[str | None] = mapped_column(String(32))
    #: Exactly as written, ranges and ± included.
    value_original: Mapped[str] = mapped_column(String(64))
    unit_original: Mapped[str] = mapped_column(String(32))
    context_sentence: Mapped[str] = mapped_column(Text)

    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    extraction_source: Mapped[str] = mapped_column(String(8), default="regex")
    status: Mapped[str] = mapped_column(
        String(16), default=QuantityStatus.AUTO, index=True
    )

    model_id: Mapped[str | None] = mapped_column(String(128))
    pipeline_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    __table_args__ = (
        # The range filter's whole life: WHERE kind = ? AND value BETWEEN ? AND ?
        Index("ix_quantities_kind_value", "quantity_kind", "value_si"),
    )
