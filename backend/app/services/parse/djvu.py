"""Reading the text layer out of a DjVu document.

DjVu is a scanned-page format, so a ``.djvu`` in a personal library is nearly
always a book someone photographed or downloaded from an archive. What makes it
readable at all is that the scanning tools store their OCR output alongside the
images, in a ``TXTa`` (raw) or ``TXTz`` (BZZ-compressed) chunk per page. That
text is what this module extracts.

A DjVu with no text layer holds only images, and there is nothing to recover
without OCR — the file is reported unreadable rather than ingested empty, so it
shows up as a failure the operator can act on instead of a blank node on the
map.

The container is IFF: an ``AT&T`` signature followed by nested ``FORM`` chunks.
Single-page files are one ``FORM:DJVU``; bundled books are a ``FORM:DJVM``
holding one ``FORM:DJVU`` per page. Walking the tree finds both without caring
which it was given.
"""

from __future__ import annotations

import logging
import struct
from pathlib import Path

from app.services.parse.djvu_bzz import CorruptStream, decompress
from app.services.parse.limits import append_note

logger = logging.getLogger(__name__)

MAGIC = b"AT&T"
_CHUNK_HEADER = 8
#: A page's text chunk opens with a 24-bit length, then that many UTF-8 bytes,
#: then the zone tree (page/line/word rectangles) which we do not need.
_TEXT_LENGTH_BYTES = 3


class NotADjVu(ValueError):
    """The file is not a DjVu document."""


def _walk(buffer: bytes, start: int, end: int) -> list[tuple[bytes, int, int]]:
    """Flatten the chunk tree to (id, offset, length), descending into FORMs."""
    found: list[tuple[bytes, int, int]] = []
    offset = start
    while offset + _CHUNK_HEADER <= end:
        chunk_id = buffer[offset : offset + 4]
        (length,) = struct.unpack_from(">I", buffer, offset + 4)
        body = offset + _CHUNK_HEADER
        if body + length > end:
            # Truncated download. Everything already collected is still good,
            # which for a part-downloaded book is most of it.
            logger.warning("DjVu chunk %s runs past the end of the file", chunk_id)
            break

        if chunk_id == b"FORM":
            found.append((buffer[body : body + 4], body + 4, length - 4))
            found.extend(_walk(buffer, body + 4, body + length))
        else:
            found.append((chunk_id, body, length))
        # Chunks are padded to an even offset.
        offset = body + length + (length & 1)
    return found


def _page_text(buffer: bytes, offset: int, length: int, compressed: bool) -> str:
    payload = buffer[offset : offset + length]
    if compressed:
        payload = decompress(payload)
    if len(payload) < _TEXT_LENGTH_BYTES:
        return ""
    size = int.from_bytes(payload[:_TEXT_LENGTH_BYTES], "big")
    body = payload[_TEXT_LENGTH_BYTES : _TEXT_LENGTH_BYTES + size]
    return body.decode("utf-8", errors="replace")


def read_djvu_text(path: Path, *, limit: int | None = None) -> tuple[str, int]:
    """Every page's OCR text, and the page count.

    Pages are joined by blank lines so the chunker sees them as paragraphs. No
    heading structure is invented: OCR output has none, and guessing one from
    line lengths would put scanning artefacts on the map as chapter titles.
    """
    if limit is None:
        from app.core.config import get_settings

        limit = get_settings().max_parse_pages

    buffer = Path(path).read_bytes()
    if not buffer.startswith(MAGIC):
        raise NotADjVu(f"{Path(path).name} has no AT&T signature")

    chunks = _walk(buffer, len(MAGIC), len(buffer))
    if not chunks:
        raise NotADjVu(f"{Path(path).name} contains no DjVu chunks")

    # One INFO per page, whether or not that page carries text.
    pages = sum(1 for chunk_id, _, _ in chunks if chunk_id == b"INFO")

    texts: list[str] = []
    # Counted separately from ``texts``: a scanned page whose OCR found nothing
    # is still a page read. Using the length of ``texts`` as the position would
    # report a book with blank plates as truncated when it was read in full.
    pages_read = 0
    stopped_early = False
    for chunk_id, offset, length in chunks:
        if chunk_id not in (b"TXTa", b"TXTz"):
            continue
        if limit > 0 and pages_read >= limit:
            # Decoding is cheap here compared with a VLM, but a scanned book
            # still runs to hundreds of pages and the later ones say no more
            # about what it is than the first eighty.
            logger.info("%s: stopping after %d pages", path.name, limit)
            stopped_early = True
            break
        pages_read += 1
        try:
            text = _page_text(buffer, offset, length, chunk_id == b"TXTz")
        except CorruptStream as exc:
            # One unreadable page must not cost the whole book.
            logger.warning("skipping a damaged text chunk in %s: %s", path, exc)
            continue
        if text.strip():
            texts.append(text.strip())

    if not texts and any(c == b"DIRM" for c, _, _ in chunks) and pages == 0:
        # A DJVM whose DIRM points at sibling files rather than embedding them.
        raise NotADjVu(
            f"{Path(path).name} is an indirect DjVu index; ingest the bundled "
            "document instead"
        )

    body = "\n\n".join(texts)
    if stopped_early:
        body = append_note(body, kept=pages_read, total=max(pages, pages_read))
    return body, max(pages, pages_read)
