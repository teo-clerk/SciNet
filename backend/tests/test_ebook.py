"""EPUB, MOBI and AZW3 ingestion."""

from __future__ import annotations

import zipfile

import pytest

from app.core.paths import DocumentKind, classify_document
from app.services.metadata.extract import extract_from_document
from app.services.parse.ebook import (
    UnreadableBook,
    epub_metadata,
    read_epub,
    read_mobi,
)
from app.services.parse.html_text import html_to_markdown
from app.services.parse.text_documents import UnreadableDocument, parse_text_document
from tests.fixtures.documents import build_epub, build_mobi

CHAPTERS = [
    ("A Role for History", "History, viewed as a repository for more than anecdote."),
    ("The Route to Normal Science", "Normal science means research firmly based."),
    ("Anomaly and Discovery", "Discovery commences with the awareness of anomaly."),
]


@pytest.fixture
def epub(tmp_path):
    return build_epub(
        tmp_path / "kuhn.epub",
        title="The Structure of Scientific Revolutions",
        author="Thomas S. Kuhn",
        year="1962",
        chapters=CHAPTERS,
    )


def test_epub_is_recognised_by_extension_not_by_zip_magic(epub, tmp_path):
    # A .docx is also a zip. The signature narrows, the extension settles.
    docx = tmp_path / "notes.docx"
    docx.write_bytes(epub.read_bytes())

    assert classify_document(epub) is DocumentKind.EPUB
    assert classify_document(docx) is DocumentKind.DOCX


def test_epub_chapters_keep_their_headings(epub):
    markdown, chapters = read_epub(epub)

    assert chapters == len(CHAPTERS)
    for heading, body in CHAPTERS:
        assert f"# {heading}" in markdown
        assert body in markdown


def test_epub_chapters_follow_the_spine_not_the_zip_order(epub):
    markdown, _ = read_epub(epub)

    positions = [markdown.index(heading) for heading, _ in CHAPTERS]
    assert positions == sorted(positions)


def test_epub_metadata_comes_from_the_package_document(epub):
    meta = epub_metadata(epub)

    assert meta.title == "The Structure of Scientific Revolutions"
    assert meta.authors == ["Thomas S. Kuhn"]
    assert meta.year == 1962


def test_a_books_own_metadata_beats_the_text_heuristics(epub):
    markdown, _ = read_epub(epub)

    extracted = extract_from_document(epub, parsed_text=markdown)

    # Without the package document the title would be the first chapter's
    # heading, which is a section of the book rather than the book.
    assert extracted.title == "The Structure of Scientific Revolutions"
    assert extracted.authors == ["Thomas S. Kuhn"]
    # A book's front matter lists every printing; the stated date is the work's.
    assert extracted.year == 1962


def test_a_corrupt_epub_says_so(tmp_path):
    path = tmp_path / "broken.epub"
    path.write_bytes(b"PK\x03\x04 and then nothing that is a zip")

    with pytest.raises(UnreadableBook):
        read_epub(path)


def test_an_epub_without_a_package_document_says_so(tmp_path):
    path = tmp_path / "bare.epub"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("readme.txt", "not a book")

    with pytest.raises(UnreadableBook, match="container.xml"):
        read_epub(path)


def test_epub_parses_through_the_pipeline_entry_point(epub):
    result = parse_text_document(epub, DocumentKind.EPUB)

    assert result.tier == 0
    assert result.parser == "epub-xhtml"
    assert result.page_count == len(CHAPTERS)


@pytest.fixture
def mobi(tmp_path):
    return build_mobi(
        tmp_path / "geb.mobi",
        title="Godel Escher Bach",
        html=(
            "<html><body><h1>An Eternal Golden Braid</h1>"
            "<p>This book is about how animate beings can come out of "
            "inanimate matter, and what a self could possibly be.</p>"
            "</body></html>"
        ),
    )


def test_mobi_is_recognised_by_its_palmdb_header(mobi, tmp_path):
    # Named wrongly on purpose: the bytes decide, so an archive download that
    # lost its extension is still ingested.
    renamed = tmp_path / "B00CATALOGUE"
    renamed.write_bytes(mobi.read_bytes())

    assert classify_document(mobi) is DocumentKind.MOBI
    assert classify_document(renamed) is DocumentKind.MOBI


def test_azw3_takes_the_same_path_as_mobi(tmp_path, mobi):
    azw3 = tmp_path / "geb.azw3"
    azw3.write_bytes(mobi.read_bytes())

    assert classify_document(azw3) is DocumentKind.MOBI
    result = parse_text_document(azw3, DocumentKind.MOBI)

    assert result.parser == "mupdf-mobi"
    assert "Eternal Golden Braid" in result.markdown


def test_a_mobi_that_is_not_one_is_rejected(tmp_path):
    path = tmp_path / "fake.mobi"
    path.write_bytes(b"<html>Paywall</html>" + b"\x00" * 200)

    assert classify_document(path) is None
    with pytest.raises(UnreadableDocument):
        parse_text_document(path, DocumentKind.MOBI)


def test_html_conversion_keeps_structure_and_drops_presentation():
    markdown = html_to_markdown(
        "<html><head><style>p{color:red}</style></head><body>"
        "<h2>Section</h2><p>Prose with <em>emphasis</em>.</p>"
        "<ul><li>one</li><li>two</li></ul><script>alert(1)</script></body></html>"
    )

    assert "## Section" in markdown
    assert "Prose with emphasis." in markdown
    assert "- one" in markdown
    assert "color:red" not in markdown
    assert "alert" not in markdown


def test_a_drm_protected_book_says_so_plainly(tmp_path, mobi):
    """3 of 10 MOBI-family files in the reference library are encrypted.

    The DRM wrapper sits ahead of an intact PalmDB header, so every structural
    check passes and MuPDF fails with a generic "could not open" — which reads
    like a broken parser rather than a book nothing can read.
    """
    locked = tmp_path / "antifragile.azw3"
    locked.write_bytes(b"CR!" + mobi.read_bytes()[3:])

    with pytest.raises(UnreadableBook, match="DRM-protected"):
        read_mobi(locked)


def test_a_drm_free_book_of_the_same_shape_still_reads(mobi):
    text, _pages = read_mobi(mobi)

    assert "Eternal Golden Braid" in text
