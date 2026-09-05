"""The graph payload, and semantic search.

Positions go out as a raw Float32Array rather than JSON. For 4,000 nodes that
is 48 KB against roughly 20 MB of JSON, and the browser can hand the buffer
straight to a BufferAttribute with no parsing step.

Metadata is deliberately thin — id, title, cluster, tags as integer ids, year.
Abstracts and summaries are fetched per node on click; sending them for the
whole corpus is what turns a 50 ms load into a multi-second one.

The response carries an ETag keyed to the active projection run, so reopening
the app is a 304 and costs nothing.

Two questions about the map that are not the map itself also live here: where
to start reading an arbitrary set of papers, and the trail of papers that leads
from one to another. Both run in the embedding space, never on x/y/z.
"""

from __future__ import annotations

import base64
import json

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.core.warmup import WARMER, WarmupState
from app.models import (
    Cluster,
    JobKind,
    JobState,
    Paper,
    PaperInsight,
    PaperMeta,
    PaperTag,
    Projection,
    ProjectionRun,
    Tag,
)
from app.routers.clusters import EntryPoint, entry_point_out
from app.services.project import curriculum_api
from app.services.project.curriculum import MAX_READING_ORDER
from app.services.project.paths import MAX_STOPS, KnnGraph, find_path, graph_for, thin
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


@router.get("/runs")
def list_runs(db: Session = Depends(get_db)) -> list[dict]:
    """Every retained projection run — the morph view's picker.

    Inactive runs are kept on purpose (rollback, and A/B-ing two embedding
    models); this is where keeping them starts paying rent.
    """
    runs = db.scalars(select(ProjectionRun).order_by(ProjectionRun.id.desc())).all()
    return [
        {
            "id": r.id,
            "model_id": r.model_id,
            "method": r.method,
            "is_active": r.is_active,
            "n_fit": r.n_fit,
            "fitted_at": r.fitted_at.isoformat() if r.fitted_at else None,
        }
        for r in runs
    ]


@router.get("")
def get_graph(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    run_id: int | None = Query(
        None, description="serve a retained run instead of the active one"
    ),
) -> Response:
    if run_id is None:
        run = _active_run(db)
    else:
        run = db.get(ProjectionRun, run_id)
        if run is None:
            raise HTTPException(404, "no such projection run")

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


# --- where do I start? -------------------------------------------------------


class EntryPointRequest(BaseModel):
    paper_ids: list[int] = Field(max_length=2000)
    limit: int = Field(MAX_READING_ORDER, ge=1, le=50)


class CurriculumOut(BaseModel):
    entry_point: EntryPoint | None
    alternatives: list[EntryPoint]
    reading_order: list[int]
    considered: int


