"""Deterministic metadata extraction from a PDF.

Order of authority: the PDF's own metadata dictionary, then regular expressions
over the first page, then shape heuristics. An LLM is only consulted afterwards
for the fields still missing (see ``llm_fallback``), and never for identifiers —
a model asked for a DOI will produce a plausible one, and a wrong DOI silently
merges two different papers under a single work key.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from app.models import MetaSource
from app.services.ingest.identity import normalise_doi

# Identifiers are searched only near the front of the document: a DOI further in
# almost always belongs to a cited paper, not to this one.
HEAD_CHARS = 4000

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:a-z0-9]+)", re.IGNORECASE)
ARXIV_NEW_RE = re.compile(r"arxiv[:\s]\s*(\d{4}\.\d{4,5}(?:v\d+)?)", re.IGNORECASE)
ARXIV_OLD_RE = re.compile(
    r"arxiv[:\s]\s*([a-z-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?)", re.IGNORECASE
)

AUTHOR_SPLIT_RE = re.compile(r"\s*(?:,|;|\band\b|&)\s*", re.IGNORECASE)
# Affiliation markers trailing a name: "Alan Turing2,3" or "Ada Lovelace*".
AUTHOR_MARKER_RE = re.compile(r"[\d\*†‡§¶,\s]+$")

# Lines that are page furniture rather than the title.
FURNITURE_RE = re.compile(
    r"^(?:arxiv[:\s]|preprint|draft|under review|submitted|accepted|"
    r"published|proceedings|copyright|©|doi[:\s]|https?://|page\s|\d+$)",
    re.IGNORECASE,
)
MIN_TITLE_LENGTH = 12
MAX_TITLE_LENGTH = 300


@dataclass
class ExtractedMeta:
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    abstract: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    year: int | None = None
    field_sources: dict[str, MetaSource] = field(default_factory=dict)

    def missing(self) -> list[str]:
        """Fields the LLM fallback should be asked about."""
        gaps = []
        if not self.title:
            gaps.append("title")
        if not self.authors:
            gaps.append("authors")
        if not self.abstract:
            gaps.append("abstract")
        return gaps


def extract_doi(text: str, head_chars: int = HEAD_CHARS) -> str | None:
    match = DOI_RE.search(text[:head_chars])
    return normalise_doi(match.group(1)) if match else None


def extract_arxiv_id(text: str, head_chars: int = HEAD_CHARS) -> str | None:
    head = text[:head_chars]
    for pattern in (ARXIV_NEW_RE, ARXIV_OLD_RE):
        if match := pattern.search(head):
            return match.group(1)
    return None


def split_authors(raw: str) -> list[str]:
    """Split an author line, dropping affiliation superscripts."""
    if not raw or not raw.strip():
        return []

    names = []
    for part in AUTHOR_SPLIT_RE.split(raw):
        cleaned = AUTHOR_MARKER_RE.sub("", part.strip()).strip()
        if len(cleaned) > 1:
            names.append(cleaned)
    return names


def guess_title(text: str) -> str | None:
    """First substantial line that is not page furniture.

    Crude on purpose — it only has to be right often enough that the LLM
    fallback is rarely needed, and its verdict is recorded as HEURISTIC so a
    better source can override it later.
    """
    for line in text.splitlines():
        candidate = line.strip()
        if len(candidate) < MIN_TITLE_LENGTH or len(candidate) > MAX_TITLE_LENGTH:
            continue
        if FURNITURE_RE.match(candidate):
            continue
        if candidate.lower().startswith("abstract"):
            break
        return candidate
    return None


def extract_year(text: str, head_chars: int = HEAD_CHARS) -> int | None:
    years = [int(y) for y in re.findall(r"\b(19[89]\d|20[0-4]\d)\b", text[:head_chars])]
    # Latest plausible year on the front matter is the publication year far more
    # often than the earliest, which tends to come from a citation.
    return max(years) if years else None


def extract_from_pdf(path: Path | str) -> ExtractedMeta:
    """Read everything obtainable without a model."""
    with pymupdf.open(path) as doc:
        embedded = dict(doc.metadata or {})
        head_text = doc[0].get_text() if doc.page_count else ""

    meta = ExtractedMeta()

    embedded_title = (embedded.get("title") or "").strip()
    if len(embedded_title) >= MIN_TITLE_LENGTH:
        meta.title = embedded_title
        meta.field_sources["title"] = MetaSource.PDF_EMBEDDED
    elif (guessed := guess_title(head_text)) is not None:
        meta.title = guessed
        meta.field_sources["title"] = MetaSource.HEURISTIC

    if embedded_author := (embedded.get("author") or "").strip():
        if names := split_authors(embedded_author):
            meta.authors = names
            meta.field_sources["authors"] = MetaSource.PDF_EMBEDDED

    if (doi := extract_doi(head_text)) is not None:
        meta.doi = doi
        meta.field_sources["doi"] = MetaSource.REGEX

    if (arxiv := extract_arxiv_id(head_text)) is not None:
        meta.arxiv_id = arxiv
        meta.field_sources["arxiv_id"] = MetaSource.REGEX

    if (year := extract_year(head_text)) is not None:
        meta.year = year
        meta.field_sources["year"] = MetaSource.REGEX

    return meta
