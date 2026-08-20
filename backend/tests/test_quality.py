"""The tier-0 quality gate.

Everything hinges on this: it decides whether a paper is done in 50 ms of CPU
or costs seconds of GPU. Both directions are expensive to get wrong — a false
pass puts garbage into the embeddings, a false escalation multiplies the
backfill time.
"""

from __future__ import annotations

import pytest

from app.services.parse.quality import TextProbe, assess

PROSE = (
    "We present a method for learning representations of scientific documents "
    "from their citation graph. The model is trained on a large corpus of open "
    "access papers and evaluated on a suite of downstream tasks. "
)
SPANISH = (
    "Presentamos un metodo para aprender representaciones de documentos "
    "cientificos a partir de su grafo de citas. El modelo se entrena con un "
    "corpus amplio de articulos de acceso abierto y se evalua en varias tareas. "
)


def probe(text: str, pages: int = 3, fonts: int = 3, images: float = 0.0):
    return TextProbe(
        text=text, page_count=pages, font_count=fonts, image_area_ratio=images
    )


# --- must pass ------------------------------------------------------------


def test_clean_english_prose_passes():
    report = assess(probe(PROSE * 12))
    assert report.passed, report.reasons


def test_non_english_prose_passes():
    """A Spanish paper is not a broken paper — this is the false-escalation trap."""
    report = assess(probe(SPANISH * 12))
    assert report.passed, report.reasons


def test_prose_with_equations_and_citations_passes():
    noisy = PROSE + "L(x) = sum_i w_i * f(x_i) + b   [12, 13]  (Eq. 3)  " * 8
    assert assess(probe(noisy * 4)).passed


# --- must escalate --------------------------------------------------------


def test_no_text_at_all_escalates():
    report = assess(probe("", pages=10, fonts=0))
    assert not report.passed
    assert "insufficient_text" in report.reasons


def test_near_empty_escalates():
    report = assess(probe("Title Page", pages=1, fonts=1))
    assert not report.passed
    assert "insufficient_text" in report.reasons


def test_missing_whitespace_escalates():
    """CID/Type3 space loss: normal length, unusable output."""
    run_on = (PROSE.replace(" ", "") + "\n") * 20
    report = assess(probe(run_on))
    assert not report.passed
    assert "missing_whitespace" in report.reasons


def test_unmapped_glyphs_escalate():
    report = assess(probe("??? ?? ???? " * 200))
    assert not report.passed


def test_replacement_characters_escalate():
    report = assess(probe("��� word " * 200))
    assert not report.passed
    assert "replacement_characters" in report.reasons


def test_latin1_mojibake_escalates():
    """The subtle one: healthy length, real letters, spaces intact.

    ASCII function words survive double-encoding, so the stopword rate stays
    high and only the byte-pair signature gives it away.
    """
    # Accents written as escapes so the source encoding cannot silently make
    # this a no-op round-trip.
    accented = (
        "L'\u00e9tude pr\u00e9sente une m\u00e9thode pour repr\u00e9senter "
        "les documents scientifiques \u00e0 partir de leur graphe de citations. "
    )
    garbled = accented.encode("utf-8").decode("latin-1")
    assert garbled != accented, "fixture must actually be double-encoded"

    report = assess(probe(garbled * 12))
    assert not report.passed, report.metrics
    assert "double_encoded_text" in report.reasons


def test_correctly_encoded_accents_do_not_escalate():
    """The false-positive guard for the check above."""
    accented = (
        "L'\u00e9tude pr\u00e9sente une m\u00e9thode pour repr\u00e9senter "
        "les documents scientifiques \u00e0 partir de leur graphe de citations. "
    )
    report = assess(probe(accented * 12))
    assert report.passed, report.reasons


def test_page_that_is_almost_entirely_image_escalates():
    report = assess(probe("Figure 1: results", pages=1, fonts=1, images=0.95))
    assert not report.passed


# --- report shape ---------------------------------------------------------


def test_report_carries_metrics_for_diagnosis():
    report = assess(probe(PROSE * 12))
    for key in (
        "chars_per_page",
        "stopword_hit_rate",
        "nonascii_ratio",
        "alpha_ratio",
        "missing_whitespace_ratio",
    ):
        assert key in report.metrics


def test_score_is_bounded():
    for text in ("", PROSE * 12, "???" * 400):
        assert 0.0 <= assess(probe(text)).score <= 1.0


def test_passing_documents_have_no_reasons():
    assert assess(probe(PROSE * 12)).reasons == ()


def test_a_single_hard_failure_is_enough():
    """Hard checks do not need corroboration; soft ones do."""
    report = assess(probe(PROSE * 12 + "\n" + "x" * 30, pages=3))
    assert report.passed, "one odd line must not sink an otherwise fine document"


# --- real fixtures --------------------------------------------------------


@pytest.mark.parametrize(
    ("fixture", "should_pass"),
    [
        ("clean_single_column.pdf", True),
        ("two_column.pdf", True),
        ("non_english.pdf", True),
        ("with_identifiers.pdf", True),
        ("near_empty.pdf", False),
        ("scanned_no_text_layer.pdf", False),
        ("cid_no_whitespace.pdf", False),
        ("mojibake.pdf", False),
        ("unmapped_glyphs.pdf", False),
    ],
)
def test_routes_real_pdfs_correctly(pdf_fixtures, fixture, should_pass):
    from app.services.parse.tier0_pymupdf import probe_pdf

    report = assess(probe_pdf(pdf_fixtures[fixture]))
    assert report.passed is should_pass, (
        f"{fixture}: reasons={report.reasons} metrics={report.metrics}"
    )
