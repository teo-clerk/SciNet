"""Non-PDF documents entering the same pipeline.

A personal library is not all PDFs: it holds meeting notes, drafts, and papers
a colleague sent as a Word file. These formats are already text, so they skip
tier escalation entirely — the point of these tests is that everything
*downstream* of parsing cannot tell the difference.
"""

from __future__ import annotations

import pytest

from app.core.paths import DocumentKind, classify_document
from app.services.parse.text_documents import (
    UnreadableDocument,
    parse_text_document,
    read_docx,
)


def test_plain_text_is_read_as_a_document(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("A Study of Something\n\nBody text goes here.", encoding="utf-8")

    assert classify_document(path) is DocumentKind.TEXT
    result = parse_text_document(path, DocumentKind.TEXT)
    assert "A Study of Something" in result.markdown
    assert result.parser == "utf8-text"
    assert result.tier == 0


def test_markdown_keeps_its_headings(tmp_path):
    """The metadata stage reads ``#`` headings to find the title."""
    path = tmp_path / "review.md"
    path.write_text("# The Real Title\n\n## Abstract\n\nProse.", encoding="utf-8")

    result = parse_text_document(path, DocumentKind.MARKDOWN)
    assert result.markdown.startswith("# The Real Title")


def test_non_utf8_text_is_still_read(tmp_path):
    """One bad byte must not cost the library a whole document."""
    path = tmp_path / "legacy.txt"
    path.write_bytes("Café findings and results".encode("cp1252"))

    result = parse_text_document(path, DocumentKind.TEXT)
    assert "findings and results" in result.markdown


def test_empty_file_is_rejected(tmp_path):
    path = tmp_path / "blank.txt"
    path.write_text("   \n\n", encoding="utf-8")

    with pytest.raises(UnreadableDocument):
        parse_text_document(path, DocumentKind.TEXT)


def test_docx_headings_become_markdown(tmp_path):
    docx = pytest.importorskip("docx")

    path = tmp_path / "draft.docx"
    document = docx.Document()
    document.add_heading("Structural Constraints in Model Organisms", level=1)
    document.add_paragraph("An abstract-like opening paragraph with content.")
    document.add_heading("Methods", level=2)
    document.add_paragraph("What was done.")
    document.save(str(path))

    assert classify_document(path) is DocumentKind.DOCX
    result = parse_text_document(path, DocumentKind.DOCX)
    assert "# Structural Constraints in Model Organisms" in result.markdown
    assert "## Methods" in result.markdown
    assert result.parser == "python-docx"


def test_docx_title_is_recovered_by_the_metadata_stage(tmp_path):
    """The whole point: a .docx must produce a usable title like a PDF does."""
    docx = pytest.importorskip("docx")

    from app.services.metadata.extract import extract_from_document

    path = tmp_path / "paper.docx"
    document = docx.Document()
    document.add_heading("Epigenetic Drift in Long-Lived Cells", level=1)
    document.add_paragraph("Body prose that is long enough to look like text.")
    document.save(str(path))

    parsed = parse_text_document(path, DocumentKind.DOCX).markdown
    meta = extract_from_document(path, parsed_text=parsed)
    assert meta.title == "Epigenetic Drift in Long-Lived Cells"


def test_a_zip_named_docx_is_not_mistaken_for_a_document(tmp_path):
    """python-docx would raise deep in the pipeline; catch it at the door."""
    path = tmp_path / "broken.docx"
    path.write_bytes(b"not a zip at all")
    assert classify_document(path) is None


def test_unopenable_docx_raises_unreadable(tmp_path):
    path = tmp_path / "corrupt.docx"
    path.write_bytes(b"PK\x03\x04" + b"\x00" * 64)

    with pytest.raises(UnreadableDocument):
        read_docx(path)
