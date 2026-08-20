"""Deterministic metadata extraction.

Identifiers are extracted, never guessed. An LLM asked for a DOI will invent a
plausible one, and a wrong DOI is worse than a missing one: it silently merges
two different papers under one work key.
"""

from __future__ import annotations

import pytest

from app.models import MetaSource
from app.services.metadata.extract import (
    extract_arxiv_id,
    extract_doi,
    extract_from_pdf,
    guess_title,
    split_authors,
)

# --- identifiers ----------------------------------------------------------


def test_finds_arxiv_id_in_the_stamp():
    text = "arXiv:2401.01234v2  [cs.LG]  15 Jan 2024"
    assert extract_arxiv_id(text) == "2401.01234v2"


def test_finds_old_style_arxiv_id():
    assert extract_arxiv_id("arXiv:math.GT/0309136") == "math.GT/0309136"


def test_finds_doi_in_a_url():
    text = "Available at https://doi.org/10.1145/3292500.3330701 (accessed 2024)"
    assert extract_doi(text) == "10.1145/3292500.3330701"


def test_doi_does_not_swallow_trailing_prose():
    text = "See 10.1038/s41586-021-03819-2. Further work follows."
    assert extract_doi(text) == "10.1038/s41586-021-03819-2"


def test_returns_none_when_no_identifier_present():
    assert extract_doi("no identifiers here") is None
    assert extract_arxiv_id("no identifiers here") is None


def test_only_the_first_page_region_is_searched_for_identifiers():
    """A DOI in the bibliography belongs to a *cited* paper, not this one."""
    body = "This paper has no identifier of its own.\n" * 40
    references = "[1] Smith et al. https://doi.org/10.9999/other.paper\n"
    assert extract_doi(body + references, head_chars=len(body) // 2) is None


# --- authors --------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Ada Lovelace, Alan Turing", ["Ada Lovelace", "Alan Turing"]),
        ("Ada Lovelace and Alan Turing", ["Ada Lovelace", "Alan Turing"]),
        (
            "Ada Lovelace, Alan Turing, and Grace Hopper",
            ["Ada Lovelace", "Alan Turing", "Grace Hopper"],
        ),
        ("Ada Lovelace; Alan Turing", ["Ada Lovelace", "Alan Turing"]),
        ("Ada Lovelace", ["Ada Lovelace"]),
    ],
)
def test_author_separators(raw, expected):
    assert split_authors(raw) == expected


def test_author_superscript_markers_are_dropped():
    assert split_authors("Ada Lovelace1, Alan Turing2,3") == [
        "Ada Lovelace",
        "Alan Turing",
    ]


def test_empty_author_string():
    assert split_authors("") == []


# --- title heuristic ------------------------------------------------------


def test_title_is_the_first_substantial_line():
    text = "\n\nLearning Scientific Document Embeddings\nAda Lovelace\n\nAbstract\n"
    assert guess_title(text) == "Learning Scientific Document Embeddings"


def test_title_skips_preprint_stamps():
    text = "arXiv:2401.01234v2 [cs.LG] 15 Jan 2024\nA Real Title Here\nAuthors\n"
    assert guess_title(text) == "A Real Title Here"


def test_title_skips_page_furniture():
    text = "Preprint. Under review.\n1\nThe Actual Title\nSomeone\n"
    assert guess_title(text) == "The Actual Title"


def test_title_returns_none_for_empty_text():
    assert guess_title("") is None


# --- end to end on a real PDF --------------------------------------------


def test_extracts_identifiers_from_a_pdf(pdf_fixtures):
    meta = extract_from_pdf(pdf_fixtures["with_identifiers.pdf"])
    assert meta.arxiv_id == "2401.01234v2"
    assert meta.doi == "10.1145/3292500.3330701"
    assert meta.title == "Deep Sets for Molecular Property Prediction"


def test_records_the_source_of_each_field(pdf_fixtures):
    """Provenance is what lets enrichment refine fields without clobbering."""
    meta = extract_from_pdf(pdf_fixtures["with_identifiers.pdf"])
    assert meta.field_sources["doi"] == MetaSource.REGEX
    assert meta.field_sources["title"] in {
        MetaSource.PDF_EMBEDDED,
        MetaSource.HEURISTIC,
    }


def test_pdf_without_identifiers_yields_none_not_a_guess(pdf_fixtures):
    meta = extract_from_pdf(pdf_fixtures["clean_single_column.pdf"])
    assert meta.doi is None
    assert meta.arxiv_id is None


