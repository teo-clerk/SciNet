"""What each job kind actually does.

Handlers are deliberately thin: they move a paper from one state to the next
and enqueue the follow-on job. All real logic lives in ``app.services`` so it
can be tested without a queue, a database, or a GPU.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.events import BROKER
from app.core.paths import markdown_path_for
from app.models import (
    Job,
    JobKind,
    MarkdownDoc,
    Paper,
    PaperMeta,
    PaperStatus,
)
from app.services.ingest.registrar import Registration, refresh_work_key
from app.services.metadata.extract import extract_from_pdf
from app.services.parse import tier0_pymupdf, tier1_marker, tier2_vlm
from app.services.parse.router import parse_with_escalation
from app.workers.queue import enqueue

logger = logging.getLogger(__name__)


def handle_parse(session: Session, job: Job, settings: Settings) -> None:
    """Tier the PDF into Markdown and record how hard it was."""
    paper = session.get(Paper, job.paper_id)
    if paper is None:
        raise ValueError(f"paper {job.paper_id} no longer exists")

    pdf = Path(paper.pdf_path)
    if not pdf.exists():
        raise FileNotFoundError(f"{pdf} has disappeared from the library")

    paper.status = PaperStatus.PARSING
    session.add(paper)
    session.commit()

    BROKER.publish("parse.start", paper_id=paper.id, name=pdf.name)

    outcome = parse_with_escalation(
        pdf,
        tier0=tier0_pymupdf.parse,
        tier1=tier1_marker.parse,
        tier2=tier2_vlm.parse,
        probe=tier0_pymupdf.probe_pdf,
    )

    md_path = markdown_path_for(paper.id, settings.markdown_dir)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(outcome.result.markdown, encoding="utf-8")

    existing = session.get(MarkdownDoc, paper.id)
    if existing is not None:
        session.delete(existing)
        session.flush()

    session.add(
        MarkdownDoc(
            paper_id=paper.id,
            md_path=str(md_path),
            tier=outcome.result.tier,
            parser=outcome.result.parser,
            parser_version=outcome.result.parser_version,
            quality_score=outcome.report.score,
            char_count=len(outcome.result.markdown),
            quality_report_json=json.dumps(
                {
                    "passed": outcome.report.passed,
                    "reasons": list(outcome.report.reasons),
                    "escalation_reasons": list(outcome.escalation_reasons),
                    "metrics": outcome.report.metrics,
                    "degraded": outcome.degraded,
                    "notes": outcome.notes,
                }
            ),
        )
    )

    paper.page_count = outcome.result.page_count
    paper.status = PaperStatus.PARSED
    session.add(paper)

    enqueue(session, JobKind.METADATA, paper_id=paper.id)
    BROKER.publish(
        "parse.done",
        paper_id=paper.id,
        tier=outcome.result.tier,
        degraded=outcome.degraded,
    )


def handle_metadata(session: Session, job: Job, settings: Settings) -> None:
    """Extract identifiers and bibliographic fields, then fix the work key."""
    paper = session.get(Paper, job.paper_id)
    if paper is None:
        raise ValueError(f"paper {job.paper_id} no longer exists")

    extracted = extract_from_pdf(paper.pdf_path)

    meta = session.get(PaperMeta, paper.id) or PaperMeta(paper_id=paper.id)
    meta.title = extracted.title
    meta.authors_json = json.dumps(extracted.authors)
    meta.abstract = extracted.abstract
    meta.doi = extracted.doi
    meta.arxiv_id = extracted.arxiv_id
    meta.year = extracted.year
    meta.field_sources_json = json.dumps(
        {k: str(v) for k, v in extracted.field_sources.items()}
    )
    session.add(meta)
    session.flush()
    paper.meta = meta

    outcome = refresh_work_key(session, paper)
    if outcome is Registration.DUPLICATE_WORK:
        BROKER.publish("paper.duplicate", paper_id=paper.id, work_key=paper.work_key)

    # M1 ends here: embedding and tagging arrive in M2. Marking the paper ready
    # keeps it visible in the UI rather than stranded in an intermediate state.
    paper.status = PaperStatus.READY
    session.add(paper)

    if settings.enrichment_enabled and (extracted.doi or extracted.arxiv_id):
        enqueue(session, JobKind.ENRICH, paper_id=paper.id)

    BROKER.publish("metadata.done", paper_id=paper.id, title=extracted.title)


def handle_enrich(session: Session, job: Job, settings: Settings) -> None:
    """Optional online metadata cleanup. Never runs unless explicitly enabled."""
    if not settings.enrichment_enabled:
        logger.info("enrichment disabled; skipping job %d", job.id)
        return

    from app.services.metadata.enrich import enrich_paper

    paper = session.get(Paper, job.paper_id)
    if paper is None:
        raise ValueError(f"paper {job.paper_id} no longer exists")

    if enrich_paper(session, paper, settings):
        BROKER.publish("enrich.done", paper_id=paper.id)


HANDLERS = {
    JobKind.PARSE: handle_parse,
    JobKind.METADATA: handle_metadata,
    JobKind.ENRICH: handle_enrich,
}
