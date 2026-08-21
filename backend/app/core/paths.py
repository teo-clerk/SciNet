"""Filesystem safety helpers.

The API exposes an endpoint that hands a path to ``xdg-open``. Any path that
reaches the shell must be proven to live inside the library root first, with
symlinks resolved — see the "path traversal" edge case in the design plan.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path


class UnsafePathError(ValueError):
    """Raised when a path escapes its permitted root."""


def resolve_within(candidate: Path | str, root: Path) -> Path:
    """Resolve ``candidate`` and assert it lives under ``root``.

    Symlinks are followed before the containment check, so a symlink inside the
    library that points outside of it is rejected rather than followed.
    """
    root_real = Path(root).resolve(strict=False)
    path_real = Path(candidate).resolve(strict=False)

    if not path_real.is_relative_to(root_real):
        raise UnsafePathError(f"path escapes library root: {candidate!r}")
    return path_real


PDF_MAGIC = b"%PDF-"


def is_pdf(path: Path) -> bool:
    """Is this a PDF?

    Checked by content, falling back to the extension when the file cannot be
    read. Papers downloaded from arXiv routinely arrive named after their
    identifier with no extension at all — `2504.15673` is a perfectly good PDF
    — and an extension-only test drops them from the library without saying
    anything, which is worst in exactly the small collections where one missing
    paper is a noticeable hole.
    """
    try:
        with open(path, "rb") as handle:
            if handle.read(len(PDF_MAGIC)) == PDF_MAGIC:
                return True
    except OSError:
        # Unreadable or vanished: fall back to the name rather than guessing.
        return path.suffix.lower() == ".pdf"
    # Readable and not a PDF. A .pdf extension on a non-PDF is a lie, and
    # trusting it costs a wasted parse job and a permanent piece of noise.
    return False


#: Zip-based container formats share this signature, so it narrows but never
#: settles which one a file is.
ZIP_MAGIC = b"PK\x03\x04"
DOCX_MAGIC = ZIP_MAGIC
#: DjVu's IFF signature.
DJVU_MAGIC = b"AT&T"
#: MOBI and AZW3 are both PalmDB databases whose type and creator sit at byte
#: 60. AZW3 differs from MOBI in how its payload is compressed, not in its
#: container, so one check covers both.
PALM_TYPE_OFFSET = 60
MOBI_TYPES = (b"BOOKMOBI", b"TEXtREAd")


class DocumentKind(StrEnum):
    """What a library file is, and therefore how it must be read."""

    PDF = "pdf"
    TEXT = "text"
    MARKDOWN = "markdown"
    DOCX = "docx"
    EPUB = "epub"
    MOBI = "mobi"
    DJVU = "djvu"


# Extensions we will open when the content check is inconclusive. PDF, DjVu and
# MOBI are deliberately absent: each is recognised by its magic bytes, because
# papers from arXiv routinely arrive with no extension at all and a book
# downloaded from an archive is often named after its catalogue number.
TEXT_EXTENSIONS = {".txt": DocumentKind.TEXT, ".md": DocumentKind.MARKDOWN}
#: Zip containers, which magic alone cannot tell apart.
ZIP_EXTENSIONS = {".docx": DocumentKind.DOCX, ".epub": DocumentKind.EPUB}
SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".djvu",
    ".djv",
    ".mobi",
    ".azw3",
    *ZIP_EXTENSIONS,
    *TEXT_EXTENSIONS,
}

#: Human names for the formats, used in messages the operator actually reads.
#: A rejection that says "wrong signature" is actionable; one that prints
#: b"PK\x03\x04" at them is not.
FORMAT_NAMES = {
    ".pdf": "PDF",
    ".docx": "Word document",
    ".epub": "EPUB book",
    ".djvu": "DjVu document",
    ".djv": "DjVu document",
    ".mobi": "MOBI book",
    ".azw3": "AZW3 book",
    ".txt": "text file",
    ".md": "Markdown file",
}


#: What each format's reader needs to see before it will accept a file. Only
#: consulted for uploads, where there is no file on disk to sniff yet.
MAGIC_BY_EXTENSION = {
    ".pdf": (PDF_MAGIC,),
    ".djvu": (DJVU_MAGIC,),
    ".djv": (DJVU_MAGIC,),
    ".docx": (ZIP_MAGIC,),
    ".epub": (ZIP_MAGIC,),
}


def _head(path: Path, size: int) -> bytes | None:
    """The first ``size`` bytes, or None if the file cannot be read yet."""
    try:
        with open(path, "rb") as handle:
            return handle.read(size)
    except OSError:
        return None


def classify_document(path: Path) -> DocumentKind | None:
    """What kind of document is this, or None if we cannot ingest it.

    Content first, extension second, for the same reason ``is_pdf`` works that
    way: the name is a claim and the bytes are evidence. Zip-based formats are
    the exception — ``.docx`` and ``.epub`` are both zips, so the signature
    only narrows the field and the extension has to settle it. A mislabelled
    zip fails in the extractor rather than here, where telling them apart would
    mean unpacking the archive on every scan of the library.
    """
    head = _head(path, PALM_TYPE_OFFSET + 8)
    suffix = path.suffix.lower()

    if head is None:
        # Unreadable or not there yet — the watcher classifies paths from
        # filesystem events, which arrive while the file is still being copied.
        # Trust the name here; the extractor is the one that has to succeed,
        # and it runs once the file settles.
        if suffix == ".pdf":
            return DocumentKind.PDF
        return _by_extension(suffix)

    if head.startswith(PDF_MAGIC):
        return DocumentKind.PDF
    if head.startswith(DJVU_MAGIC):
        return DocumentKind.DJVU
    if head[PALM_TYPE_OFFSET : PALM_TYPE_OFFSET + 8] in MOBI_TYPES:
        return DocumentKind.MOBI
    if head.startswith(ZIP_MAGIC):
        return ZIP_EXTENSIONS.get(suffix)

    # Readable, and none of the container signatures matched. A .pdf extension
    # on a non-PDF is a lie, and trusting it costs a wasted parse job and a
    # permanent piece of noise; the same holds for every other binary format.
    return TEXT_EXTENSIONS.get(suffix)


def _by_extension(suffix: str) -> DocumentKind | None:
    if suffix in (".djvu", ".djv"):
        return DocumentKind.DJVU
    if suffix in (".mobi", ".azw3"):
        return DocumentKind.MOBI
    return ZIP_EXTENSIONS.get(suffix) or TEXT_EXTENSIONS.get(suffix)


def markdown_path_for(paper_id: int, markdown_dir: Path) -> Path:
    """Shard markdown output so no single directory holds thousands of files."""
    shard = f"{paper_id // 1000:03d}"
    return markdown_dir / shard / f"{paper_id:06d}.md"
