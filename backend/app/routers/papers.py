"""Paper listing, detail, markdown, and opening the original PDF."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.core.paths import UnsafePathError, resolve_within
from app.models import MarkdownDoc, Paper, PaperMeta
from app.schemas.paper import (
    PaperDetail,
    PaperPage,
    PaperSummary,
    ParseInfo,
)

router = APIRouter(prefix="/api/papers", tags=["papers"])


def _authors(meta: PaperMeta | None) -> list[str]:
    if meta is None or not meta.authors_json:
        return []
    try:
        return json.loads(meta.authors_json)
    except json.JSONDecodeError:
        return []


def _summary(paper: Paper) -> PaperSummary:
    meta = paper.meta
    return PaperSummary(
        id=paper.id,
        title=meta.title if meta else None,
        year=meta.year if meta else None,
        status=paper.status,
        doi=meta.doi if meta else None,
        arxiv_id=meta.arxiv_id if meta else None,
    )


@router.get("", response_model=PaperPage)
def list_papers(
    db: Session = Depends(get_db),
    q: str | None = Query(None, description="substring match on title or DOI"),
    status: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> PaperPage:
    stmt = select(Paper).outerjoin(PaperMeta)
    count_stmt = select(func.count()).select_from(Paper).outerjoin(PaperMeta)

    if status:
        stmt = stmt.where(Paper.status == status)
        count_stmt = count_stmt.where(Paper.status == status)
    if q:
        pattern = f"%{q}%"
        clause = or_(PaperMeta.title.ilike(pattern), PaperMeta.doi.ilike(pattern))
        stmt = stmt.where(clause)
        count_stmt = count_stmt.where(clause)

    total = db.scalar(count_stmt) or 0
    papers = db.scalars(
        stmt.order_by(Paper.id.desc()).limit(limit).offset(offset)
    ).all()

    return PaperPage(
        items=[_summary(p) for p in papers], total=total, offset=offset, limit=limit
    )


@router.get("/{paper_id}", response_model=PaperDetail)
def get_paper(paper_id: int, db: Session = Depends(get_db)) -> PaperDetail:
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(404, "paper not found")

    meta = paper.meta
    md = db.get(MarkdownDoc, paper_id)

    parse_info = None
    if md is not None:
        report = json.loads(md.quality_report_json or "{}")
        parse_info = ParseInfo(
            tier=md.tier,
            parser=md.parser,
            quality_score=md.quality_score,
            char_count=md.char_count,
            degraded=report.get("degraded", False),
            escalation_reasons=report.get("escalation_reasons", []),
        )

    return PaperDetail(
        **_summary(paper).model_dump(),
        authors=_authors(meta),
        abstract=meta.abstract if meta else None,
        summary=meta.summary if meta else None,
        venue=meta.venue if meta else None,
        page_count=paper.page_count,
        pdf_bytes=paper.pdf_bytes,
        work_key=paper.work_key,
        added_at=paper.added_at,
        last_error=paper.last_error,
        parse=parse_info,
    )


@router.get("/{paper_id}/markdown", response_class=PlainTextResponse)
def get_markdown(paper_id: int, db: Session = Depends(get_db)) -> str:
    md = db.get(MarkdownDoc, paper_id)
    if md is None:
        raise HTTPException(404, "this paper has not been parsed yet")
    path = Path(md.md_path)
    if not path.exists():
        raise HTTPException(410, "markdown file is missing from disk")
    return path.read_text(encoding="utf-8")


def _safe_pdf_path(paper: Paper, settings: Settings) -> Path:
    """Resolve the PDF, refusing anything outside the library root.

    The client only ever sends a paper id, never a path — but the stored path
    is still validated, because a row written by an earlier version, a restored
    backup, or a symlink inside the library could all point elsewhere, and the
    next thing that happens to this value is a subprocess call.
    """
    try:
        path = resolve_within(paper.pdf_path, settings.library_dir)
    except UnsafePathError as exc:
        raise HTTPException(400, "refusing to open a path outside the library") from exc
    if not path.exists():
        raise HTTPException(410, "the PDF is no longer on disk")
    return path


@router.get("/{paper_id}/pdf")
def stream_pdf(
    paper_id: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    """Serve the PDF bytes for the in-app viewer."""
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(404, "paper not found")
    path = _safe_pdf_path(paper, settings)
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@router.post("/{paper_id}/open")
def open_in_system_viewer(
    paper_id: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    """Hand the PDF to the desktop's default viewer."""
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(404, "paper not found")
    path = _safe_pdf_path(paper, settings)

    try:
        # No shell, and an argument vector rather than a string, so a filename
        # containing shell metacharacters is inert.
        subprocess.Popen(
            ["xdg-open", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise HTTPException(501, "xdg-open is not available on this system") from exc

    return {"status": "opened", "path": str(path)}
