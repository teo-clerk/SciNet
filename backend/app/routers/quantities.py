"""The quantities API: kinds, ranges, rows with provenance, the review queue.

Filters serve only what earned trust — AUTO and CONFIRMED rows — because a
range filter that silently included rejected or unreviewed guesses would
put wrong numbers behind a UI that looks authoritative. The search endpoint
returns paper ids, mirroring the semantic-search shape: the frontend's
visible-set machinery already knows exactly what to do with a Set of ids.
The rows endpoint returns the measurements themselves, each with the
sentence it came from — what an agent needs, where the map needs ids.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import distinct, func, or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import PaperMeta, Quantity, QuantityStatus
from app.services.quantities.extract import CANONICAL, UNITS

router = APIRouter(prefix="/api/quantities", tags=["quantities"])

TRUSTED = (QuantityStatus.AUTO, QuantityStatus.CONFIRMED)
#: Rows per answer. An agent asking for "every frequency" gets the count and
#: the first page, not sixteen thousand sentences.
MAX_ROWS = 100


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


class RowsOut(BaseModel):
    #: Rows matching before the limit, and how many distinct papers hold them.
    count: int
    papers: int
    rows: list[ReviewRow]


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


def kind_for_unit(unit: str) -> str | None:
    """The kind a unit names, when the allowlist knows it.

    "Hz" and "kHz" are frequency by the extractor's own table; "hertz" is the
    SI name the rows carry. Anything else is unknown, and the caller decides.
    """
    if unit in UNITS:
        return UNITS[unit][1]
    lowered = unit.lower()
    for token, (_, family) in UNITS.items():
        if token.lower() == lowered:
            return family
    for kind, si_unit in CANONICAL.items():
        if si_unit == lowered:
            return kind
    return None


@router.get("/rows", response_model=RowsOut)
def rows(
    kind: str | None = Query(None),
    unit: str | None = Query(None),
    min_value: float | None = Query(None),
    max_value: float | None = Query(None),
    q: str | None = Query(None, min_length=2),
    limit: int = Query(20, ge=1, le=MAX_ROWS),
    db: Session = Depends(get_db),
) -> RowsOut:
    """Trusted measured values, each with the sentence it came from.

    ``min_value`` and ``max_value`` are in the kind's SI unit; every row says
    which in ``unit_si``. A unit token ("kHz") narrows to values written in
    that token, an SI name ("hertz") selects the whole kind, and when ``kind``
    is not given either one chooses it. ``q`` matches the sentence, case
    aside. At least one of kind, unit or phrase is required.
    """
    if kind is None and unit is not None:
        kind = kind_for_unit(unit)
    if kind is None and unit is None and q is None:
        raise HTTPException(400, "give a kind, a unit or a phrase")

    conditions = [Quantity.status.in_(TRUSTED), Quantity.value_si.is_not(None)]
    if kind is not None:
        conditions.append(Quantity.quantity_kind == kind)
    if unit is not None:
        token = unit.lower()
        conditions.append(
            or_(
                func.lower(Quantity.unit_original) == token,
                func.lower(Quantity.unit_si) == token,
            )
        )
    if min_value is not None:
        conditions.append(Quantity.value_si >= min_value)
    if max_value is not None:
        conditions.append(Quantity.value_si <= max_value)
    if q is not None:
        conditions.append(
            func.lower(Quantity.context_sentence).contains(q.lower(), autoescape=True)
        )

    count, papers = db.execute(
        select(func.count(Quantity.id), func.count(distinct(Quantity.paper_id))).where(
            *conditions
        )
    ).one()
    matched = db.execute(
        select(Quantity, PaperMeta.title)
        .outerjoin(PaperMeta, PaperMeta.paper_id == Quantity.paper_id)
        .where(*conditions)
        .order_by(Quantity.value_si, Quantity.id)
        .limit(limit)
    ).all()
    return RowsOut(
        count=count or 0,
        papers=papers or 0,
        rows=[
            ReviewRow(**_row(qty).model_dump(), paper_title=title)
            for qty, title in matched
        ],
    )


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
