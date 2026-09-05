"""Labelled front matter in a text or Markdown document.

A reader's own notes, and the bundled sample library, open with lines like
``Author: John Stuart Mill`` and ``Year: 1859``. The four-digit scrape that
finds a preprint's year stops at 1980 on purpose — an unlabelled "1859" on a
page is a citation far more often than a date — so an explicit label is the
only way an older work gets its year, and it must beat the scrape.
"""

from __future__ import annotations

from app.models import MetaSource
from app.services.metadata.extract import extract_from_document, front_matter

ESSAY = """# On Liberty

Author: John Stuart Mill
Year: 1859
Source: Project Gutenberg ebook #34901, the 1859 text; public domain.

The subject of this Essay is not the so-called Liberty of the Will, so
unfortunately opposed to the misnamed doctrine of Philosophical Necessity; but
Civil, or Social Liberty: the nature and limits of the power which can be
legitimately exercised by society over the individual. A question seldom
stated, and hardly ever discussed, in general terms, but which profoundly
influences the practical controversies of the age by its latent presence.
"""


def test_labelled_author_and_year_are_read():
    authors, year = front_matter(ESSAY)
    assert authors == ["John Stuart Mill"]
    assert year == 1859


def test_labels_are_case_insensitive_and_tolerate_emphasis():
    authors, year = front_matter(
        "# T\n\n**Authors:** A. Person and B. Other\nPUBLISHED: 1690\n"
    )
    assert authors == ["A. Person", "B. Other"]
    assert year == 1690


def test_a_labelled_year_may_be_ancient_but_not_absurd():
    """Epictetus wrote around 125; a labelled year is trusted as written."""
    assert front_matter("# T\n\nAuthor: Epictetus\nYear: 125\n")[1] == 125
    assert front_matter("# T\n\nYear: 3000\n")[1] is None
    assert front_matter("# T\n\nYear: 0\n")[1] is None
    assert front_matter("# T\n\nYear:\n")[1] is None


def test_a_year_in_the_body_is_not_front_matter():
    text = "# T\n\nSome prose.\n\n" + "x" * 1300 + "\nYear: 1859\n"
    assert front_matter(text) == ([], None)


def test_nothing_labelled_means_nothing_found():
    assert front_matter(None) == ([], None)
    assert front_matter("# Title\n\nJust prose from 1859 about things.") == ([], None)


def test_the_metadata_stage_files_an_old_essay_under_its_own_year(tmp_path):
    path = tmp_path / "Politics_06-mill-on-liberty.md"
    path.write_text(ESSAY, encoding="utf-8")

    meta = extract_from_document(path, parsed_text=ESSAY)

    assert meta.title == "On Liberty"
    assert meta.authors == ["John Stuart Mill"]
    assert meta.year == 1859
    assert meta.field_sources["year"] == MetaSource.HEURISTIC
    assert meta.field_sources["authors"] == MetaSource.HEURISTIC
    assert meta.abstract is not None and meta.abstract.startswith("The subject")