@router.post("/entry-point", response_model=CurriculumOut)
def entry_point_for_set(
    body: EntryPointRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CurriculumOut:
    """Where to start reading an arbitrary set of papers.

    A cluster gets this for free from its own endpoint; this is for every other
    set a reader assembles — a search result, a lasso selection, a tag. The
    ranking is the same one, so the answer does not change with how the set
    was drawn.
    """
    if not body.paper_ids:
        raise HTTPException(400, "give at least one paper id")

    curriculum = curriculum_api.curriculum_for(
        db, settings, body.paper_ids, limit=body.limit
    )
    return CurriculumOut(
        entry_point=(
            entry_point_out(curriculum.entry_point)
            if curriculum.entry_point is not None
            else None
        ),
        alternatives=[entry_point_out(p) for p in curriculum.alternatives],
        reading_order=curriculum.reading_order,
        considered=curriculum.considered,
    )


# --- idea trails -------------------------------------------------------------


class PathEnd(BaseModel):
    paper_id: int
    #: The phrase this end was typed as, when it was anchored to its nearest
    #: paper rather than given by id. Null for an id.
    anchored_from_text: str | None = None


class PathStop(BaseModel):
    paper_id: int
    title: str | None
    year: int | None
    cluster_id: int | None
    cluster_label: str | None
    #: Cosine to the following stop, so the reader sees which step is the
    #: leap. Null on the last stop.
    similarity_to_next: float | None
    #: The work's central question in plain English, when the INSIGHT stage
    #: has written it — a trail of titles alone asks the reader to guess what
    #: each step is about.
    core_question: str | None = None


class PathOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: PathEnd = Field(alias="from")
    to: PathEnd
    complete: bool
    hops: int
    #: True when the raw trail was longer than MAX_STOPS and has been sampled.
    thinned: bool
    stops: list[PathStop]


def _require_warm_encoder() -> None:
    """Refuse to embed a phrase until the model is loaded.

    A twin of the gate in ``routers.search.search_semantic``, kept in step by
    hand rather than shared: the two routers are owned separately and the
    block is short. The three answers are deliberately distinct — "still
    loading" is not "unavailable", and neither is "no results".
    """
    status = WARMER.status()
    if status.state is WarmupState.FAILED:
        raise HTTPException(
            503,
            {
                "state": "failed",
                "message": f"the embedding model could not be loaded: {status.error}",
            },
        )
    if not WARMER.is_ready:
        WARMER.start()  # no-op if already warming; recovers a missed dispatch
        raise HTTPException(
            503,
            {
                "state": "warming",
                "message": "the search engine is still starting up",
                "elapsed_seconds": round(status.elapsed_seconds, 1),
                "estimated_remaining": (
                    round(status.estimated_remaining, 1)
                    if status.estimated_remaining is not None
                    else None
                ),
            },
        )


def _anchor(text: str, store, settings: Settings) -> int:
    """The paper nearest a phrase — how a typed end becomes a node."""
    from app.services.embed import encoder

    _require_warm_encoder()
    try:
        vector = encoder.encode_query(text, settings=settings)
    except Exception as exc:  # noqa: BLE001 - model absent or not yet downloaded
        raise HTTPException(
            503,
            {"state": "failed", "message": f"the embedding model failed: {exc}"},
        ) from exc

    hits = store.nearest(vector, k=1)
    if not hits:
        raise HTTPException(404, "nothing in the library has an embedding yet")
    return hits[0][0]


def _resolve_end(
    paper_id: int | None, text: str | None, store, settings: Settings
) -> PathEnd:
    """One end of a trail as a node. An id wins over text when both are given."""
    if paper_id is not None:
        if store.get(paper_id) is None:
            raise HTTPException(404, f"paper {paper_id} has no embedding yet")
        return PathEnd(paper_id=paper_id)
    assert text is not None  # presence was checked before either end resolved
    return PathEnd(paper_id=_anchor(text, store, settings), anchored_from_text=text)


def _describe_stops(db: Session, graph: KnnGraph, stops: list[int]) -> list[PathStop]:
    run = db.scalar(select(ProjectionRun).where(ProjectionRun.is_active.is_(True)))
    # A trail does not need a map to exist; without one the cluster fields are
    # simply empty rather than the request failing.
    run_id = run.id if run is not None else -1
    rows = db.execute(
        select(
            Paper.id,
            PaperMeta.title,
            PaperMeta.year,
            Projection.cluster_id,
            Cluster.llm_label,
            PaperInsight.question,
        )
        .outerjoin(PaperMeta, PaperMeta.paper_id == Paper.id)
        .outerjoin(
            Projection,
            and_(Projection.paper_id == Paper.id, Projection.run_id == run_id),
        )
        .outerjoin(Cluster, Cluster.id == Projection.cluster_id)
        .outerjoin(PaperInsight, PaperInsight.paper_id == Paper.id)
        .where(Paper.id.in_(stops))
    ).all()
    by_id = {row.id: row for row in rows}

    described: list[PathStop] = []
    for position, pid in enumerate(stops):
        row = by_id.get(pid)
        following = stops[position + 1] if position + 1 < len(stops) else None
        similarity = (
            float(
                graph.unit[graph.index_of[pid]] @ graph.unit[graph.index_of[following]]
            )
            if following is not None
            else None
        )
        described.append(
            PathStop(
                paper_id=pid,
                title=row.title if row else None,
                year=row.year if row else None,
                cluster_id=row.cluster_id if row else None,
                cluster_label=row.llm_label if row else None,
                similarity_to_next=round(similarity, 4)
                if similarity is not None
                else None,
                core_question=row.question if row else None,
            )
        )
    return described


@router.get("/path", response_model=PathOut)
def idea_trail(
    from_id: int | None = Query(None, alias="from"),
    to_id: int | None = Query(None, alias="to"),
    from_text: str | None = Query(None, min_length=2),
    to_text: str | None = Query(None, min_length=2),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PathOut:
    """The chain of papers from one idea to another.

    Each end is a paper id or a phrase; a phrase is anchored to its nearest
    paper first, and the response says so, because "from Ethics" is a claim
    about the reader's words and "from paper 12" is a claim about the corpus.
    """
    from app.workers.embed_handlers import open_store

    if (from_id is None and from_text is None) or (to_id is None and to_text is None):
        raise HTTPException(400, "each end of a trail needs a paper id or a phrase")

    store = open_store(settings)
    source = _resolve_end(from_id, from_text, store, settings)
    target = _resolve_end(to_id, to_text, store, settings)
    if source.paper_id == target.paper_id:
        raise HTTPException(400, "both ends of the trail are the same paper")

    graph = graph_for(store, settings)
    result = find_path(graph, source.paper_id, target.paper_id)
    thinned = len(result.stops) > MAX_STOPS
    stops = thin(result.stops) if thinned else list(result.stops)

    return PathOut(
        from_=source,
        to=target,
        complete=result.complete,
        hops=result.hops,
        thinned=thinned,
        stops=_describe_stops(db, graph, stops),
    )
