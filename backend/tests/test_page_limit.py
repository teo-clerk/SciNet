"""Reading only the front of a very long document.

The limit exists for compute, not for context windows: the downstream caps
(``MAX_DOC_CHARS``, the tagger's ``MAX_INPUT_CHARS``) already bounded what
reaches a model. What was unbounded was tier 2, which transcribes one page at a
time through a vision model — a 731-page scan was killed after seven hours,
unfinished, with 435 documents queued behind it.
"""

from __future__ import annotations

import pymupdf
import pytest

from app.core.config import Settings
from app.services.parse import tier0_pymupdf
from app.services.parse.limits import (
    append_note,
    full_length,
    page_budget,
    was_truncated,
)


def build_pdf(path, pages: int, text: str = "Chapter text on this page."):
    doc = pymupdf.open()
    for index in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {index + 1}. {text}")
    doc.save(str(path))
    doc.close()
    return path


# --- the budget itself ---------------------------------------------------


def test_a_short_document_is_read_whole():
    assert page_budget(24, 80) == 24


def test_a_long_document_is_cut_to_the_limit():
    assert page_budget(731, 80) == 80


def test_zero_means_no_limit():
    """So the cap can be switched off without a second code path."""
    assert page_budget(731, 0) == 731
    assert page_budget(731, -1) == 731


# --- the note ------------------------------------------------------------


def test_no_note_when_nothing_was_cut():
    body = "The whole document."
    assert append_note(body, kept=24, total=24) == body
    assert not was_truncated(body)


def test_the_note_records_both_numbers():
    marked = append_note("Front of the book.", kept=80, total=731)

    assert "80 of 731 pages" in marked
    assert was_truncated(marked)
    assert full_length(marked) == 731


def test_the_note_travels_in_the_markdown():
    """Every later stage reads the file, not the database row."""
    marked = append_note("Front of the book.", kept=80, total=731)

    assert marked.startswith("Front of the book.")
    assert marked.rstrip().endswith("]")


# --- tier 0 --------------------------------------------------------------


@pytest.fixture
def limited(monkeypatch, tmp_path):
    settings = Settings(data_dir=tmp_path, max_parse_pages=5)
    monkeypatch.setattr(tier0_pymupdf, "get_settings", lambda: settings)
    return settings


def test_tier0_stops_at_the_limit(limited, tmp_path):
    path = build_pdf(tmp_path / "book.pdf", pages=40)

    result = tier0_pymupdf.parse(path)

    assert result.pages_parsed == 5
    assert result.truncated
    assert was_truncated(result.markdown)
    assert "Page 5." in result.markdown
    assert "Page 6." not in result.markdown


def test_the_recorded_page_count_stays_truthful(limited, tmp_path):
    """A truncated book is still a 40-page book, and the sidebar says so."""
    path = build_pdf(tmp_path / "book.pdf", pages=40)

    result = tier0_pymupdf.parse(path)

    assert result.page_count == 40
    assert full_length(result.markdown) == 40


def test_a_paper_under_the_limit_is_untouched(limited, tmp_path):
    path = build_pdf(tmp_path / "paper.pdf", pages=4)

    result = tier0_pymupdf.parse(path)

    assert result.pages_parsed == 4
    assert not result.truncated
    assert not was_truncated(result.markdown)


def test_the_probe_reads_only_what_the_parse_would(limited, tmp_path):
    """The gate must judge the pages that will actually be kept.

    Deciding the tier from two hundred scanned plates nobody will read sends a
    book to the VLM on the strength of pages the parse would have discarded.
    """
    path = build_pdf(tmp_path / "book.pdf", pages=40)

    probe = tier0_pymupdf.probe_pdf(path)

    assert probe.page_count == 40, "the count is of the document, not the sample"
    assert "Page 5." in probe.text
    assert "Page 6." not in probe.text


# --- the interaction that would otherwise break ---------------------------


def test_a_truncated_book_is_still_treated_as_a_book():
    """Truncation is the operation that makes a book short.

    The synopsis ladder decides a document is a book by length, so a 700-page
    scan cut to eighty pages of sparse OCR would come back under the threshold
    and be read as a preprint — returning chapter one's opening paragraph as
    the abstract, which is the bug the length test exists to prevent.
    """
    from app.services.metadata.synopsis import unlabelled_abstract

    short_book = "\n\n".join(
        "In this chapter the argument is developed at length and with care, "
        "returning to the theme announced in the preface and extending it. " * 3
        for _ in range(6)
    )

    assert unlabelled_abstract(short_book) is not None, "fixture must be paper-shaped"
    assert unlabelled_abstract(append_note(short_book, kept=80, total=731)) is None


def test_the_note_never_becomes_the_abstract():
    from app.services.metadata.synopsis import find_synopsis, is_furniture

    note = "[Document truncated at 80 of 731 pages to bound parsing cost.]"
    assert is_furniture(note)

    body = "\n\n".join(
        "The organism is a network of networks whose coupling changes with "
        "state, and the argument of this chapter follows that thread. " * 3
        for _ in range(8)
    )
    found = find_synopsis(append_note(body, kept=80, total=731))

    assert found is not None
    assert "truncated at" not in found.text


# --- the gate, which the limit and the library both stress ----------------


def test_per_page_metrics_divide_by_pages_actually_read():
    """A sampled document must not be judged as though the rest were blank.

    Introduced by the page limit itself: the probe reads eighty pages of a
    731-page book but reports the true count, so dividing by page_count gave a
    ninth of the real text density and failed the book as "insufficient text".
    """
    from app.services.parse.quality import TextProbe, _metrics

    text = "Real prose on the page. " * 400

    whole = _metrics(TextProbe(text=text, page_count=80, pages_sampled=80))
    sampled = _metrics(TextProbe(text=text, page_count=731, pages_sampled=80))

    assert sampled["chars_per_page"] == whole["chars_per_page"]


def test_an_ocrd_scan_is_not_sent_to_the_vision_model():
    """A scanned book with a good text layer is every page image and fine.

    12% of the sampled library — all books — failed on the image ratio alone
    while yielding 300 to 2,900 characters a page. Transcribing those costs
    8-15 s/page to reproduce text that was already correct.
    """
    from app.services.parse.quality import TextProbe, assess

    # ~2,100 characters a page, the density measured on real books here.
    probe = TextProbe(
        text="The organism is a network of networks, and the argument runs. " * 2800,
        page_count=80,
        pages_sampled=80,
        font_count=3,
        image_area_ratio=1.0,
    )

    report = assess(probe)

    assert "page_is_image" not in report.reasons
    assert report.passed


def test_a_scan_with_no_text_layer_still_escalates():
    """The rule is narrowed, not removed."""
    from app.services.parse.quality import TextProbe, assess

    report = assess(
        TextProbe(text="", page_count=80, pages_sampled=80, image_area_ratio=1.0)
    )

    assert "page_is_image" in report.reasons
    assert not report.passed


def test_a_stray_caption_on_a_scan_still_escalates():
    """The case the image rule was written for: text that is not the document."""
    from app.services.parse.quality import TextProbe, assess

    report = assess(
        TextProbe(
            text="Figure 1. The apparatus.\n" * 4,
            page_count=80,
            pages_sampled=80,
            font_count=1,
            image_area_ratio=1.0,
        )
    )

    assert "page_is_image" in report.reasons
    assert not report.passed