def test_extraction_survives_a_pdf_with_no_text(pdf_fixtures):
    meta = extract_from_pdf(pdf_fixtures["scanned_no_text_layer.pdf"])
    assert meta.doi is None
    assert meta.title is None


# --- escalated papers -----------------------------------------------------


def test_rescued_text_is_preferred_over_a_broken_text_layer(pdf_fixtures):
    """A paper escalates *because* its text layer is unusable.

    Deriving a title from that same layer reproduces the garbage the
    escalation existed to escape.
    """
    rescued = "# Learning Scientific Document Embeddings\n\nWe present a method."
    meta = extract_from_pdf(pdf_fixtures["cid_no_whitespace.pdf"], parsed_text=rescued)
    assert meta.title == "Learning Scientific Document Embeddings"


def test_a_broken_text_layer_alone_yields_run_together_junk(pdf_fixtures):
    """The behaviour the fix above avoids, pinned so it cannot come back."""
    meta = extract_from_pdf(pdf_fixtures["cid_no_whitespace.pdf"])
    assert meta.title is not None
    assert " " not in meta.title, "this fixture has no spaces; that is the point"


def test_markdown_heading_markers_are_stripped_from_titles():
    from app.services.metadata.extract import guess_title

    assert guess_title("## A Real Paper Title\n\nBody text.") == "A Real Paper Title"


def test_identifiers_are_found_in_either_source(pdf_fixtures):
    """A DOI can survive in the raw layer even when the prose does not."""
    meta = extract_from_pdf(
        pdf_fixtures["with_identifiers.pdf"], parsed_text="# Clean Title\n\nBody."
    )
    assert meta.doi == "10.1145/3292500.3330701"
    assert meta.arxiv_id == "2401.01234v2"
    assert meta.title == "Deep Sets for Molecular Property Prediction"


# --- markdown artifacts in titles ----------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("**Ultraviolet Spectra of Galaxies**", "Ultraviolet Spectra of Galaxies"),
        ("## A Real Paper Title", "A Real Paper Title"),
        ("*Italic Title Here*", "Italic Title Here"),
        ("***Bold Italic Title***", "Bold Italic Title"),
        ("__Underscore Emphasis__", "Underscore Emphasis"),
        ("**Unbalanced Emphasis", "Unbalanced Emphasis"),
        ("Plain Title Untouched", "Plain Title Untouched"),
    ],
)
def test_markdown_emphasis_is_stripped_from_titles(raw, expected):
    """These render literally on the map if they survive extraction."""
    from app.services.metadata.extract import strip_markdown

    assert strip_markdown(raw) == expected


def test_emphasis_inside_a_title_is_unwrapped_not_deleted():
    from app.services.metadata.extract import strip_markdown

    assert strip_markdown("The **Fast** Algorithm") == "The Fast Algorithm"


# --- abstracts ------------------------------------------------------------

ABSTRACT_MD = """# A Paper About Things

Ada Lovelace, Alan Turing

# ABSTRACT

We investigate the effects of neutrino heating on the dynamics of a spherical
accretion shock, and show that the resulting instability is suppressed at high
luminosity across the parameter range we consider.

# 1 Introduction

Something else entirely that must not end up in the abstract.
"""


def test_the_abstract_is_pulled_from_the_markdown():
    """The most informative field for placing a paper, and it was being dropped."""
    from app.services.metadata.extract import extract_abstract

    abstract = extract_abstract(ABSTRACT_MD)
    assert abstract is not None
    assert "neutrino heating" in abstract
    assert "Something else entirely" not in abstract, "ran past the next heading"


def test_the_abstract_survives_varied_heading_styles():
    from app.services.metadata.extract import extract_abstract

    body = " ".join(["Substantive abstract text here."] * 8)
    for heading in ("# Abstract", "## ABSTRACT", "### **Abstract**", "# Abstract."):
        markdown = f"{heading}\n\n{body}\n\n# Introduction\n\nOther."
        assert extract_abstract(markdown) is not None, heading


def test_no_abstract_heading_yields_none():
    from app.services.metadata.extract import extract_abstract

    assert extract_abstract("# Introduction\n\nNo abstract in this paper.") is None


def test_a_stub_abstract_is_rejected():
    """A heading followed by nothing is not an abstract."""
    from app.services.metadata.extract import extract_abstract

    assert extract_abstract("# Abstract\n\nTBD.\n\n# Introduction\n\nx") is None


