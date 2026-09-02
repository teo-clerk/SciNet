"""The quantities table round-trips, and its verdicts have a vocabulary."""

from __future__ import annotations

from sqlalchemy import select

from app.models import Paper, Quantity, QuantityStatus
from app.models.enums import PaperStatus


def test_a_quantity_round_trips_with_its_provenance(db, tmp_path) -> None:
    db.add(
        Paper(
            id=1,
            content_sha256="1" * 64,
            work_key="w1",
            pdf_path=str(tmp_path / "1.pdf"),
            status=PaperStatus.READY,
        )
    )
    db.add(
        Quantity(
            paper_id=1,
            chunk_ord=3,
            section="Results",
            quantity_kind="length",
            value_si=500.0,
            unit_si="meter",
            value_original="0.5",
            unit_original="km",
            context_sentence="The swath width is 0.5 km at nadir.",
            confidence=0.95,
        )
    )
    db.flush()

    row = db.scalars(select(Quantity)).one()
    assert row.status == QuantityStatus.AUTO
    assert row.value_si == 500.0 and row.unit_si == "meter"
    assert "swath width" in row.context_sentence
    assert row.extraction_source == "regex"


def test_an_ambiguous_hit_may_hold_no_si_value(db, tmp_path) -> None:
    """A wrong unit is worse than a missing one: pending rows record the
    verbatim text and wait for adjudication instead of guessing."""
    db.add(
        Paper(
            id=2,
            content_sha256="2" * 64,
            work_key="w2",
            pdf_path=str(tmp_path / "2.pdf"),
            status=PaperStatus.READY,
        )
    )
    db.add(
        Quantity(
            paper_id=2,
            chunk_ord=0,
            section=None,
            quantity_kind="unknown",
            value_si=None,
            unit_si=None,
            value_original="12",
            unit_original="RJ",
            context_sentence="The orbit reaches 12 RJ from the planet.",
            confidence=0.4,
            status=QuantityStatus.PENDING_LLM,
        )
    )
    db.flush()
    pending = db.scalars(
        select(Quantity).where(Quantity.status == QuantityStatus.PENDING_LLM)
    ).one()
    assert pending.value_si is None and pending.unit_original == "RJ"
