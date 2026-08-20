"""Logical identity for a paper, independent of the file it arrived in.

``content_sha256`` catches byte-identical duplicates and nothing else. The same
paper routinely arrives as arXiv v1 and v2, as a preprint and a published
version, or simply downloaded twice under different names. ``work_key`` is the
key those should agree on.
"""

from __future__ import annotations

import re

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:a-z0-9]+)", re.IGNORECASE)
ARXIV_VERSION_RE = re.compile(r"v\d+$", re.IGNORECASE)
NON_WORD_RE = re.compile(r"[^a-z0-9]+")


def normalise_doi(raw: str | None) -> str | None:
    """Reduce any of the usual DOI spellings to the bare, lowercased identifier."""
    if not raw:
        return None

    match = DOI_RE.search(raw.strip())
    if not match:
        return None

    # Trailing punctuation is common when a DOI is scraped from running text.
    return match.group(1).rstrip(".,;)]").lower()


def normalise_arxiv_id(raw: str | None) -> str | None:
    """Strip the version suffix so v1 and v2 of a paper share one key."""
    if not raw:
        return None
    cleaned = raw.strip().lower().removeprefix("arxiv:").strip()
    return ARXIV_VERSION_RE.sub("", cleaned) or None


def slugify(text: str, *, max_length: int = 80) -> str:
    return NON_WORD_RE.sub("-", text.casefold()).strip("-")[:max_length]


def surname_of(author: str) -> str:
    """Best-effort surname, for use as a weak disambiguator only.

    Deliberately crude: this feeds a dedup hint, not a citation. "A. Vaswani"
    and "Ashish Vaswani" must agree, which is all that is required of it.
    """
    cleaned = author.replace(",", " ").strip()
    parts = [p for p in cleaned.split() if len(p.rstrip(".")) > 1]
    return slugify(parts[-1]) if parts else ""


def work_key(
    *,
    doi: str | None = None,
    arxiv_id: str | None = None,
    title: str | None = None,
    authors: list[str] | None = None,
    content_sha256: str | None = None,
) -> str:
    """Identity of the *work*, in descending order of authority.

    A DOI is definitive. An arXiv ID is definitive once the version is dropped.
    Title plus first-author surname is a heuristic and collisions are surfaced
    for review rather than merged silently. The content hash is the last
    resort, so a scanned PDF with no extractable identity is still unique.
    """
    if (normalised := normalise_doi(doi)) is not None:
        return f"doi:{normalised}"

    if (arxiv := normalise_arxiv_id(arxiv_id)) is not None:
        return f"arxiv:{arxiv}"

    if title and (slug := slugify(title)):
        first_author = surname_of(authors[0]) if authors else ""
        return f"title:{slug}|{first_author}"

    if content_sha256:
        return f"sha256:{content_sha256}"

    raise ValueError("cannot derive a work key without any identifying field")
