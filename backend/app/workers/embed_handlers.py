"""M2 pipeline stages: embed, project, tag.

Kept apart from the M1 handlers because these are the GPU-resident stages, and
the rule that governs them is different: the worker drains each stage
completely before releasing the model, since an 8 GiB card holds one at a time
and a reload costs more than most of the per-paper work.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.events import BROKER
from app.models import (
    Chunk,
    DocVector,
    Job,
    JobKind,
    MarkdownDoc,
    Paper,
    PaperMeta,
    PaperStatus,
    PaperTag,
)
from app.services.embed import encoder
from app.services.embed.chunker import chunk_markdown, document_text, split_sections
from app.services.embed.store import VectorStore
from app.services.project.pipeline import project_corpus
from app.services.tagging import tagger
from app.services.tagging.canonicalize import (
    approved_slugs,
    register_proposal,
    resolve,
    seed_vocabulary,
)
from app.workers.queue import enqueue

logger = logging.getLogger(__name__)


def store_slug(model_id: str) -> str:
    """A filesystem-safe name for a model's vector store."""
    return re.sub(r"[^a-z0-9]+", "-", model_id.lower()).strip("-")


def open_store(settings: Settings) -> VectorStore:
    """The vector store for the configured embedding model.

    Keyed by model rather than a single shared file. Vectors from two models
    are not comparable, so they cannot share a store — and giving each its own
    means swapping models is reversible: the previous vectors stay on disk and
    switching back costs nothing instead of a full re-embed.
    """
    return VectorStore(
        settings.vectors_dir / f"doc_vectors__{store_slug(settings.embed_model)}",
        dim=settings.embed_dim,
        model_id=settings.embed_model,
    )


def _markdown_for(session: Session, paper_id: int) -> str:
    doc = session.get(MarkdownDoc, paper_id)
    if doc is None:
        return ""
    path = Path(doc.md_path)
    return path.read_text(encoding="utf-8") if path.exists() else ""


def handle_embed(session: Session, job: Job, settings: Settings) -> None:
    """Produce the document vector and the chunk rows for one paper."""
    paper = session.get(Paper, job.paper_id)
    if paper is None:
        raise ValueError(f"paper {job.paper_id} no longer exists")

    meta = session.get(PaperMeta, paper.id)
    markdown = _markdown_for(session, paper.id)

    doc_text = document_text(
        title=meta.title if meta else None,
        abstract=meta.abstract if meta else None,
        summary=meta.summary if meta else None,
        markdown=markdown,
    )
    if not doc_text.strip():
        # Nothing characterises this paper yet; fall back to the body so it can
        # still be placed rather than being invisible on the map.
        doc_text = markdown[:2000]
    if not doc_text.strip():
        raise ValueError(f"paper {paper.id} has no text to embed")

    chunks = chunk_markdown(markdown) if markdown else []

    # Only the document vector is embedded. Chunks are retrieved by BM25 over
    # their text (see the chunks_fts index), and nothing has ever read a chunk
    # vector — they were encoded and dropped on the floor. That was invisible
    # at fifty papers and is not at five hundred with books among them: a
    # single book contributes hundreds of chunks, so this was most of the GPU
    # time in the embed stage, spent on vectors no query could reach.
    doc_vector = encoder.encode_documents([doc_text], settings=settings)[0]

    store = open_store(settings)
    row = store.add(paper.id, doc_vector)
    store.flush()

    existing = session.get(DocVector, paper.id)
    if existing is not None:
        session.delete(existing)
        session.flush()
    session.add(
        DocVector(
            paper_id=paper.id,
            row_idx=row,
            model_id=settings.embed_model,
            dim=int(doc_vector.shape[0]),
        )
    )

    # Chunks are replaced wholesale: a re-parse changes their boundaries, so
    # merging would leave orphans pointing at text that no longer exists.
    session.execute(delete(Chunk).where(Chunk.paper_id == paper.id))
    session.add_all(
        Chunk(
            paper_id=paper.id,
            ord=c.ord,
            section=c.section,
            text=c.text,
            token_count=c.approx_tokens,
        )
        for c in chunks
    )

    paper.status = PaperStatus.EMBEDDED
    session.add(paper)

    enqueue(session, JobKind.PROJECT, paper_id=None)
    enqueue(session, JobKind.TAG, paper_id=paper.id)

    BROKER.publish(
        "embed.done",
        paper_id=paper.id,
        chunks=len(chunks),
    )


def handle_project(session: Session, job: Job, settings: Settings) -> None:
    """Bring the map up to date. Corpus-wide, so it carries no paper_id."""
    store = open_store(settings)
    if store.count == 0:
        logger.info("no vectors yet; nothing to project")
        return

    payload = json.loads(job.payload_json) if job.payload_json else {}
    outcome = project_corpus(
        session, settings, store, force_refit=bool(payload.get("force_refit"))
    )

    logger.info(
        "projection run %d (%s): %d/%d placed, %d clusters%s",
        outcome.run_id,
        outcome.method,
        outcome.placed,
        outcome.total,
        outcome.clusters,
        f", refit because {outcome.reason}" if outcome.refitted else "",
    )
    BROKER.publish(
        "project.done",
        run_id=outcome.run_id,
        refitted=outcome.refitted,
        placed=outcome.placed,
        total=outcome.total,
        clusters=outcome.clusters,
    )


def handle_tag(session: Session, job: Job, settings: Settings) -> None:
    """Summarise and tag one paper against the controlled vocabulary."""
    paper = session.get(Paper, job.paper_id)
    if paper is None:
        raise ValueError(f"paper {job.paper_id} no longer exists")

    seed_vocabulary(session)
    vocabulary = approved_slugs(session)

    meta = session.get(PaperMeta, paper.id) or PaperMeta(paper_id=paper.id)
    markdown = _markdown_for(session, paper.id)
    headings = [s.title for s in split_sections(markdown) if s.title][:20]

    from app.core.model_router import TaskKind
    from app.core.model_router import resolve as resolve_task_model

    tag_model = resolve_task_model(TaskKind.TAG_PAPER, settings, session)
    result = tagger.tag_paper(
        title=meta.title,
        abstract=meta.abstract,
        headings=headings,
        vocabulary=vocabulary,
        settings=settings,
        model=tag_model,
    )

    meta.summary = result["summary"] or meta.summary
    session.add(meta)

    session.execute(delete(PaperTag).where(PaperTag.paper_id == paper.id))
    for slug in result["tags"]:
        tag = resolve(session, slug)
        if tag is not None:
            session.add(
                PaperTag(
                    paper_id=paper.id,
                    tag_id=tag.id,
                    confidence=1.0,
                    source="llm",
                    # Provenance records the model that actually answered —
                    # the routed one, not whatever the settings field says.
                    model_id=tag_model,
                )
            )

    for slug in result["proposed_tags"]:
        register_proposal(session, slug)

    paper.status = PaperStatus.READY
    session.add(paper)
    BROKER.publish(
        "tag.done",
        paper_id=paper.id,
        tags=result["tags"],
        proposed=result["proposed_tags"],
    )


def papers_needing_embedding(session: Session) -> list[int]:
    return list(
        session.scalars(
            select(Paper.id)
            .outerjoin(DocVector, DocVector.paper_id == Paper.id)
            .where(DocVector.paper_id.is_(None))
        )
    )
