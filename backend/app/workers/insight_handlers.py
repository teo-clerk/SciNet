"""The INSIGHT stage: one work in, its plain-English reading out.

Kept apart from ``embed_handlers`` for the same reason the quantity stage is:
that module is the GPU-resident core of the pipeline and every stage added to
it makes the rule it exists to state harder to see. This one is small — read
what the metadata and parse stages left, ask the model once, replace the row.

Runs after TAG in the same LLM-resident era (priority 101), so the card is not
reloaded to write it and the map is already navigable when it lands. It never
touches ``paper.status``: the tagging stage owns READY, and a work whose
plain-English reading failed is still a work on the map.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.events import BROKER
from app.models import Job, MarkdownDoc, Paper, PaperInsight, PaperMeta
from app.services.embed.chunker import split_sections
from app.services.insight.distill import distill_paper
from app.services.metadata.synopsis import opening_excerpt

logger = logging.getLogger(__name__)

MAX_HEADINGS = 20


def _markdown_for(session: Session, paper_id: int) -> str:
    doc = session.get(MarkdownDoc, paper_id)
    if doc is None:
        return ""
    path = Path(doc.md_path)
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _authors(meta: PaperMeta | None) -> list[str]:
    if meta is None or not meta.authors_json:
        return []
    try:
        authors = json.loads(meta.authors_json)
    except json.JSONDecodeError:
        return []
    return [str(a) for a in authors if a]


def handle_insight(session: Session, job: Job, settings: Settings) -> None:
    """Write the plain-English reading of one work, replacing any earlier one."""
    paper = session.get(Paper, job.paper_id)
    if paper is None:
        raise ValueError(f"paper {job.paper_id} no longer exists")

    meta = session.get(PaperMeta, paper.id)
    markdown = _markdown_for(session, paper.id)
    headings = [s.title for s in split_sections(markdown) if s.title][:MAX_HEADINGS]

    from app.core.model_router import TaskKind
    from app.core.model_router import resolve as resolve_task_model

    model = resolve_task_model(TaskKind.INSIGHT, settings, session)
    reading = distill_paper(
        title=meta.title if meta else None,
        authors=_authors(meta),
        year=meta.year if meta else None,
        abstract=meta.abstract if meta else None,
        headings=headings,
        excerpt=opening_excerpt(markdown),
        settings=settings,
        model=model,
    )

    # Wholesale replace, the rule every derived row follows: a re-parse or a
    # different model produces a different reading, and merging two readings
    # of one work would say things neither of them said.
    session.execute(delete(PaperInsight).where(PaperInsight.paper_id == paper.id))
    session.add(
        PaperInsight(
            paper_id=paper.id,
            genre=reading["genre"],
            question=reading["question"] or None,
            argument=reading["argument"] or None,
            significance=reading["significance"] or None,
            claims_json=json.dumps(reading["claims"]),
            entities_json=json.dumps(reading["entities"]),
            grounded=reading["grounded"],
            # The model that actually answered — the routed one.
            model_id=model,
            pipeline_version=settings.pipeline_version if settings else 1,
        )
    )

    BROKER.publish(
        "insight.done",
        paper_id=paper.id,
        genre=reading["genre"],
        grounded=reading["grounded"],
    )
