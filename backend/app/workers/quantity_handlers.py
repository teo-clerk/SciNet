"""The EXTRACT stage: chunks in, quantity rows out, wholesale.

Delete-then-insert by paper, the same rule chunks themselves follow: a
re-parse changes sentence boundaries, and merging would leave rows pointing
at text that no longer exists. Pure CPU at priority 35 — after the embed
that produced the chunks, before the projection the map waits on, and never
in the GPU's way.
"""

from __future__ import annotations

import logging

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Chunk, Job, Paper, Quantity
from app.models.enums import QuantityStatus
from app.services.quantities.extract import extract_from_text

logger = logging.getLogger(__name__)


def handle_extract(session: Session, job: Job, settings: Settings) -> None:
    paper = session.get(Paper, job.paper_id)
    if paper is None:
        raise ValueError(f"paper {job.paper_id} no longer exists")

    chunks = session.scalars(
        select(Chunk).where(Chunk.paper_id == paper.id).order_by(Chunk.ord)
    ).all()

    session.execute(delete(Quantity).where(Quantity.paper_id == paper.id))

    pending = 0
    written = 0
    for chunk in chunks:
        for hit in extract_from_text(chunk.text, chunk.ord, chunk.section):
            session.add(
                Quantity(
                    paper_id=paper.id,
                    chunk_ord=hit.chunk_ord,
                    section=hit.section,
                    quantity_kind=hit.quantity_kind,
                    value_si=hit.value_si,
                    unit_si=hit.unit_si,
                    value_original=hit.value_original,
                    unit_original=hit.unit_original,
                    context_sentence=hit.context_sentence,
                    confidence=hit.confidence,
                    status=hit.status,
                    pipeline_version=settings.pipeline_version if settings else 1,
                )
            )
            written += 1
            if hit.status == QuantityStatus.PENDING_LLM:
                pending += 1

    if pending:
        # Corpus-wide and payload-free, so the standard collapse applies:
        # a 500-paper backfill queues one adjudication pass, not 500.
        from app.models import JobKind
        from app.workers.queue import enqueue

        enqueue(session, JobKind.ADJUDICATE, paper_id=None)

    logger.info(
        "extracted %d quantities from paper %d (%d pending adjudication)",
        written,
        paper.id,
        pending,
    )
