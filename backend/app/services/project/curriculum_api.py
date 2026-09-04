"""The entry-point ranking, fed from the store and the database.

``curriculum`` is pure so it can be tested on hand-built vectors; this is the
thin layer that fetches the real ones. It is deliberately the only place that
knows both what a ``Candidate`` needs and where each field lives.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Paper, PaperMeta
from app.services.project.curriculum import (
    MAX_READING_ORDER,
    Candidate,
    Scored,
    rank_entry_points,
    reading_order,
)

#: Runners-up shown beside the winner. Two is enough for a reader to see the
#: choice was close (or not) without turning the suggestion back into a list.
ALTERNATIVES = 2


@dataclass(frozen=True)
class EntryPoint:
    paper_id: int
    title: str | None
    year: int | None
    score: float
    reasons: list[str]


@dataclass(frozen=True)
class Curriculum:
    entry_point: EntryPoint | None
    alternatives: list[EntryPoint]
    reading_order: list[int]
    #: How many of the requested papers actually had a vector. A set of forty
    #: with two embedded is not a ranking of forty, and the reader should know.
    considered: int


def _empty(considered: int) -> Curriculum:
    return Curriculum(
        entry_point=None, alternatives=[], reading_order=[], considered=considered
    )


def _candidates(db: Session, ids: Sequence[int]) -> list[Candidate]:
    rows = db.execute(
        select(
            Paper.id,
            Paper.page_count,
            PaperMeta.title,
            PaperMeta.abstract,
            PaperMeta.year,
        )
        .outerjoin(PaperMeta, PaperMeta.paper_id == Paper.id)
        .where(Paper.id.in_(list(ids)))
    ).all()
    by_id = {row.id: row for row in rows}

    def candidate(pid: int) -> Candidate:
        row = by_id.get(pid)
        # A vector whose Paper row has gone is still a point in the set; it
        # ranks on centrality alone rather than being dropped and skewing the
        # centre.
        if row is None:
            return Candidate(pid, None, None, None, None)
        return Candidate(pid, row.title, row.abstract, row.year, row.page_count)

    return [candidate(pid) for pid in ids]


def _entry_point(scored: Scored, candidate: Candidate) -> EntryPoint:
    return EntryPoint(
        paper_id=scored.paper_id,
        title=candidate.title,
        year=candidate.year,
        score=scored.score,
        reasons=list(scored.reasons),
    )


def curriculum_for(
    db: Session,
    settings: Settings,
    paper_ids: Sequence[int],
    *,
    limit: int = MAX_READING_ORDER,
) -> Curriculum:
    """Entry point, runners-up and a reading order for a set of papers.

    Only papers with a vector take part: the ranking is a statement about
    where each sits relative to the others, and a paper with no position has
    nothing to say. Fewer than two leaves nothing to rank.
    """
    from app.workers.embed_handlers import open_store

    store = open_store(settings)
    matrix, ids = store.matrix(list(paper_ids))
    if len(ids) < 2:
        return _empty(len(ids))

    candidates = _candidates(db, ids)
    by_id = {c.paper_id: c for c in candidates}
    scored = rank_entry_points(candidates, matrix)
    order = reading_order(candidates, matrix, limit=limit)

    return Curriculum(
        entry_point=_entry_point(scored[0], by_id[scored[0].paper_id]),
        alternatives=[
            _entry_point(s, by_id[s.paper_id]) for s in scored[1 : 1 + ALTERNATIVES]
        ],
        reading_order=order,
        considered=len(ids),
    )
