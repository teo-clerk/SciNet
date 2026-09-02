"""The quantities API: kinds, ranges, per-paper rows, and the review queue.

Filters serve only what earned trust — AUTO and CONFIRMED rows — because a
range filter that silently included rejected or unreviewed guesses would
put wrong numbers behind a UI that looks authoritative. The search endpoint
returns paper ids, mirroring the semantic-search shape: the frontend's
visible-set machinery already knows exactly what to do with a Set of ids.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import PaperMeta, Quantity, QuantityStatus

router = APIRouter(prefix="/api/quantities", tags=["quantities"])

TRUSTED = (QuantityStatus.AUTO, QuantityStatus.CONFIRMED)


class KindSummary(BaseModel):
    kind: str
    unit_si: str | None
    count: int
    min_value: float
    max_value: float


class QuantityRow(BaseModel):
    id: int
    paper_id: int
    quantity_kind: str
    value_si: float | None
    unit_si: str | None
    value_original: str
    unit_original: str
    context_sentence: str
    section: str | None
    confidence: float
    extraction_source: str
    status: str


class ReviewRow(QuantityRow):
    paper_title: str | None


class ReviewRequest(BaseModel):
    verdict: str  # confirm | reject


def _row(q: Quantity) -> QuantityRow:
    return QuantityRow(
        id=q.id,
        paper_id=q.paper_id,
        quantity_kind=q.quantity_kind,
        value_si=q.value_si,
        unit_si=q.unit_si,
        value_original=q.value_original,
        unit_original=q.unit_original,
        context_sentence=q.context_sentence,
        section=q.section,
        confidence=q.confidence,
        extraction_source=q.extraction_source,
        status=q.status,
    )


@router.get("/kinds", response_model=list[KindSummary])
def kinds(db: Session = Depends(get_db)) -> list[KindSummary]:
    rows = db.execute(
        select(
            Quantity.quantity_kind,
            Quantity.unit_si,
            func.count(Quantity.id),
            func.min(Quantity.value_si),
            func.max(Quantity.value_si),
        )
        .where(Quantity.status.in_(TRUSTED), Quantity.value_si.is_not(None))
        .group_by(Quantity.quantity_kind)
        .order_by(func.count(Quantity.id).desc())
    ).all()
    return [
        KindSummary(kind=kind, unit_si=unit, count=count, min_value=low, max_value=high)
        for kind, unit, count, low, high in rows
    ]


@router.get("/search")
def search(
    kind: str = Query(...),
    min_value: float | None = Query(None),
    max_value: float | None = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """Papers holding a trusted value of this kind inside the range."""
    statement = select(Quantity.paper_id).where(
        Quantity.quantity_kind == kind,
        Quantity.status.in_(TRUSTED),
        Quantity.value_si.is_not(None),
    )
    if min_value is not None:
        statement = statement.where(Quantity.value_si >= min_value)
    if max_value is not None:
        statement = statement.where(Quantity.value_si <= max_value)
    paper_ids = sorted({pid for pid in db.scalars(statement)})
    return {"kind": kind, "paper_ids": paper_ids, "count": len(paper_ids)}


@router.get("/paper/{paper_id}", response_model=list[QuantityRow])
def for_paper(paper_id: int, db: Session = Depends(get_db)) -> list[QuantityRow]:
    rows = db.scalars(
        select(Quantity)
        .where(
            Quantity.paper_id == paper_id,
            Quantity.status.in_(
                (*TRUSTED, QuantityStatus.PENDING_REVIEW, QuantityStatus.PENDING_LLM)
            ),
        )
        .order_by(Quantity.chunk_ord, Quantity.id)
    ).all()
    return [_row(q) for q in rows]


@router.get("/review", response_model=list[ReviewRow])
def review_queue(
    db: Session = Depends(get_db), limit: int = Query(100, ge=1, le=500)
) -> list[ReviewRow]:
    rows = db.execute(
        select(Quantity, PaperMeta.title)
        .outerjoin(PaperMeta, PaperMeta.paper_id == Quantity.paper_id)
        .where(Quantity.status == QuantityStatus.PENDING_REVIEW)
        .order_by(Quantity.id)
        .limit(limit)
    ).all()
    return [ReviewRow(**_row(q).model_dump(), paper_title=title) for q, title in rows]


@router.post("/{quantity_id}/review")
def review(
    quantity_id: int, body: ReviewRequest, db: Session = Depends(get_db)
) -> dict:
    row = db.get(Quantity, quantity_id)
    if row is None:
        raise HTTPException(404, "no such quantity")
    if body.verdict == "confirm":
        row.status = QuantityStatus.CONFIRMED
    elif body.verdict == "reject":
        row.status = QuantityStatus.REJECTED
    else:
        raise HTTPException(400, "verdict must be confirm or reject")
    db.commit()
    return {"id": row.id, "status": row.status}
