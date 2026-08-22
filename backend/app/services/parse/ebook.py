"""Reading EPUB, MOBI and AZW3 books.

Two different strategies, for a reason.

**EPUB** is a zip of XHTML, so it is opened directly. That is worth doing
rather than handing it to a PDF converter: the chapter headings are marked up
semantically, and the package manifest carries a real title, author and
publication date. Both are strictly better than what any layout-based guess
produces, and both matter downstream — the title goes on the map, and the
headings are how a book without an abstract gets characterised at all.

**MOBI and AZW3** are handed to MuPDF. Their payload is PalmDOC- or KF8-
compressed HTML, and MuPDF already decompresses both correctly; reimplementing
that would be a great deal of work to arrive at the same text. Headings come
back from font size rather than markup, which is less reliable than EPUB's, but
these formats are rarer in a scientific library and it is enough to work with.

A DRM-protected file cannot be read by anything, and says so plainly rather
than arriving as an empty document.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from app.services.parse.html_text import html_to_markdown
from app.services.parse.limits import append_note, page_budget

logger = logging.getLogger(__name__)


class UnreadableBook(Exception):
    """The book cannot be turned into text."""


#: Ceiling on what one book may expand to. A zip can claim to hold far more
#: than it does; a 4,000-page technical book is comfortably under 40 MB of
#: text, so anything past this is a malformed or hostile archive.
MAX_UNPACKED_BYTES = 40 * 1024 * 1024
#: The package document is a manifest, not content.
MAX_OPF_BYTES = 4 * 1024 * 1024

#: Amazon's DRM wrapper, at byte zero, ahead of an otherwise valid PalmDB.
DRM_MAGIC = b"CR!"

_CONTAINER = "META-INF/container.xml"
_XHTML_SUFFIXES = (".xhtml", ".html", ".htm")


#: Characters a printed page holds, measured on this library: three books of
#: 535, 273 and 260 pages converted at 2,093, 2,073 and 2,244 characters per
#: page. Used only to turn a page limit into something an EPUB can honour.
CHARS_PER_PRINTED_PAGE = 2100


def _limit(explicit: int | None) -> int:
    if explicit is not None:
        return explicit
    from app.core.config import get_settings

    return get_settings().max_parse_pages


def _character_allowance(pages: int) -> int:
    """A page limit expressed in characters, or 0 for no limit."""
    return max(0, pages) * CHARS_PER_PRINTED_PAGE


@dataclass(frozen=True)
class BookMetadata:
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None


def _read_capped(archive: zipfile.ZipFile, name: str, limit: int) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > limit:
        raise UnreadableBook(f"{name} claims {info.file_size} bytes; refusing")
    with archive.open(info) as handle:
        return handle.read(limit + 1)[:limit]


def _opf_path(archive: zipfile.ZipFile) -> str:
    """Where the package document lives, per META-INF/container.xml."""
    try:
        raw = _read_capped(archive, _CONTAINER, MAX_OPF_BYTES)
    except KeyError as exc:
        raise UnreadableBook("no META-INF/container.xml; not an EPUB") from exc
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise UnreadableBook(f"container.xml is not valid XML: {exc}") from exc

    element = root.find(".//{*}rootfile")
    full_path = element.get("full-path") if element is not None else None
    if not full_path:
        raise UnreadableBook("container.xml names no package document")
    return full_path


def _text_of(node: ET.Element | None) -> str:
    return " ".join((node.text or "").split()) if node is not None else ""


def _parse_opf(raw: bytes) -> tuple[ET.Element, BookMetadata]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise UnreadableBook(f"package document is not valid XML: {exc}") from exc

    title = _text_of(root.find(".//{*}metadata/{*}title")) or None
    authors = [
        name
        for node in root.findall(".//{*}metadata/{*}creator")
        if (name := _text_of(node))
    ]

    year = None
    for node in root.findall(".//{*}metadata/{*}date"):
        digits = _text_of(node)[:4]
        if digits.isdigit():
            # Several dates may be listed (published, modified). The earliest
            # is the one that describes the work rather than the file.
            candidate = int(digits)
            year = candidate if year is None else min(year, candidate)
    return root, BookMetadata(title, authors, year)


def _spine_documents(root: ET.Element, opf_path: str) -> list[str]:
    """Chapter file names, in reading order."""
    base = Path(opf_path).parent
    manifest = {
        item.get("id"): item.get("href")
        for item in root.findall(".//{*}manifest/{*}item")
        if item.get("id") and item.get("href")
    }
    order = [ref.get("idref") for ref in root.findall(".//{*}spine/{*}itemref")]

    names: list[str] = []
    for identifier in order:
        href = manifest.get(identifier or "")
        if not href:
            continue
        # Hrefs are relative to the package document and may be URL-escaped.
        from urllib.parse import unquote

        resolved = (base / unquote(href.split("#", 1)[0])).as_posix()
        names.append(str(Path(resolved).as_posix()).lstrip("/"))
    return names


def epub_metadata(path: Path) -> BookMetadata:
    """Title, authors and year from the package document."""
    try:
        with zipfile.ZipFile(path) as archive:
            opf = _opf_path(archive)
            _, meta = _parse_opf(_read_capped(archive, opf, MAX_OPF_BYTES))
            return meta
    except (zipfile.BadZipFile, KeyError, UnreadableBook) as exc:
        logger.debug("no EPUB metadata for %s: %s", path, exc)
        return BookMetadata()


def read_epub(path: Path, *, limit: int | None = None) -> tuple[str, int]:
    """The book as Markdown, and its chapter count.

    Chapters are read in spine order — the order the author intended — rather
    than in whatever order the zip happens to store them, which is frequently
    alphabetical and puts chapter 10 before chapter 2.
    """
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise UnreadableBook(f"{path.name} is not a readable zip: {exc}") from exc

    with archive:
        opf_path = _opf_path(archive)
        root, _ = _parse_opf(_read_capped(archive, opf_path, MAX_OPF_BYTES))
        names = _spine_documents(root, opf_path)
        if not names:
            # No usable spine: fall back to every XHTML file in the archive.
            names = sorted(
                n for n in archive.namelist() if n.lower().endswith(_XHTML_SUFFIXES)
            )

        # An EPUB has no pages — it reflows — so the limit is spent in
        # characters instead, at the rate a real book measures. Chapters are
        # kept whole: stopping mid-chapter to hit an exact count would cut a
        # sentence for no gain, since the limit is a budget and not a boundary.
        text_allowance = _character_allowance(_limit(limit))
        parts: list[str] = []
        budget = MAX_UNPACKED_BYTES
        kept_chapters = 0
        for name in names:
            try:
                raw = _read_capped(archive, name, budget)
            except (KeyError, UnreadableBook):
                logger.debug("%s: chapter %s is missing or oversized", path.name, name)
                continue
            budget -= len(raw)
            markdown = html_to_markdown(raw.decode("utf-8", errors="replace"))
            if markdown.strip():
                parts.append(markdown)
                kept_chapters += 1
            if budget <= 0:
                logger.warning("%s exceeded the unpack budget; truncated", path.name)
                break
            if text_allowance and sum(len(p) for p in parts) >= text_allowance:
                logger.info(
                    "%s: stopping after %d of %d chapters",
                    path.name,
                    kept_chapters,
                    len(names),
                )
                break

    if not parts:
        raise UnreadableBook(f"{path.name} contains no readable chapters")
    body = "\n\n".join(parts)
    if kept_chapters < len(names):
        body = append_note(body, kept=kept_chapters, total=len(names))
    return body, len(names)


def read_mobi(path: Path, *, limit: int | None = None) -> tuple[str, int]:
    """A MOBI or AZW3 as Markdown, via MuPDF, and its page count.

    The PalmDB header is checked here rather than trusted from the caller.
    MuPDF sniffs content and will cheerfully open an HTML file that happens to
    be named ``.mobi`` — which is precisely how a saved paywall interstitial
    would enter the library as a one-paragraph book.
    """
    import pymupdf
    import pymupdf4llm

    from app.core.paths import MOBI_TYPES, PALM_TYPE_OFFSET

    with open(path, "rb") as handle:
        header = handle.read(PALM_TYPE_OFFSET + 8)
    if header.startswith(DRM_MAGIC):
        # Amazon's encrypted container. The PalmDB header behind it is intact,
        # so every structural check passes and MuPDF then fails with a generic
        # "could not open" — which reads like a bug in the parser rather than
        # a book nothing can read. 3 of 10 MOBI-family files in the reference
        # library are these, so the distinction is worth one branch.
        # Diagnosis first, filename last. Books from an archive carry
        # hundred-character names, and every consumer of this string truncates
        # — the sidebar, the log line, the job's last_error column — so a
        # message that opens with the name says nothing in the space it gets.
        raise UnreadableBook(
            "DRM-protected (Amazon encrypted container); no tool can read it. "
            f"A DRM-free copy would ingest normally. File: {path.name}"
        )
    if header[PALM_TYPE_OFFSET : PALM_TYPE_OFFSET + 8] not in MOBI_TYPES:
        raise UnreadableBook(
            f"{path.name} has no PalmDB header; the extension is a lie"
        )

    try:
        document = pymupdf.open(path)
    except Exception as exc:  # noqa: BLE001 - MuPDF raises several types
        raise UnreadableBook(
            f"{path.name} could not be opened; DRM-protected books cannot be "
            f"read by any tool ({exc})"
        ) from exc

    with document:
        if document.needs_pass:
            raise UnreadableBook(f"{path.name} is encrypted")
        pages = document.page_count
        kept = page_budget(pages, _limit(limit))
        try:
            markdown = pymupdf4llm.to_markdown(
                document,
                pages=list(range(kept)) if kept < pages else None,
                show_progress=False,
            )
        except Exception as exc:  # noqa: BLE001
            raise UnreadableBook(f"{path.name} could not be converted: {exc}") from exc

    if not markdown.strip():
        raise UnreadableBook(f"{path.name} holds no extractable text")
    return append_note(markdown, kept=kept, total=pages), pages


def mobi_metadata(path: Path) -> BookMetadata:
    """Whatever MuPDF recovers from the book's header."""
    import pymupdf

    try:
        with pymupdf.open(path) as document:
            raw = dict(document.metadata or {})
    except Exception:  # noqa: BLE001 - absent metadata is not an error
        return BookMetadata()

    title = (raw.get("title") or "").strip() or None
    author = (raw.get("author") or "").strip()
    authors = [author] if author else []
    return BookMetadata(title, authors, None)
