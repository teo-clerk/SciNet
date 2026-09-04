"""The plain-English layer: what a work asks, argues and changes.

An abstract is written by a specialist for specialists, and a book has none
at all. This table holds the other reading of the same work — three answers
a curious reader from another field can follow (the central question, the
core argument, why it matters), the handful of claims the text itself makes,
and the names and ideas it turns on. A physics paper with measured values and
a philosophical essay with none both get one row, which is what lets the
inspector show *something* about every document rather than an empty table.

One row per work, replaced wholesale, and never mixed into the document
vector: the plain-English text is a reading of the work, not part of it, and
feeding it to the embedder would move every node on the map.

``grounded`` is the model's own verdict on whether the material it was given
supported its answers. It is declared last in the schema because it assesses
prose that has to exist before it can be assessed — the same reasoning that
puts ``confident`` last in the cluster-naming schema.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.types import UtcDateTime, utcnow


class PaperInsight(Base):
    __tablename__ = "paper_insights"

    paper_id: Mapped[int] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True
    )

    #: What kind of work this is — decided before the prose is written, because
    #: whether "the central question" is a hypothesis, a thesis or a narrative
    #: arc depends on it.
    genre: Mapped[str | None] = mapped_column(String(32))
    question: Mapped[str | None] = mapped_column(Text)
    argument: Mapped[str | None] = mapped_column(Text)
    significance: Mapped[str | None] = mapped_column(Text)
    #: JSON list of short sentences the text itself asserts.
    claims_json: Mapped[str | None] = mapped_column(Text)
    #: JSON list of ``{"name", "kind"}``; kinds are people, works, concepts,
    #: events, places and organisations.
    entities_json: Mapped[str | None] = mapped_column(Text)
    grounded: Mapped[bool] = mapped_column(Boolean, default=True)

    #: Provenance, as every derived row carries: the model that actually
    #: answered (the routed one), and the pipeline that asked.
    model_id: Mapped[str] = mapped_column(String(128))
    pipeline_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