def test_the_abstract_is_length_capped():
    from app.services.metadata.extract import extract_abstract

    markdown = "# Abstract\n\n" + ("word " * 20000)
    assert len(extract_abstract(markdown)) <= 4000


def test_whitespace_is_normalised():
    from app.services.metadata.extract import extract_abstract

    body = "Line one of the abstract.\nLine two continues it.\n\n" * 4
    assert "\n" not in (extract_abstract(f"# Abstract\n\n{body}\n# Next\n") or "")


def test_extraction_records_the_abstract_source(pdf_fixtures):
    from app.models import MetaSource

    meta = extract_from_pdf(
        pdf_fixtures["with_identifiers.pdf"], parsed_text=ABSTRACT_MD
    )
    assert meta.abstract is not None
    assert meta.field_sources["abstract"] == MetaSource.HEURISTIC


# --- abstracts with no heading -------------------------------------------

PREPRINT_MD = """MAD-TH-08-16

# **Towards a warped inflationary brane scanning**

Heng-Yu Chen<sup>*</sup>

_Department of Physics, University of Wisconsin-Madison, Madison, WI 53706_

We present a detailed systematics for comparing warped brane inflation with the
observations, incorporating the effects of both moduli stabilization and
ultraviolet bulk physics. We explicitly construct an example of the inflaton
potential governing the motion of a mobile D3 brane.

# 1 Introduction

The idea of brane inflation has been studied extensively in recent years.
"""


def test_an_unlabelled_abstract_is_recovered():
    """Most physics preprints never write the word "Abstract"."""
    from app.services.metadata.extract import extract_abstract_unlabelled

    abstract = extract_abstract_unlabelled(PREPRINT_MD)
    assert abstract is not None
    assert abstract.startswith("We present a detailed systematics")
    assert "brane inflation has been studied" not in abstract, "ran into the body"


def test_the_title_is_not_mistaken_for_an_abstract():
    from app.services.metadata.extract import extract_abstract_unlabelled

    assert "warped inflationary brane scanning" not in (
        extract_abstract_unlabelled(PREPRINT_MD) or ""
    )


def test_affiliations_are_not_mistaken_for_an_abstract():
    """They sit exactly where an abstract would and can be long."""
    from app.services.metadata.extract import extract_abstract_unlabelled

    markdown = (
        "# A Title\n\n"
        "Department of Physics, University of Wisconsin-Madison, Madison, "
        "WI 53706-1390, USA, and Instituut-Lorentz for Theoretical Physics, "
        "Universiteit Leiden, 2333 CA Leiden, The Netherlands. Correspondence "
        "to author@example.edu for further information.\n\n"
        "# 1 Introduction\n\nBody."
    )
    assert extract_abstract_unlabelled(markdown) is None


def test_an_author_list_is_not_mistaken_for_an_abstract():
    from app.services.metadata.extract import extract_abstract_unlabelled

    markdown = (
        "# A Title\n\n"
        "Ada Lovelace, Alan Turing, Grace Hopper, John Von Neumann, Claude "
        "Shannon, Donald Knuth, Barbara Liskov, Edsger Dijkstra, Alonzo "
        "Church, Emmy Noether, Kurt Godel, David Hilbert.\n\n"
        "# Introduction\n\nBody."
    )
    assert extract_abstract_unlabelled(markdown) is None


def test_a_labelled_abstract_is_preferred_over_the_fallback(pdf_fixtures):
    """The heading is the reliable signal; the fallback is for when it is absent."""
    markdown = (
        "# Title\n\nSome opening prose that runs on for a while and could "
        "plausibly be mistaken for an abstract by a structural heuristic.\n\n"
        "# Abstract\n\n" + ("The genuine abstract text. " * 8) + "\n\n"
        "# 1 Introduction\n\nBody."
    )
    meta = extract_from_pdf(pdf_fixtures["with_identifiers.pdf"], parsed_text=markdown)
    assert meta.abstract.startswith("The genuine abstract text")


def test_a_paper_with_no_abstract_at_all_yields_none():
    from app.services.metadata.extract import extract_abstract_unlabelled

    assert (
        extract_abstract_unlabelled("# Title\n\n# 1 Introduction\n\nStraight in.")
        is None
    )


def test_the_fallback_stops_at_the_body():
    """Without a stop it would return the introduction instead."""
    from app.services.metadata.extract import extract_abstract_unlabelled

    markdown = "# Title\n\n# Introduction\n\n" + (
        "This is body prose that should never be returned. " * 8
    )
    assert extract_abstract_unlabelled(markdown) is None


