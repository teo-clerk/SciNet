"""DTOs for the papers API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ParseInfo(BaseModel):
    tier: int
    parser: str
    quality_score: float | None
    char_count: int | None
    degraded: bool = False
    escalation_reasons: list[str] = []


class PaperSummary(BaseModel):
    """Deliberately small — this shape is returned in lists of thousands."""

    id: int
    title: str | None
    year: int | None
    status: str
    doi: str | None = None
    arxiv_id: str | None = None


class PaperDetail(PaperSummary):
    authors: list[str] = []
    abstract: str | None = None
    summary: str | None = None
    venue: str | None = None
    tags: list[str] = []
    page_count: int | None = None
    pdf_bytes: int | None = None
    work_key: str
    added_at: datetime
    last_error: str | None = None
    parse: ParseInfo | None = None


class PaperPage(BaseModel):
    items: list[PaperSummary]
    total: int
    offset: int
    limit: int


class IngestRequest(BaseModel):
    path: str


class IngestResponse(BaseModel):
    outcome: str
    paper_id: int | None = None
    existing_paper_id: int | None = None


class JobCounts(BaseModel):
    counts: dict[str, int]
    queued: int
    running: int
    failed: int
    dead: int
