"""Regions of the map, and how they relate.

The graph payload carries only what the renderer needs. Everything here is
fetched when a reader actually asks about a region, which keeps the cost off
the initial load.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import Cluster, ClusterLink, Paper, PaperMeta, Projection, ProjectionRun

router = APIRouter(prefix="/api/clusters", tags=["clusters"])


class ClusterMember(BaseModel):
    paper_id: int
    title: str | None
    year: int | None
    first_author: str | None
    confidence: float | None


class ClusterDetail(BaseModel):
    id: int
    label: str | None
    overview: str | None
    size: int
    terms: list[str]
    members: list[ClusterMember]


class LinkSummary(BaseModel):
    source_id: int
    target_id: int
    source_label: str | None
    target_label: str | None
    similarity: float
    shared_terms: list[str]
    summary: str | None
    #: The papers that span the two regions — the evidence for the claim.
    bridge_papers: list[ClusterMember]


def _active_run(db: Session) -> ProjectionRun:
    run = db.scalar(select(ProjectionRun).where(ProjectionRun.is_active.is_(True)))
    if run is None:
        raise HTTPException(409, "no projection has been computed yet")
    return run


def _first_author(meta: PaperMeta | None) -> str | None:
    if meta is None or not meta.authors_json:
        return None
    try:
        authors = json.loads(meta.authors_json)
    except json.JSONDecodeError:
        return None
    return authors[0] if authors else None


def _members(
    db: Session, run_id: int, paper_ids: list[int] | None, cluster_id: int | None
) -> list[ClusterMember]:
    query = (
        select(Projection.paper_id, Projection.cluster_probability, PaperMeta, Paper)
        .outerjoin(PaperMeta, PaperMeta.paper_id == Projection.paper_id)
        .outerjoin(Paper, Paper.id == Projection.paper_id)
        .where(Projection.run_id == run_id)
    )
    query = (
        query.where(Projection.cluster_id == cluster_id)
        if cluster_id is not None
        else query.where(Projection.paper_id.in_(paper_ids or []))
    )

    members = [
        ClusterMember(
            paper_id=paper_id,
            title=meta.title if meta else None,
            year=meta.year if meta else None,
            first_author=_first_author(meta),
            confidence=round(probability, 3) if probability is not None else None,
        )
        for paper_id, probability, meta, _paper in db.execute(query).all()
    ]
    # Most representative first: the reader wants the centre of the region
    # before its edges.
    members.sort(key=lambda m: (-(m.confidence or 0.0), (m.title or "").lower()))
    return members


@router.get("/{cluster_id}", response_model=ClusterDetail)
def get_cluster(cluster_id: int, db: Session = Depends(get_db)) -> ClusterDetail:
    cluster = db.get(Cluster, cluster_id)
    if cluster is None:
        raise HTTPException(404, "no such cluster")

    return ClusterDetail(
        id=cluster.id,
        label=cluster.llm_label,
        overview=cluster.llm_overview,
        size=cluster.size,
        terms=json.loads(cluster.top_terms_json or "[]"),
        members=_members(db, cluster.run_id, None, cluster.id),
    )


@router.get("", response_model=list[LinkSummary])
def get_relationships(db: Session = Depends(get_db)) -> list[LinkSummary]:
    """Every recorded bridge between regions, strongest first."""
    run = _active_run(db)

    links = db.scalars(
        select(ClusterLink)
        .where(ClusterLink.run_id == run.id)
        .order_by(ClusterLink.similarity.desc())
    ).all()
    if not links:
        return []

    labels = {
        cluster.id: cluster.llm_label
        for cluster in db.scalars(select(Cluster).where(Cluster.run_id == run.id))
    }

    return [
        LinkSummary(
            source_id=link.source_id,
            target_id=link.target_id,
            source_label=labels.get(link.source_id),
            target_label=labels.get(link.target_id),
            similarity=link.similarity,
            shared_terms=json.loads(link.shared_terms or "[]"),
            summary=link.llm_summary,
            bridge_papers=_members(
                db, run.id, json.loads(link.bridge_paper_ids or "[]"), None
            ),
        )
        for link in links
    ]
