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


DOCX_MAGIC = b"PK\x03\x04"


class DocumentKind(StrEnum):
    """What a library file is, and therefore how it must be read."""

    PDF = "pdf"
    TEXT = "text"
    MARKDOWN = "markdown"
    DOCX = "docx"


# Extensions we will open when the content check is inconclusive. PDFs are
# deliberately absent: they are recognised by their magic bytes, because papers
# from arXiv routinely arrive with no extension at all.
TEXT_EXTENSIONS = {".txt": DocumentKind.TEXT, ".md": DocumentKind.MARKDOWN}
SUPPORTED_EXTENSIONS = {".pdf", ".docx", *TEXT_EXTENSIONS}


def classify_document(path: Path) -> DocumentKind | None:
    """What kind of document is this, or None if we cannot ingest it.

    Content first, extension second, for the same reason ``is_pdf`` works that
    way: the name is a claim and the bytes are evidence. A ``.docx`` is a zip,
    so its magic only narrows the field — the extension settles it, and a
    mislabelled zip fails later in the extractor rather than here, where we
    cannot tell the difference without unpacking it.
    """
    if is_pdf(path):
        return DocumentKind.PDF

    suffix = path.suffix.lower()
    if suffix == ".docx":
        try:
            with open(path, "rb") as handle:
                if handle.read(len(DOCX_MAGIC)) != DOCX_MAGIC:
                    # Readable and not a zip: the extension is a lie.
                    return None
        except OSError:
            # Unreadable or not there yet — the watcher classifies paths from
            # filesystem events, which arrive while the file is still being
            # copied. Trust the name here as ``is_pdf`` does; the extractor is
            # the one that has to succeed, and it runs once the file settles.
            pass
        return DocumentKind.DOCX

    return TEXT_EXTENSIONS.get(suffix)


def markdown_path_for(paper_id: int, markdown_dir: Path) -> Path:
    """Shard markdown output so no single directory holds thousands of files."""
    shard = f"{paper_id // 1000:03d}"
    return markdown_dir / shard / f"{paper_id:06d}.md"
