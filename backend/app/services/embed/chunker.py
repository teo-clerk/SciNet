"""Splitting a paper's Markdown into embeddable pieces.

Two different jobs need two different granularities, and conflating them is the
classic way to end up with a map that looks fine and means nothing:

*Document* text drives the map. It is deliberately short — title, abstract,
summary, section headings — because mean-pooling a whole paper drags every
document toward a generic academic-prose centroid and collapses the clusters.

*Chunk* text drives search. Here the whole body matters, split on section
boundaries so a hit can be attributed to a part of the paper.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)
# Sections that say nothing about what a paper is *about*.
BOILERPLATE_SECTIONS = re.compile(
    r"^\s*(references|bibliography|acknowledge?ments?|appendix|"
    r"supplementary|funding|author contributions|conflicts? of interest|"
    r"declaration)",
    re.IGNORECASE,
)

DEFAULT_CHUNK_CHARS = 1800
DEFAULT_OVERLAP_CHARS = 200
MIN_CHUNK_CHARS = 120
MAX_DOC_CHARS = 6000


@dataclass(frozen=True)
class Chunk:
    ord: int
    section: str | None
    text: str

    @property
    def approx_tokens(self) -> int:
        # Good enough for budgeting; the encoder does real tokenisation.
        return max(1, len(self.text) // 4)


@dataclass(frozen=True)
class Section:
    title: str | None
    level: int
    body: str


def split_sections(markdown: str) -> list[Section]:
    """Split on Markdown headings, keeping any preamble as an untitled section."""
    matches = list(HEADING_RE.finditer(markdown))
    if not matches:
        body = markdown.strip()
        return [Section(None, 0, body)] if body else []

    sections: list[Section] = []
    preamble = markdown[: matches[0].start()].strip()
    if preamble:
        sections.append(Section(None, 0, preamble))

    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        body = markdown[match.end() : end].strip()
        sections.append(Section(match.group(2).strip(), len(match.group(1)), body))
    return sections


def is_boilerplate(title: str | None) -> bool:
    return bool(title) and bool(BOILERPLATE_SECTIONS.match(title or ""))


def chunk_markdown(
    markdown: str,
    *,
    max_chars: int = DEFAULT_CHUNK_CHARS,
    overlap: int = DEFAULT_OVERLAP_CHARS,
    drop_boilerplate: bool = True,
) -> list[Chunk]:
    """Section-aware chunks for retrieval.

    Chunks never span a section boundary, so a search hit can always be
    attributed to a part of the paper. Long sections are split on paragraph
    breaks with a small overlap, so a sentence straddling a split is still
    retrievable from one side.
    """
    chunks: list[Chunk] = []
    for section in split_sections(markdown):
        if drop_boilerplate and is_boilerplate(section.title):
            continue
        body = section.body.strip()
        if len(body) < MIN_CHUNK_CHARS:
            continue

        for piece in _split_long(body, max_chars, overlap):
            if len(piece.strip()) >= MIN_CHUNK_CHARS:
                chunks.append(Chunk(len(chunks), section.title, piece.strip()))
    return chunks


def _split_long(text: str, max_chars: int, overlap: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            # Prefer a paragraph break, then a sentence end, then a hard cut.
            for separator in ("\n\n", ". ", "\n"):
                found = text.rfind(separator, start + max_chars // 2, end)
                if found != -1:
                    end = found + len(separator)
                    break
        pieces.append(text[start:end])
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return pieces


def document_text(
    *,
    title: str | None,
    abstract: str | None,
    summary: str | None,
    markdown: str | None = None,
    max_chars: int = MAX_DOC_CHARS,
) -> str:
    """The text whose embedding decides where a paper sits on the map.

    Short and structural on purpose. Section headings are included because they
    characterise a paper's shape — a survey and an experimental report differ
    in headings long before they differ in vocabulary — but the body is not,
    because averaging it makes every paper look like every other paper.
    """
    parts: list[str] = []
    if title:
        parts.append(title.strip())
    if abstract:
        parts.append(abstract.strip())
    if summary:
        parts.append(summary.strip())

    if markdown:
        headings = [
            s.title
            for s in split_sections(markdown)
            if s.title and not is_boilerplate(s.title)
        ]
        if headings:
            parts.append("Sections: " + "; ".join(headings[:20]))

    return "\n\n".join(p for p in parts if p)[:max_chars]
