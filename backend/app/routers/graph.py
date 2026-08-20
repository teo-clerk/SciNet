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
from app.models import Cluster, PaperMeta, PaperTag, Projection, ProjectionRun, Tag

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
            PaperMeta.title,
            PaperMeta.year,
        )
        .outerjoin(PaperMeta, PaperMeta.paper_id == Projection.paper_id)
        .where(Projection.run_id == run.id)
        .order_by(Projection.paper_id)
    ).all()
    if not rows:
        raise HTTPException(409, "the active projection has no coordinates")

    # Keyed on the run and its size: a refit or an incremental insert both
    # change it, and nothing else needs to.
    etag = f'W/"run-{run.id}-{len(rows)}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})

    positions = np.array([[r.x, r.y, r.z] for r in rows], dtype=np.float32)

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
