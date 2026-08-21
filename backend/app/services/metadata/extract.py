"""Deterministic metadata extraction from a library document.

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

from app.core.paths import DocumentKind, classify_document
from app.models import MetaSource
from app.services.ingest.identity import normalise_doi
from app.services.metadata.synopsis import (
    BOILERPLATE_RE,
    TABLE_RE,
    Strategy,
    find_synopsis,
)

# Identifiers are searched only near the front of the document: a DOI further in
# almost always belongs to a cited paper, not to this one.
HEAD_CHARS = 4000

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:a-z0-9]+)", re.IGNORECASE)
ARXIV_NEW_RE = re.compile(r"arxiv[:\s]\s*(\d{4}\.\d{4,5}(?:v\d+)?)", re.IGNORECASE)
ARXIV_OLD_RE = re.compile(
    r"arxiv[:\s]\s*([a-z-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?)", re.IGNORECASE
)

AUTHOR_SPLIT_RE = re.compile(r"\s*(?:,|;|\band\b|&)\s*", re.IGNORECASE)
# Journal PDF templates ship with these still in the Author metadata field, and
# they survive into the sidebar as the paper's authors.
PLACEHOLDER_AUTHOR_RE = re.compile(
    r"^(?:lastname|surname|firstname|forename|author|name|anonymous|unknown"
    r"|title|your\s+name|enter)\b",
    re.IGNORECASE,
)
# Affiliation markers trailing a name: "Alan Turing2,3" or "Ada Lovelace*".
AUTHOR_MARKER_RE = re.compile(r"[\d\*†‡§¶,\s]+$")

# Lines that are page furniture rather than the title.
FURNITURE_RE = re.compile(
    r"^(?:arxiv[:\s]|preprint|draft|under review|submitted|accepted|"
    r"published|proceedings|copyright|©|doi[:\s]|https?://|page\s|\d+$"
    # Journals print their own address across the top of every page, and it is
    # frequently the first substantial line the parser sees.
    r"|www\.|nature\.com|sciencedirect|springer|wiley|elsevier|ieee\b"
    r"|scientific\s*reports$|vol(?:ume)?\.?\s*\d|issn|isbn"
    # Repository and download banners stamped onto the first page.
    r"|downloaded\s+from|nih\s+public\s+access|author\s+manuscript"
    r"|available\s+online|see\s+discussions|this\s+content\s+downloaded"
    r"|licen[cs]ed?\s+under|all\s+rights\s+reserved"
    # Unfilled manuscript templates. These reach the sidebar as a paper's
    # title and are unmistakably not one.
    r"|replace\s+this|double-?click\s+here|click\s+here\s+to|<\s*title\s*>"
    r"|insert\s+(?:title|your)|type\s+your|\[?title\s+here)",
    re.IGNORECASE,
)

# "Journal of Theoretical Biology 241 (2006) 438-441" — a citation line for the
# paper, printed above it, not the paper's name.
CITATION_HEADER_RE = re.compile(
    r"\d+\s*\(\s*(?:19|20)\d{2}\s*\)\s*\d+\s*[-–]\s*\d+\s*$"
)

# There was a rule here rejecting lines that begin with an institution name.
# It was removed: every formulation of it also ate real titles — "Laboratory
# Automation for High-Throughput Screening", "Department Store Economics",
# "Institutional Trust and Democratic Backsliding" — and losing a real title is
# a worse outcome than occasionally showing an affiliation. The two affected
# papers are better served by the embedded-metadata check below.

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
        if len(cleaned) <= 1:
            continue
        if PLACEHOLDER_AUTHOR_RE.match(cleaned):
            # An unfilled template is worse than no author at all: it reads as
            # a real name and there is no way for the reader to tell.
            continue
        names.append(cleaned)
    return names


# "- 1 The post-reproductive ovary..." — a list bullet and page number the
# parser carried over from the page margin.
# A bullet, a page number, or a blockquote marker carried over from the page
# margin. Stripped before the furniture patterns are tested, or a template
# placeholder behind a ">" slips through as a title.
LEADING_MARKER_RE = re.compile(r"^[>\-–—*•|]+\s*\d*\s*")

EMPHASIS_RE = re.compile(r"(\*{1,3}|_{1,3})(.+?)\1")


def strip_markdown(line: str) -> str:
    """Remove heading markers and emphasis from a candidate title line."""
    candidate = line.strip().lstrip("#").strip()
    # Applied repeatedly for nested emphasis (***bold italic***).
    for _ in range(3):
        replaced = EMPHASIS_RE.sub(r"\2", candidate)
        if replaced == candidate:
            break
        candidate = replaced
    # An unbalanced marker survives the pattern above.
    return candidate.strip().strip("*_").strip()


# A line carrying a bare URL or DOI is a citation stripe, a footer, or a
# preprint banner — never a title. Cheaper and far more general than teaching
# CITATION_HEADER_RE every journal's house style: the citation that shipped
# this rule read "An. Quím. RSEQ, 2026, 122 (1), 11-15 https://doi.org/10.6...",
# which matches no positional pattern but is unmistakable by its DOI.
LOCATOR_RE = re.compile(r"https?://|\bdoi\.org/|\bdoi:\s*10\.", re.IGNORECASE)

# How far into the document to look for a heading. Titles live at the top; a
# heading found deeper is a section name.
HEADING_SEARCH_LINES = 40

# Headings are tried shallowest first, and no deeper than this. Converters mark
# the title at the top level and demote everything else — authors arrive as
# ``##`` or ``######``, journal banners as ``###`` — so accepting any depth
# made "Angelica Kaufmann" and "Article in Press" into titles. But the top
# level is not always the title either: NIH manuscripts stamp
# "# NIH Public Access Author Manuscript" above a level-2 real title, so a
# rejected level-1 falls through to level 2 rather than to line order.
MAX_TITLE_HEADING_LEVEL = 2
HEADING_RE = re.compile(r"^\s*(#{1,6})\s*\S")

# A level-1 heading that names part of the document rather than the document.
# Front matter ("Table of Contents") and numbered sections ("1 Introduction")
# both appear above the real title often enough to matter.
SECTION_HEADING_RE = re.compile(
    r"^\s*(?:\d+[.)]?\s+)?(?:table\s+of\s+contents|contents|summary|"
    r"introduction|abstract|background|references|bibliography|"
    r"acknowledge?ments?|appendix|conclusions?|methods?|results?|discussion)"
    r"\s*$",
    re.IGNORECASE,
)


def _title_candidate(line: str) -> str | None:
    """A line reduced to a usable title, or None if it is page furniture."""
    # Tolerate Markdown: the rescued text arrives with heading markers and
    # emphasis around the title, both of which end up rendered literally on
    # the map ("**Ultraviolet Spectra of Local Galaxies").
    candidate = LEADING_MARKER_RE.sub("", strip_markdown(line)).strip()
    if len(candidate) < MIN_TITLE_LENGTH or len(candidate) > MAX_TITLE_LENGTH:
        return None
    if FURNITURE_RE.match(candidate):
        return None
    if CITATION_HEADER_RE.search(candidate) or LOCATOR_RE.search(candidate):
        return None
    if SECTION_HEADING_RE.match(candidate):
        # Checked here rather than only in the heading pass: "Introduction" is
        # not a title whether or not it happens to be marked up as one.
        return None
    return candidate


def guess_title(text: str) -> str | None:
    """The document's title, preferring an explicit heading over line order.

    Headings first, shallowest level first, then line order. A Markdown heading
    near the top is the strongest signal the converter gives us — it marked
    that line as a title — so it wins even when unmarked text precedes it,
    which is how journal citation stripes and "PREPRINT" banners used to become
    titles. Only if no heading qualifies does this fall back to the first
    substantial line.

    Crude on purpose — it only has to be right often enough that the LLM
    fallback is rarely needed, and its verdict is recorded as HEURISTIC so a
    better source can override it later.
    """
    lines = text.splitlines()

    head = lines[:HEADING_SEARCH_LINES]
    for level in range(1, MAX_TITLE_HEADING_LEVEL + 1):
        for line in head:
            match = HEADING_RE.match(line)
            if match is None or len(match.group(1)) != level:
                continue
            candidate = _title_candidate(line)
            if candidate is None:
                continue
            return candidate

    for line in lines:
        deep = HEADING_RE.match(line)
        if deep is not None and len(deep.group(1)) > MAX_TITLE_HEADING_LEVEL:
            # The converter marked this line subordinate. It was already passed
            # over by the heading pass; picking it up here on account of its
            # position would undo that judgement — this is how author bylines
            # ("###### **Pierluigi Fasano**") became titles.
            continue
        candidate = _title_candidate(line)
        if candidate is None:
            continue
        if candidate.lower().startswith("abstract"):
            break
        return candidate
    return None


ABSTRACT_HEADING_RE = re.compile(
    r"^#{1,6}\s*\**\s*abstract\b.*$", re.IGNORECASE | re.MULTILINE
)
NEXT_HEADING_RE = re.compile(r"^#{1,6}\s+\S", re.MULTILINE)
MIN_ABSTRACT_CHARS = 120
MAX_ABSTRACT_CHARS = 4000


def extract_abstract(markdown: str) -> str | None:
    """Pull the abstract out of the parsed Markdown.

    The abstract is the single most informative field for placing a paper on
    the map — it is what the author wrote to say what the work is about — and
    it was being left on the floor: the field existed but nothing ever filled
    it, so every document vector was built from a title, a generated summary
    and a list of section headings.

    Found by heading rather than by position, because front matter varies
    wildly: author lists, affiliations and preprint stamps all sit above it in
    unpredictable amounts.
    """
    if not markdown:
        return None

    match = ABSTRACT_HEADING_RE.search(markdown)
    if match is None:
        return None

    rest = markdown[match.end() :]
    # The abstract runs until the next heading of any level.
    following = NEXT_HEADING_RE.search(rest)
    body = rest[: following.start()] if following else rest

    cleaned = " ".join(body.split())
    if len(cleaned) < MIN_ABSTRACT_CHARS:
        return None
    return cleaned[:MAX_ABSTRACT_CHARS]


# Front matter that sits between the title and the abstract in preprints.
# Stems, not whole words: "universit" has to match "University", "Universite"
# and "Universiteit". A trailing \b would defeat all but the first.
AFFILIATION_RE = re.compile(
    r"\b(?:universit|department|institut|laborator|academy|college|faculty|"
    r"school\s+of|centre\s+for|center\s+for|observator|@|e-?mail)",
    re.IGNORECASE,
)
# Where the body starts, when nothing is labelled "abstract".
BODY_START_RE = re.compile(
    r"^#{0,6}\s*\**\s*(?:[IVX0-9]+[.\s]+)?(?:introduction|background|"
    r"motivation|overview|preliminaries)\b",
    re.IGNORECASE | re.MULTILINE,
)
SENTENCE_END_RE = re.compile(r"[.!?](?:\s|$)")
MIN_ABSTRACT_SENTENCES = 2
MAX_LEADING_PARAGRAPHS = 12


def _looks_like_prose(paragraph: str) -> bool:
    """Is this a paragraph of writing, or front matter?

    Author lists, affiliations, report numbers and page markers all sit above
    an unlabelled abstract, and each has a shape: short, heavy with proper
    nouns, or naming an institution. Prose has sentences.
    """
    text = " ".join(paragraph.split())
    if len(text) < MIN_ABSTRACT_CHARS:
        return False
    if AFFILIATION_RE.search(text):
        return False
    # A copyright page reads like prose by every other measure here: long
    # enough, several sentences, not especially capitalised. It was being
    # returned as the abstract for books, which is how "All rights reserved.
    # No part of this book may be reproduced" ended up describing a paper.
    if BOILERPLATE_RE.search(text):
        return False
    # A keywords/summary table converted to Markdown pipes. Reads as prose by
    # every other measure here and is not one.
    if TABLE_RE.search(text):
        return False
    if len(SENTENCE_END_RE.findall(text)) < MIN_ABSTRACT_SENTENCES:
        return False

    words = [w for w in text.split() if w]
    if not words:
        return False
    # An author list is mostly capitalised tokens; running prose is not.
    capitalised = sum(1 for w in words if w[:1].isupper())
    return capitalised / len(words) < 0.4


def extract_abstract_unlabelled(markdown: str) -> str | None:
    """Find an abstract that carries no heading.

    Many preprints — physics ones especially — open with title, authors,
    affiliation and then the abstract as a bare paragraph, with the first
    heading appearing only at "Introduction". Requiring the word "Abstract"
    missed 127 of 293 papers in a real corpus, and those papers were then
    placed on the map using only their title and a generated summary.
    """
    if not markdown:
        return None

    # Everything before the body proper. Without a stop the search would run
    # into the introduction and return that instead.
    body = BODY_START_RE.search(markdown)
    head = markdown[: body.start()] if body else markdown[:8000]

    for paragraph in head.split("\n\n")[:MAX_LEADING_PARAGRAPHS]:
        candidate = paragraph.strip()
        # Headings are the title, not the abstract; blockquotes are addresses.
        if candidate.startswith("#") or candidate.startswith(">"):
            continue
        if _looks_like_prose(candidate):
            return " ".join(candidate.split())[:MAX_ABSTRACT_CHARS]
    return None


def extract_year(text: str, head_chars: int = HEAD_CHARS) -> int | None:
    years = [int(y) for y in re.findall(r"\b(19[89]\d|20[0-4]\d)\b", text[:head_chars])]
    # Latest plausible year on the front matter is the publication year far more
    # often than the earliest, which tends to come from a citation.
    return max(years) if years else None


@dataclass(frozen=True)
class _Embedded:
    """Metadata a file carries about itself, if its format has any."""

    fields: dict[str, str] = field(default_factory=dict)
    #: Raw first-page text, used only for identifier matching.
    head_text: str = ""
    year: int | None = None
    source: MetaSource = MetaSource.PDF_EMBEDDED


def _embedded_metadata(path: Path | str) -> _Embedded:
    """Whatever the container itself claims, by format.

    PDFs carry a metadata dictionary that is worth reading and frequently worth
    ignoring. EPUBs carry a package document, which is far better: it is what a
    publisher filled in, not what a template left behind, and it is the only
    place a book's year is written down at all — the year regex reads the front
    matter, and a book's front matter is a copyright page listing every
    printing since 1974.

    Plain text and Markdown carry nothing, and .docx carries only the authoring
    tool's defaults, which would put "Normal.dotm" on the map as a title.
    """
    path = Path(path)
    kind = classify_document(path)

    if kind is DocumentKind.PDF:
        with pymupdf.open(path) as doc:
            head = doc[0].get_text() if doc.page_count else ""
            return _Embedded(dict(doc.metadata or {}), head)

    if kind is DocumentKind.EPUB:
        from app.services.parse.ebook import epub_metadata

        book = epub_metadata(path)
        return _Embedded(
            {"title": book.title or "", "author": "; ".join(book.authors)},
            year=book.year,
            source=MetaSource.EBOOK_EMBEDDED,
        )

    if kind is DocumentKind.MOBI:
        from app.services.parse.ebook import mobi_metadata

        book = mobi_metadata(path)
        return _Embedded(
            {"title": book.title or "", "author": "; ".join(book.authors)},
            source=MetaSource.EBOOK_EMBEDDED,
        )

    return _Embedded()


def extract_from_document(
    path: Path | str, *, parsed_text: str | None = None
) -> ExtractedMeta:
    """Read everything obtainable without a model.

    ``parsed_text`` is the Markdown the parse stage actually kept. When a paper
    escalated to tier 1 or 2 it did so *because* its embedded text layer was
    unusable, so deriving a title from that same layer reproduces the very
    garbage the escalation was meant to escape — run-together words from a CID
    font, or mojibake from a double-encoded stream. The rescued text is used
    for the shape heuristics when it is available; the raw page is still read
    for the PDF's own metadata dictionary, which is unaffected by any of this.

    Non-PDF documents take the same path with an empty metadata dictionary:
    the heuristics only ever needed text, and the parse stage has already
    produced it.
    """
    container = _embedded_metadata(path)
    embedded, raw_head = container.fields, container.head_text

    head_text = parsed_text[: HEAD_CHARS * 2] if parsed_text else raw_head
    # Identifiers are searched in both: a DOI can survive in one and not the
    # other, and a wrong one is worse than none, so only exact matches count.
    identifier_text = f"{head_text}\n{raw_head}"

    meta = ExtractedMeta()

    # The PDF's own Title field is the best source when it is filled in
    # honestly, and worthless when it is not: real files in a real library
    # carry "Dissertation Thesis", "CLASE No. 1 PARTE I", a journal's running
    # header, or a LaTeX template's leftovers. Checked against the same
    # furniture patterns as any other candidate before being trusted.
    embedded_title = strip_markdown(embedded.get("title") or "")
    embedded_usable = (
        len(embedded_title) >= MIN_TITLE_LENGTH
        and not FURNITURE_RE.match(embedded_title)
        and not CITATION_HEADER_RE.search(embedded_title)
    )
    if embedded_usable:
        meta.title = embedded_title
        meta.field_sources["title"] = container.source
    elif (guessed := guess_title(head_text)) is not None:
        meta.title = guessed
        meta.field_sources["title"] = MetaSource.HEURISTIC

    if embedded_author := (embedded.get("author") or "").strip():
        if names := split_authors(embedded_author):
            meta.authors = names
            meta.field_sources["authors"] = container.source

    if (doi := extract_doi(identifier_text)) is not None:
        meta.doi = doi
        meta.field_sources["doi"] = MetaSource.REGEX

    if (arxiv := extract_arxiv_id(identifier_text)) is not None:
        meta.arxiv_id = arxiv
        meta.field_sources["arxiv_id"] = MetaSource.REGEX

    if container.year is not None:
        # A stated publication date beats scraping four digits off the page.
        meta.year = container.year
        meta.field_sources["year"] = container.source
    elif (year := extract_year(identifier_text)) is not None:
        meta.year = year
        meta.field_sources["year"] = MetaSource.REGEX

    if parsed_text:
        # A labelled abstract, else one recognised by its shape, else a book's
        # preface, else a digest of the document's own most topical paragraphs.
        # See app.services.metadata.synopsis for why the ladder has four rungs.
        synopsis = find_synopsis(parsed_text)
        if synopsis is not None:
            meta.abstract = synopsis.text
            meta.field_sources["abstract"] = (
                MetaSource.EXTRACTED_DIGEST
                if synopsis.strategy is Strategy.DIGEST
                else MetaSource.HEURISTIC
            )

    return meta
