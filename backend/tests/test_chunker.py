"""Chunking, and the document/chunk split that keeps the map meaningful."""

from __future__ import annotations

from app.services.embed.chunker import (
    chunk_markdown,
    document_text,
    is_boilerplate,
    split_sections,
)

PAPER = """# A Study of Things

Some preamble text that sits before any heading and describes the work.

## Abstract

We present a method for learning representations of scientific documents.
The approach is evaluated across several downstream tasks and compared to
strong baselines drawn from the recent literature on the subject.

## 1 Introduction

Scientific literature grows faster than any individual can read it, which
motivates automated organisation of large personal collections of papers.

## References

[1] Someone. A different paper entirely. 2019.
[2] Someone Else. Another unrelated work. 2020.
"""


def test_sections_are_split_on_headings():
    titles = [s.title for s in split_sections(PAPER)]
    assert titles == ["A Study of Things", "Abstract", "1 Introduction", "References"]


def test_body_stays_with_its_heading():
    first = split_sections(PAPER)[0]
    assert first.title == "A Study of Things"
    assert "Some preamble text" in first.body


def test_text_before_the_first_heading_becomes_an_untitled_section():
    """Many parsers emit a title block before any Markdown heading."""
    sections = split_sections("Loose front matter.\n\n# Real Heading\n\nBody.")
    assert sections[0].title is None
    assert "Loose front matter." in sections[0].body
    assert sections[1].title == "Real Heading"


def test_markdown_without_headings_is_one_section():
    sections = split_sections("just a wall of text with no structure at all")
    assert len(sections) == 1 and sections[0].title is None


def test_empty_markdown_yields_no_sections():
    assert split_sections("   ") == []


def test_references_are_recognised_as_boilerplate():
    for title in (
        "References",
        "Bibliography",
        "Acknowledgements",
        "Appendix A",
        "Funding",
        "Conflict of Interest",
    ):
        assert is_boilerplate(title), title


def test_real_sections_are_not_boilerplate():
    for title in ("Introduction", "Methods", "Results", "Reference Frames"):
        assert not is_boilerplate(title), title


def test_chunks_exclude_the_bibliography():
    """Citations describe other papers; embedding them blurs this one."""
    text = " ".join(c.text for c in chunk_markdown(PAPER))
    assert "different paper entirely" not in text


def test_chunks_carry_their_section():
    sections = {c.section for c in chunk_markdown(PAPER)}
    assert "Abstract" in sections


def test_chunks_are_ordered():
    chunks = chunk_markdown(PAPER)
    assert [c.ord for c in chunks] == list(range(len(chunks)))


def test_long_sections_are_split_with_overlap():
    long_body = "# Head\n\n" + ("A sentence about the topic. " * 400)
    chunks = chunk_markdown(long_body, max_chars=600, overlap=100)
    assert len(chunks) > 1
    assert all(len(c.text) <= 700 for c in chunks)


def test_tiny_sections_are_dropped():
    assert chunk_markdown("## H\n\nshort.\n") == []


def test_document_text_prefers_structure_over_body():
    doc = document_text(
        title="A Study of Things",
        abstract="We present a method.",
        summary="A short LLM summary.",
        markdown=PAPER,
    )
    assert "A Study of Things" in doc
    assert "We present a method." in doc
    assert "Sections:" in doc
    # The body must not be pooled in — that is what collapses the clusters.
    assert "grows faster than any individual" not in doc


def test_document_text_omits_boilerplate_headings():
    doc = document_text(title="T", abstract=None, summary=None, markdown=PAPER)
    assert "References" not in doc


def test_document_text_survives_missing_fields():
    assert document_text(title=None, abstract=None, summary=None) == ""
    assert (
        document_text(title="Only a title", abstract=None, summary=None)
        == "Only a title"
    )


def test_document_text_is_bounded():
    doc = document_text(title="T", abstract="x" * 50_000, summary=None, max_chars=1000)
    assert len(doc) <= 1000
