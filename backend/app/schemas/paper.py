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
    #: Where the abstract came from. A book has none, so one may have been
    #: assembled from its own paragraphs; the reader has to be able to tell
    #: that from something its author wrote.
    abstract_source: str | None = None
    summary: str | None = None
    venue: str | None = None
    tags: list[str] = []
    page_count: int | None = None
    pdf_bytes: int | None = None
    work_key: str
    added_at: datetime
    last_error: str | None = None
    parse: ParseInfo | None = None

    #: The region of the map this paper sits in.
    cluster_name: str | None = None
    #: HDBSCAN membership strength, 0..1. Low means the paper sits between
    #: fields rather than inside one.
    cluster_confidence: float | None = None
    #: Distance from the manifold the reducer was fitted on; ~1 is typical.
    #: High means the paper is from an area the map has not seen much of.
    manifold_drift: float | None = None
    #: Placed by transform() against a stored fit rather than a full refit.
    provisional: bool = False


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


class UploadAccepted(BaseModel):
    filename: str
    paper_id: int | None
    #: created | duplicate_content | moved — the same vocabulary the watcher uses.
    outcome: str
    bytes: int


class UploadRejected(BaseModel):
    filename: str
    reason: str


class UploadResponse(BaseModel):
    accepted: list[UploadAccepted]
    rejected: list[UploadRejected]
    #: How many will actually be processed; duplicates are accepted but not queued.
    queued: int
    total: int
