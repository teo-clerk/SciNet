"""Paper listing, detail, markdown, and opening the original PDF."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.core.events import BROKER
from app.core.paths import UnsafePathError, resolve_within
from app.models import (
    Cluster,
    MarkdownDoc,
    Paper,
    PaperMeta,
    PaperTag,
    Projection,
    ProjectionRun,
    Tag,
)
from app.schemas.paper import (
    PaperDetail,
    PaperPage,
    PaperSummary,
    ParseInfo,
    UploadAccepted,
    UploadRejected,
    UploadResponse,
)
from app.services.ingest.registrar import register_pdf

router = APIRouter(prefix="/api/papers", tags=["papers"])

#: Refused above this size. A scientific PDF is rarely past 100 MB, and the
#: upload is streamed to disk, so this guards against a mistake rather than a
#: memory limit.
MAX_UPLOAD_BYTES = 512 * 1024 * 1024
UPLOAD_CHUNK = 1024 * 1024
PDF_MAGIC = b"%PDF-"


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


def _open_command(path: Path) -> list[str]:
    """The platform's "open this file with whatever handles it" command.

    Kept as an argument vector on every branch. Windows needs `cmd /c start`
    because there is no standalone opener binary, and its first argument is a
    window title that must be present but empty — omitting it makes `start`
    treat the path as the title and open nothing.
    """
    if sys.platform == "win32":
        return ["cmd", "/c", "start", "", str(path)]
    if sys.platform == "darwin":
        return ["open", str(path)]
    return ["xdg-open", str(path)]


def _safe_destination(filename: str, library: Path) -> Path:
    """A collision-free path inside the library for an uploaded file.

    The client-supplied filename is reduced to its last component and stripped
    of anything but a conservative character set: it arrives over HTTP and must
    never be able to steer where the file lands.
    """
    stem = Path(filename or "upload.pdf").name
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", stem).lstrip(".") or "upload.pdf"
    if not cleaned.lower().endswith(".pdf"):
        cleaned += ".pdf"

    candidate = library / cleaned
    # Never overwrite. Two papers can legitimately share a filename, and
    # content-level deduplication happens later against the file's hash.
    counter = 1
    while candidate.exists():
        candidate = library / f"{Path(cleaned).stem}({counter}).pdf"
        counter += 1
    return resolve_within(candidate, library)


@router.post("/upload", response_model=UploadResponse)
async def upload_papers(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> UploadResponse:
    """Copy PDFs into the library and queue them.

    Streamed to disk in chunks rather than read into memory: this is built for
    dropping a few thousand papers in at once, and buffering those would be a
    memory limit disguised as a feature.

    Each file is registered immediately so the count in the progress drawer is
    real, and one bad file never aborts the batch — a rejected PDF is reported
    alongside the ones that worked.
    """
    settings.ensure_dirs()
    library = settings.library_dir

    accepted: list[UploadAccepted] = []
    rejected: list[UploadRejected] = []

    for upload in files:
        name = upload.filename or "upload.pdf"
        destination: Path | None = None
        try:
            destination = _safe_destination(name, library)

            written = 0
            first_chunk = True
            with open(destination, "wb") as handle:
                while chunk := await upload.read(UPLOAD_CHUNK):
                    if first_chunk:
                        # Checked from the bytes, not the extension: a file
                        # named .pdf that is not one wastes a parse job and
                        # lands in the library as permanent noise.
                        if not chunk.startswith(PDF_MAGIC):
                            raise ValueError("not a PDF (missing %PDF- header)")
                        first_chunk = False
                    written += len(chunk)
                    if written > MAX_UPLOAD_BYTES:
                        raise ValueError(
                            f"larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB"
                        )
                    handle.write(chunk)

            if written == 0:
                raise ValueError("empty file")

            result = register_pdf(db, destination)
            db.commit()

            accepted.append(
                UploadAccepted(
                    filename=name,
                    paper_id=result.paper.id if result.paper else None,
                    outcome=result.outcome.value,
                    bytes=written,
                )
            )
            BROKER.publish(
                "upload.received",
                name=name,
                outcome=result.outcome.value,
                paper_id=result.paper.id if result.paper else None,
            )
        except Exception as exc:  # noqa: BLE001 - one bad file must not end the batch
            db.rollback()
            if destination is not None and destination.exists():
                destination.unlink(missing_ok=True)
            rejected.append(UploadRejected(filename=name, reason=str(exc)[:200]))
        finally:
            await upload.close()

    queued = sum(1 for a in accepted if a.outcome == "created")
    BROKER.publish(
        "upload.batch", accepted=len(accepted), queued=queued, rejected=len(rejected)
    )
    return UploadResponse(
        accepted=accepted,
        rejected=rejected,
        queued=queued,
        total=len(files),
    )


@router.get("/{paper_id}", response_model=PaperDetail)
def get_paper(paper_id: int, db: Session = Depends(get_db)) -> PaperDetail:
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(404, "paper not found")

    meta = paper.meta
    md = db.get(MarkdownDoc, paper_id)

    # Placement confidence, from the active projection. Two different things:
    # how strongly the paper belongs to its cluster, and how far it sits from
    # the manifold the reducer was fitted on.
    placement = db.execute(
        select(
            Projection.cluster_probability,
            Projection.off_manifold,
            Projection.is_transformed,
            Cluster.llm_label,
        )
        .outerjoin(Cluster, Cluster.id == Projection.cluster_id)
        .join(ProjectionRun, ProjectionRun.id == Projection.run_id)
        .where(Projection.paper_id == paper_id, ProjectionRun.is_active.is_(True))
    ).first()

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
        cluster_name=placement.llm_label if placement else None,
        cluster_confidence=(
            round(placement.cluster_probability, 3)
            if placement and placement.cluster_probability is not None
            else None
        ),
        manifold_drift=(
            round(placement.off_manifold, 2)
            if placement and placement.off_manifold is not None
            else None
        ),
        provisional=bool(placement.is_transformed) if placement else False,
        tags=[
            slug
            for (slug,) in db.execute(
                select(Tag.slug)
                .join(PaperTag, PaperTag.tag_id == Tag.id)
                .where(PaperTag.paper_id == paper_id)
                .order_by(Tag.slug)
            ).all()
        ],
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
        # containing shell metacharacters is inert on every platform.
        subprocess.Popen(
            _open_command(path),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            501, f"no system PDF viewer available on {sys.platform}"
        ) from exc

    return {"status": "opened", "path": str(path)}
