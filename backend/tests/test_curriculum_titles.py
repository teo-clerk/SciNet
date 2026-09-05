"""A work the pipeline could not name is never the recommendation.

Found on the benchmark: the recommended entry point of the largest region
was "Paper Title (use style: paper title)", a template placeholder the PDF
carried in its Title field. It was the most central work, and centrality is
the largest weight — nothing in the score can see that a title is furniture,
so the filter sits in front of the ranking rather than inside it.
"""

from __future__ import annotations

from app.services.project.curriculum import Candidate, presentable


def _candidate(title: str | None) -> Candidate:
    return Candidate(paper_id=1, title=title, abstract=None, year=None, pages=None)


def test_a_missing_or_blank_title_is_not_presentable():
    assert not presentable(_candidate(None))
    assert not presentable(_candidate(""))
    assert not presentable(_candidate("   "))


def test_a_template_placeholder_is_not_presentable():
    assert not presentable(_candidate("Paper Title (use style: paper title)"))
    assert not presentable(
        _candidate("Sample manuscript showing specifications and style")
    )
    assert not presentable(_candidate("Author template for journal articles"))


def test_an_ordinary_title_is_presentable():
    assert presentable(_candidate("On Liberty"))
    assert presentable(
        _candidate("A Template-Based Approach to Protein Structure Prediction")
    )
