"""The graph payload, and semantic search.

Positions go out as a raw Float32Array rather than JSON. For 4,000 nodes that
is 48 KB against roughly 20 MB of JSON, and the browser can hand the buffer
straight to a BufferAttribute with no parsing step.

Metadata is deliberately thin — id, title, cluster, tags as integer ids, year.
Abstracts and summaries are fetched per node on click; sending them for the
whole corpus is what turns a 50 ms load into a multi-second one.

The response carries an ETag keyed to the active projection run, so reopening
the app is a 304 and costs nothing.
"""

from __future__ import annotations

import base64
import json

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.models import (
    Cluster,
    JobKind,
    JobState,
    Paper,
    PaperMeta,
    PaperTag,
    Projection,
    ProjectionRun,
    Tag,
)
from app.services.project.skeleton import load as load_skeleton
from app.workers.queue import enqueue


def _projection_base(settings: Settings, run_id: int):
    return settings.models_dir / "projections" / f"run_{run_id:05d}"


router = APIRouter(prefix="/api/graph", tags=["graph"])


def _active_run(db: Session) -> ProjectionRun:
    run = db.scalar(select(ProjectionRun).where(ProjectionRun.is_active.is_(True)))
    if run is None:
        raise HTTPException(409, "no projection has been computed yet")
    return run


@router.get("")
def get_graph(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    run = _active_run(db)

    rows = db.execute(
        select(
            Projection.paper_id,
            Projection.x,
            Projection.y,
            Projection.z,
            Projection.cluster_id,
            Projection.is_transformed,
            Projection.off_manifold,
            Projection.cluster_probability,
            PaperMeta.title,
            PaperMeta.year,
            Paper.page_count.label("pages"),
        )
        .outerjoin(PaperMeta, PaperMeta.paper_id == Projection.paper_id)
        .outerjoin(Paper, Paper.id == Projection.paper_id)
        .where(Projection.run_id == run.id)
        .order_by(Projection.paper_id)
    ).all()
    if not rows:
        raise HTTPException(409, "the active projection has no coordinates")

    # Keyed on the run and its size: a refit or an incremental insert both
    # change it, and nothing else needs to.
    positions = np.array([[r.x, r.y, r.z] for r in rows], dtype=np.float32)

    # The static skeleton, remapped from paper ids to the node indices the
    # client actually holds. Sent as uint32 pairs: at 4,000 papers that is
    # ~48 KB against ~200 KB of the equivalent JSON, and it lands straight in
    # a BufferAttribute.
    index_of = {row.paper_id: i for i, row in enumerate(rows)}
    edges = load_skeleton(_projection_base(settings, run.id))
    pairs = [
        (index_of[a], index_of[b])
        for a, b in edges.tolist()
        if a in index_of and b in index_of
    ]
    skeleton = (
        np.array(pairs, dtype=np.uint32).ravel()
        if pairs
        else np.zeros(0, dtype=np.uint32)
    )

    etag = f'W/"run-{run.id}-{len(rows)}-s{len(pairs)}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})

    tag_rows = db.execute(
        select(PaperTag.paper_id, Tag.slug)
        .join(Tag, Tag.id == PaperTag.tag_id)
        .where(PaperTag.paper_id.in_([r.paper_id for r in rows]))
    ).all()
    vocabulary: list[str] = sorted({slug for _, slug in tag_rows})
    tag_index = {slug: i for i, slug in enumerate(vocabulary)}
    by_paper: dict[int, list[int]] = {}
    for paper_id, slug in tag_rows:
        by_paper.setdefault(paper_id, []).append(tag_index[slug])

    clusters = [
        {
            "id": c.id,
            "label": c.llm_label,
            "size": c.size,
            "terms": json.loads(c.top_terms_json or "[]"),
        }
        for c in db.scalars(select(Cluster).where(Cluster.run_id == run.id))
    ]

    payload = {
        "run_id": run.id,
        "method": run.method,
        "count": len(rows),
        "positions_f32": base64.b64encode(positions.tobytes()).decode("ascii"),
        "skeleton_u32": base64.b64encode(skeleton.tobytes()).decode("ascii"),
        "skeleton_edges": len(pairs),
        "tag_vocabulary": vocabulary,
        "clusters": clusters,
        "nodes": [
            {
                "id": r.paper_id,
                "title": r.title,
                "year": r.year,
                "cluster": r.cluster_id,
                "tags": by_paper.get(r.paper_id, []),
                # Surfaced so the UI can mark provisionally-placed papers.
                "pages": r.pages,
                # HDBSCAN membership strength, so the list can show how firmly
                # a paper belongs where it was put.
                "confidence": (
                    round(r.cluster_probability, 3)
                    if r.cluster_probability is not None
                    else None
                ),
                "provisional": bool(r.is_transformed),
                "drift": round(r.off_manifold or 0.0, 2),
            }
            for r in rows
        ],
    }
    return Response(
        content=json.dumps(payload),
        media_type="application/json",
        headers={"ETag": etag, "Cache-Control": "no-cache"},
    )


@router.post("/reproject")
def request_reprojection(db: Session = Depends(get_db)) -> dict[str, object]:
    """Ask for the map to be rebuilt from scratch.

    Enqueues rather than computes. A refit over a few thousand vectors is tens
    of seconds of UMAP followed by clustering and a round of LLM naming — far
    past what a request should hold open, and it belongs to the worker anyway,
    which is the sole writer of paper data.

    Idempotent by construction: ``enqueue`` collapses onto an outstanding job
    of the same kind, so a reader who presses the button four times gets one
    refit rather than four.
    """
    job = enqueue(db, JobKind.PROJECT, paper_id=None, payload={"force_refit": True})
    db.commit()

    running = job.state == JobState.RUNNING
    return {
        "job_id": job.id,
        "state": str(job.state),
        # So the interface can say "already under way" rather than implying it
        # started something, which is the difference between a button that
        # works and one the reader presses again.
        "already_queued": running or job.attempts > 0,
    }


@router.get("/similar/{paper_id}")
def similar_papers(
    paper_id: int,
    k: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    """Nearest neighbours in the *embedding* space.

    Not the rendered coordinates. UMAP distorts global distance on purpose, so
    "near on screen" and "semantically similar" are different questions and only
    the original space answers the second one.
    """
    from app.workers.embed_handlers import open_store

    store = open_store(settings)
    query = store.get(paper_id)
    if query is None:
        raise HTTPException(404, "this paper has no embedding yet")

    hits = store.nearest(query, k=k, exclude={paper_id})
    titles = dict(
        db.execute(
            select(PaperMeta.paper_id, PaperMeta.title).where(
                PaperMeta.paper_id.in_([pid for pid, _ in hits])
            )
        ).all()
    )
    return [
        {"id": pid, "title": titles.get(pid), "similarity": round(score, 4)}
        for pid, score in hits
    ]
