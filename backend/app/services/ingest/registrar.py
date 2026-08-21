"""Turning a file on disk into a Paper row.

Two kinds of duplicate arrive in a real library and they need different
answers. A byte-identical file is the same object and is skipped outright. A
*logically* identical paper — arXiv v1 versus v2, preprint versus published —
is a different file with the same work key, and gets recorded rather than
silently dropped, because deciding which copy to keep is the operator's call.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.paths import classify_document
from app.core.types import utcnow
from app.models import Job, JobKind, Paper, PaperStatus
from app.services.ingest.hashing import hash_file
from app.services.ingest.identity import work_key
from app.workers.queue import enqueue

logger = logging.getLogger(__name__)


class Registration(StrEnum):
    CREATED = "created"
    DUPLICATE_CONTENT = "duplicate_content"
    DUPLICATE_WORK = "duplicate_work"
    MOVED = "moved"


@dataclass(frozen=True)
class RegistrationResult:
    outcome: Registration
    paper: Paper | None
    existing: Paper | None = None


def register_document(
    session: Session,
    path: Path | str,
    *,
    pipeline_version: int = 1,
) -> RegistrationResult:
    """Record a library document, or explain why it was not recorded.

    Format-agnostic: PDFs, plain text, Markdown and .docx all become the same
    kind of row. What a file *is* only matters at the parse stage, and the
    column is still called ``pdf_path`` because renaming it would rewrite every
    migration for no behavioural gain.
    """
    path = Path(path).resolve()
    digest = hash_file(path)

    existing = session.scalar(select(Paper).where(Paper.content_sha256 == digest))
    if existing is not None:
        if existing.pdf_path != str(path):
            # Same bytes at a new location: follow the file rather than making
            # a second row that will 404 when opened.
            logger.info("paper %d moved to %s", existing.id, path)
            existing.pdf_path = str(path)
            existing.updated_at = utcnow()
            session.add(existing)
            return RegistrationResult(Registration.MOVED, existing)
        return RegistrationResult(Registration.DUPLICATE_CONTENT, None, existing)

    # Identity beyond the hash needs the text layer, which the parse stage has
    # not run yet. Start from the hash and let the metadata stage refine it.
    paper = Paper(
        content_sha256=digest,
        work_key=work_key(content_sha256=digest),
        pdf_path=str(path),
        pdf_bytes=path.stat().st_size,
        status=PaperStatus.PENDING,
        pipeline_version=pipeline_version,
    )
    session.add(paper)
    session.flush()

    enqueue(session, JobKind.PARSE, paper_id=paper.id)
    return RegistrationResult(Registration.CREATED, paper)


def refresh_work_key(session: Session, paper: Paper) -> Registration:
    """Recompute the work key once metadata exists, and flag collisions.

    Called after the metadata stage. A collision means two files describe the
    same work; both rows are kept and surfaced for a merge decision, because
    guessing wrong here loses a paper.
    """
    meta = paper.meta
    if meta is None:
        return Registration.CREATED

    import json

    authors = json.loads(meta.authors_json) if meta.authors_json else []
    new_key = work_key(
        doi=meta.doi,
        arxiv_id=meta.arxiv_id,
        title=meta.title,
        authors=authors,
        content_sha256=paper.content_sha256,
    )
    if new_key == paper.work_key:
        return Registration.CREATED

    clash = session.scalar(
        select(Paper).where(Paper.work_key == new_key, Paper.id != paper.id)
    )
    paper.work_key = new_key
    session.add(paper)

    if clash is not None:
        logger.info(
            "paper %d shares work key %s with paper %d", paper.id, new_key, clash.id
        )
        return Registration.DUPLICATE_WORK
    return Registration.CREATED


def library_documents(root: Path) -> list[Path]:
    """Every ingestible file under ``root``, at any depth.

    Recursive because people organise libraries into folders — by year, by
    project, by reading list — and a scan that only sees the top level silently
    ignores most of the collection. Classification is by content where possible,
    so an extensionless arXiv download is still found.
    """
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and classify_document(path) is not None
    )


def pending_documents(root: Path, session: Session) -> list[Path]:
    """Documents under ``root`` that are not yet known, by path.

    A path-level filter only. Content hashing happens in ``register_document``,
    since hashing every file on every scan would dominate a rescan of a large
    library.
    """
    known = {row for row in session.scalars(select(Paper.pdf_path))}
    return [p for p in library_documents(root) if str(p.resolve()) not in known]


def dead_paper_ids(session: Session) -> list[int]:
    """Papers whose PDF has disappeared from disk."""
    missing = []
    for paper in session.scalars(select(Paper)):
        if not Path(paper.pdf_path).exists():
            missing.append(paper.id)
    return missing


def outstanding_jobs(session: Session, paper_id: int) -> int:
    from app.models import JobState

    return (
        session.query(Job)
        .filter(
            Job.paper_id == paper_id,
            Job.state.in_([JobState.QUEUED, JobState.RUNNING]),
        )
        .count()
    )
