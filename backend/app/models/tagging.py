"""Controlled tag vocabulary and paper-tag assignments.

The vocabulary is deliberately closed: an unconstrained LLM produces
"deep learning" / "Deep Learning" / "DL" / "deep-learning" as four distinct
tags within fifty papers. New tags land in ``pending_review`` and are merged
into a canonical tag when their label embeddings are close enough.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.core.types import UtcDateTime, utcnow
from app.models.enums import TagStatus


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str | None] = mapped_column(String(16))

    # Non-null => this tag is an alias of another. Queries resolve through it.
    canonical_id: Mapped[int | None] = mapped_column(ForeignKey("tags.id"))
    status: Mapped[str] = mapped_column(
        String(16), default=TagStatus.APPROVED, index=True
    )
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    canonical: Mapped[Tag | None] = relationship(remote_side=[id])


class PaperTag(Base):
    __tablename__ = "paper_tags"

    paper_id: Mapped[int] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )
    confidence: Mapped[float | None] = mapped_column()
    source: Mapped[str | None] = mapped_column(String(32))
    model_id: Mapped[str | None] = mapped_column(String(128))