def test_template_placeholders_are_not_treated_as_authors():
    """Journal PDF templates leave these in the Author metadata field."""
    assert split_authors("Lastname F, Lastname F") == []
    assert split_authors("Author 1, Author 2") == []
    assert split_authors("Firstname Lastname") == []


def test_real_authors_alongside_a_placeholder_survive():
    assert split_authors("Ada Lovelace, Lastname F") == ["Ada Lovelace"]


def test_ordinary_surnames_are_not_mistaken_for_placeholders():
    """The guard must not eat real people."""
    for name in ("Nameer Al-Khwarizmi", "Anna Nameth", "Sarah Unknownson"):
        assert split_authors(name) == [name], name


@pytest.mark.parametrize(
    "furniture",
    [
        "www.nature.com/scientificreports",
        "https://www.sciencedirect.com/journal",
        "IEEE Transactions on Something",
        "Volume 14, Issue 3",
        "ISSN 1234-5678",
    ],
)
def test_journal_furniture_is_not_a_title(furniture):
    """Journals print their address across every page, often above the title."""
    real = "A Study of Spontaneous Yawning in Sea Lions"
    assert guess_title(f"{furniture}\n{real}\n\nAuthors") == real


def test_a_title_merely_mentioning_a_publisher_is_kept():
    """The guard anchors at the start; it must not eat real titles."""
    title = "Benchmarking Nature-Inspired Optimisation Algorithms"
    assert guess_title(f"{title}\n\nAuthors") == title


# --- page furniture that the list view made visible ----------------------

REAL_TITLE = "A Study of Spontaneous Yawning in Sea Lions"


@pytest.mark.parametrize(
    "furniture",
    [
        "> REPLACE THIS LINE WITH YOUR PAPER IDENTIFICATION NUMBER <",
        "Insert title here",
        "Consciousness and Cognition 14 (2005) 169-187",
        "Journal of Theoretical Biology 241 (2006) 438-441",
        "Downloaded from rstb.royalsocietypublishing.org on April 5, 2010",
        "NIH Public Access Author Manuscript",
        "This content downloaded from 192.168.0.1",
        "All rights reserved",
    ],
)
def test_page_furniture_is_never_taken_as_a_title(furniture):
    """Every one of these was a paper's title in the real library."""
    assert guess_title(f"{furniture}\n{REAL_TITLE}\n\nAuthors") == REAL_TITLE


@pytest.mark.parametrize(
    "title",
    [
        "A Study of University Admissions Policy Across Europe",
        "Institutional Trust and Democratic Backsliding in Eastern Europe",
        "Department Store Economics and Consumer Behaviour in the 1920s",
        "Bodily maps of emotions",
    ],
)
def test_real_titles_are_not_mistaken_for_furniture(title):
    """The guards are aggressive; this is what stops them eating real papers."""
    assert guess_title(f"{title}\n\nAuthors") == title


def test_a_leading_bullet_or_page_number_is_stripped():
    assert guess_title("- 1 The post-reproductive ovary shifts function") == (
        "The post-reproductive ovary shifts function"
    )


def test_affiliation_stems_match_their_variants():
    """A trailing word boundary defeated stem matching: "universit" could not
    match "Universite", so foreign affiliations passed straight through."""
    from app.services.metadata.extract import AFFILIATION_RE

    for variant in ("University", "Universite", "Universiteit", "Institution"):
        assert AFFILIATION_RE.search(variant), variant


def test_a_junk_embedded_title_falls_back_to_the_heuristic(pdf_fixtures):
    """The PDF's own Title field is authoritative only when it is honest.

    Real files carry "Dissertation Thesis", a journal running header, or a
    template's leftovers in that field, and trusting it blindly puts those on
    the map as paper names.
    """
    import pymupdf

    path = pdf_fixtures["with_identifiers.pdf"]
    doc = pymupdf.open(path)
    doc.set_metadata(
        {**(doc.metadata or {}), "title": "NIH Public Access Author Manuscript"}
    )
    junk = path.parent / "junk-title.pdf"
    doc.save(junk)
    doc.close()

    meta = extract_from_pdf(junk, parsed_text="# The Real Title Of This Paper\n\nBody.")
    assert meta.title == "The Real Title Of This Paper"


def test_an_honest_embedded_title_is_still_preferred(pdf_fixtures):
    meta = extract_from_pdf(
        pdf_fixtures["with_identifiers.pdf"],
        parsed_text="# A Worse Heuristic Guess\n\nBody.",
    )
    assert meta.title == "Deep Sets for Molecular Property Prediction"
