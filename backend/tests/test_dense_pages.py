"""A page that would take hours is read flat, and the Markdown says so.

Tier 0 is the cheap tier — roughly a second a page — and its cost model
assumed that a page's text layer is what it costs. It is not: the layout
engine also walks every vector path on the page, and a scatter plot drawn
point by point carried 1.3 million of them. One such page held the pipeline
for five and a half hours. The gate here counts paths, which is cheap, and
a page over the line is read as plain text: its prose survives, only its
layout is lost.
"""

from __future__ import annotations

import pymupdf

from app.services.parse import tier0_pymupdf
from app.services.parse.limits import MAX_PAGE_PATHS, append_plain_pages_note


def three_pages(path, dense_page: int | None = None):
    """A three-page PDF; optionally one page carrying too many paths."""
    doc = pymupdf.open()
    for number in range(1, 4):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {number}. Chapter text on this page.")
    if dense_page is not None:
        page = doc[dense_page - 1]
        xref = page.get_contents()[0]
        paths = b"0 0 m 1 1 l S\n" * (MAX_PAGE_PATHS + 500)
        doc.update_stream(xref, page.read_contents() + b"\n" + paths)
    doc.save(path)
    doc.close()
    return path


def test_a_path_dense_page_is_read_flat_not_waited_for(tmp_path):
    path = three_pages(tmp_path / "plot.pdf", dense_page=2)

    result = tier0_pymupdf.parse(path)

    assert result.pages_plain == (2,)
    assert "Page 1." in result.markdown
    assert "Page 2." in result.markdown, "the flat page keeps its prose"
    assert "Page 3." in result.markdown
    assert "Page(s) 2 were read as plain text" in result.markdown


def test_pages_stay_in_order_around_a_flat_one(tmp_path):
    path = three_pages(tmp_path / "plot.pdf", dense_page=2)

    markdown = tier0_pymupdf.parse(path).markdown

    assert (
        markdown.index("Page 1.")
        < markdown.index("Page 2.")
        < markdown.index("Page 3.")
    )


def test_a_flat_page_is_not_truncation(tmp_path):
    """The document was read to its end; the page count holds."""
    path = three_pages(tmp_path / "plot.pdf", dense_page=2)

    result = tier0_pymupdf.parse(path)

    assert result.page_count == 3
    assert result.pages_parsed == 3
    assert not result.truncated


def test_an_ordinary_document_lays_out_every_page(tmp_path):
    path = three_pages(tmp_path / "plain.pdf")

    result = tier0_pymupdf.parse(path)

    assert result.pages_plain == ()
    assert "plain text" not in result.markdown
    assert "Page 2." in result.markdown


def test_dense_pages_are_judged_within_the_page_budget_only(tmp_path):
    path = three_pages(tmp_path / "plot.pdf", dense_page=3)
    with pymupdf.open(path) as doc:
        assert tier0_pymupdf.dense_pages(doc, budget=3) == (2,)
        assert tier0_pymupdf.dense_pages(doc, budget=2) == ()


def test_the_note_names_every_page_and_is_absent_when_none():
    assert append_plain_pages_note("text", pages=()) == "text"
    noted = append_plain_pages_note("text", pages=(3, 6, 12))
    assert noted.startswith("text\n\n[Page(s) 3, 6, 12 were read as plain text")
