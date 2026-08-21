"""The abstract ladder, and the books at the bottom of it."""

from __future__ import annotations

from app.services.metadata.extract import extract_from_document
from app.services.metadata.synopsis import Strategy, find_synopsis, is_prose
from app.services.parse.text_documents import parse_text_document

PAPER = """# Attention Is All You Need

Ashish Vaswani, Noam Shazeer

## Abstract

The dominant sequence transduction models are based on complex recurrent or
convolutional neural networks. We propose a new simple network architecture,
the Transformer, based solely on attention mechanisms, dispensing with
recurrence and convolutions entirely. Experiments show these models to be
superior in quality while being more parallelisable.

## Introduction

Recurrent neural networks have been firmly established as state of the art.
"""

BOOK_FRONT_MATTER = """# The Selfish Gene

Richard Dawkins

Oxford University Press

Copyright 1976. All rights reserved. No part of this book may be reproduced.
ISBN 0-19-857519-X. First published 1976. Printed in the United Kingdom.

## Contents

1 Why are people? 2 The replicators 3 Immortal coils 4 The gene machine

## Preface

This book should be read almost as though it were science fiction. It is
designed to appeal to the imagination. But it is not science fiction: it is
science. We are survival machines, robot vehicles blindly programmed to
preserve the selfish molecules known as genes. This is a truth which still
fills me with astonishment.

## 1 Why are people?

Intelligent life on a planet comes of age when it first works out the reason
for its own existence.
"""

# A book with no front matter worth the name: straight into chapters, which is
# how a scanned DjVu or a stripped EPUB usually arrives. Deliberately built at
# book length — that length is the signal the shape heuristic keys off, and a
# short fixture would test the paper path while claiming to test the book one.
_CHAPTER_SUBJECTS = (
    ("grid", "The typographic grid organises a page into columns and rows."),
    ("legibility", "Legibility depends on size, measure and leading together."),
    ("garalde", "A garalde face carries a different voice than a grotesque."),
    ("kerning", "Kerning adjusts the fit between two particular letterforms."),
    ("hierarchy", "Hierarchy tells the reader which part of a page to read."),
    ("ligature", "A ligature joins two letters whose shapes would otherwise clash."),
    ("counter", "The counter is the enclosed space inside a letter such as o."),
    ("baseline", "The baseline is the invisible line the letters are set upon."),
    ("colophon", "The colophon records who set the book and in what face."),
    ("imposition", "Imposition arranges pages so that a folded sheet reads in order."),
    ("widow", "A widow is a single line of a paragraph stranded on a new page."),
    ("rag", "The rag is the uneven edge left by text set without justification."),
)


def _long_book() -> str:
    chapters = []
    for index, (term, opening) in enumerate(_CHAPTER_SUBJECTS, start=1):
        body = " ".join(
            f"{opening} The {term} matters to the compositor because it decides "
            f"how the page is read, and the {term} is judged by eye rather than "
            f"by rule."
            for _ in range(100)
        )
        chapters.append(f"# Chapter {index}\n\n{body}")
    return "\n\n".join(chapters)


BOOK_NO_BLURB = _long_book()


def test_the_book_fixture_is_actually_book_length():
    """Guards the fixture, which is only a book because of how long it is."""
    from app.services.metadata.synopsis import MAX_PAPER_CHARS

    assert len(BOOK_NO_BLURB) > MAX_PAPER_CHARS


def test_a_paper_uses_its_labelled_abstract():
    result = find_synopsis(PAPER)

    assert result is not None
    assert result.strategy is Strategy.LABELLED
    assert "dispensing with" in result.text


def test_a_book_falls_through_to_its_preface():
    result = find_synopsis(BOOK_FRONT_MATTER)

    assert result is not None
    assert result.strategy is Strategy.PREFACE
    assert "survival machines" in result.text


def test_the_preface_beats_the_copyright_page():
    result = find_synopsis(BOOK_FRONT_MATTER)

    assert result is not None
    assert "All rights reserved" not in result.text
    assert "ISBN" not in result.text


def test_a_book_with_no_front_matter_gets_a_digest():
    result = find_synopsis(BOOK_NO_BLURB)

    assert result is not None
    assert result.strategy is Strategy.DIGEST


def test_book_length_switches_off_the_paper_shape_heuristic():
    """ "The first substantial paragraph is the abstract" is a paper's rule.

    Applied to a book it returns the opening of chapter one, which describes a
    twelfth of the book and calls it the whole.
    """
    from app.services.metadata.synopsis import unlabelled_abstract

    assert unlabelled_abstract(BOOK_NO_BLURB) is None
    # The same text, truncated to paper length, is fair game again.
    assert unlabelled_abstract(BOOK_NO_BLURB[:8000]) is not None


def test_the_digest_samples_across_the_document_not_just_the_front():
    """A book's first pages are its least informative."""
    result = find_synopsis(BOOK_NO_BLURB)

    assert result is not None
    half = len(_CHAPTER_SUBJECTS) // 2
    early = {term for term, _ in _CHAPTER_SUBJECTS[:half]}
    late = {term for term, _ in _CHAPTER_SUBJECTS[half:]}
    words = set(result.text.lower().split())

    # Something from each half has to survive, or the digest describes one
    # chapter and calls it the book.
    assert words & early, "nothing from the first half of the book"
    assert words & late, "nothing from the second half of the book"


def test_the_digest_is_about_the_length_of_an_abstract():
    """Books and papers must contribute comparably to the map.

    If a book's abstract field is three times a paper's, the difference lands
    in the document vector and the map starts clustering by format — a "books"
    region — which is the one thing it must never do.
    """
    from app.services.metadata.synopsis import MAX_SYNOPSIS_CHARS

    result = find_synopsis(BOOK_NO_BLURB)

    assert result is not None
    assert len(result.text) <= MAX_SYNOPSIS_CHARS


def test_a_contents_listing_is_not_prose():
    assert not is_prose("1 Why are people? 2 The replicators 3 Immortal coils")


def test_a_copyright_notice_is_not_prose():
    assert not is_prose(
        "Copyright 1976. All rights reserved. No part of this book may be "
        "reproduced in any form or by any means without permission. "
        "ISBN 0-19-857519-X. First published 1976. Printed in Great Britain."
    )


def test_empty_input_yields_nothing():
    assert find_synopsis("") is None
    assert find_synopsis(None) is None


def test_a_book_reaches_the_metadata_stage_with_an_abstract(tmp_path):
    from app.core.paths import DocumentKind
    from app.models import MetaSource

    path = tmp_path / "book.md"
    path.write_text(BOOK_NO_BLURB, encoding="utf-8")
    parsed = parse_text_document(path, DocumentKind.MARKDOWN)

    meta = extract_from_document(path, parsed_text=parsed.markdown)

    assert meta.abstract
    # Recorded as assembled rather than authored, so nothing downstream
    # mistakes it for something the writer wrote.
    assert meta.field_sources["abstract"] == MetaSource.EXTRACTED_DIGEST


def test_a_converted_table_is_not_an_abstract():
    """A keywords table reads as prose by every other measure here.

    Seen on a real paper: the abstract field came out as
    "|**PALABRAS CLAVE:**|**RESUMEN:**| |---|---| |Einstein<br>Energia..."
    """
    table = (
        "|**PALABRAS CLAVE:**|**RESUMEN:**| |---|---| "
        "|Einstein<br>Energia<br>Masa<br>Reacciones quimicas|El trabajo de "
        "Einstein sobre la equivalencia entre masa y energia tuvo consecuencias "
        "profundas para la quimica, y este articulo las repasa en detalle.|"
    )

    assert not is_prose(table)
