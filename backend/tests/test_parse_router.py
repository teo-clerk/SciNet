"""Tier escalation.

The router's job is to spend GPU time only where CPU extraction genuinely
failed, and to never leave a paper unparsed just because a tier is unavailable.
"""

from __future__ import annotations

import pytest

from app.services.parse.quality import QualityReport, TextProbe
from app.services.parse.router import (
    ParserUnavailable,
    TierOutcome,
    parse_with_escalation,
)
from app.services.parse.tier0_pymupdf import ParseResult


def result(tier: int, text: str = "# Paper\n\nBody text.") -> ParseResult:
    return ParseResult(
        markdown=text,
        tier=tier,
        parser=f"t{tier}",
        parser_version="1",
        page_count=3,
    )


def good(_probe=None) -> QualityReport:
    return QualityReport(passed=True, score=1.0)


def bad(_probe=None) -> QualityReport:
    return QualityReport(passed=False, score=0.1, reasons=("insufficient_text",))


def probe_of(_path) -> TextProbe:
    return TextProbe(text="x", page_count=1)


def test_stays_on_tier_0_when_quality_passes(tmp_path):
    calls = []

    outcome = parse_with_escalation(
        tmp_path / "p.pdf",
        tier0=lambda p: (calls.append(0), result(0))[1],
        tier1=lambda p: (calls.append(1), result(1))[1],
        tier2=lambda p: (calls.append(2), result(2))[1],
        probe=probe_of,
        assess=good,
    )
    assert outcome.result.tier == 0
    assert calls == [0], "no GPU tier should have been touched"


def test_escalates_to_tier_1_when_tier_0_is_unusable(tmp_path):
    calls = []
    outcome = parse_with_escalation(
        tmp_path / "p.pdf",
        tier0=lambda p: (calls.append(0), result(0))[1],
        tier1=lambda p: (calls.append(1), result(1))[1],
        tier2=lambda p: (calls.append(2), result(2))[1],
        probe=probe_of,
        assess=bad,
        assess_markdown=lambda md, pages: good() if "t1" not in md else good(),
    )
    assert outcome.result.tier == 1
    # Tier 0's Markdown conversion (~225 ms/page) is skipped entirely: the
    # probe already showed its text layer is unusable.
    assert calls == [1], f"expected only tier 1 to run, got {calls}"


def test_escalates_to_tier_2_when_tier_1_also_fails(tmp_path):
    """Tier 1 produces unusable output; tier 2 rescues it."""
    calls = []
    outcome = parse_with_escalation(
        tmp_path / "p.pdf",
        tier0=lambda p: (calls.append(0), result(0, "t0 text"))[1],
        tier1=lambda p: (calls.append(1), result(1, "t1 text"))[1],
        tier2=lambda p: (calls.append(2), result(2, "t2 text"))[1],
        probe=probe_of,
        assess=bad,
        assess_markdown=lambda md, pages: good() if "t2" in md else bad(),
    )
    assert outcome.result.tier == 2
    assert not outcome.degraded
    assert calls == [1, 2], f"tier 0 conversion should be skipped, got {calls}"


def test_keeps_the_best_attempt_when_every_tier_fails(tmp_path):
    """Never discard output entirely — partial text still beats nothing."""
    outcome = parse_with_escalation(
        tmp_path / "p.pdf",
        tier0=lambda p: result(0, "short"),
        tier1=lambda p: result(1, "a much longer body of extracted text here"),
        tier2=lambda p: result(2, "tiny"),
        probe=probe_of,
        assess=bad,
        assess_markdown=lambda md, pages: bad(),
    )
    assert outcome.result.tier == 2
    assert outcome.degraded is True
    assert outcome.best_effort.tier == 1, "longest output should be flagged as best"


def test_missing_tier_1_falls_through_to_tier_2(tmp_path):
    """Marker not installed must not strand the paper."""

    def unavailable(_p):
        raise ParserUnavailable("marker not installed")

    outcome = parse_with_escalation(
        tmp_path / "p.pdf",
        tier0=lambda p: result(0),
        tier1=unavailable,
        tier2=lambda p: result(2),
        probe=probe_of,
        assess=bad,
        assess_markdown=lambda md, pages: good(),
    )
    assert outcome.result.tier == 2
    assert any(n.startswith("tier1_unavailable") for n in outcome.notes)


def test_all_tiers_unavailable_still_returns_tier_0_output(tmp_path):
    def unavailable(_p):
        raise ParserUnavailable("not installed")

    outcome = parse_with_escalation(
        tmp_path / "p.pdf",
        tier0=lambda p: result(0),
        tier1=unavailable,
        tier2=unavailable,
        probe=probe_of,
        assess=bad,
        assess_markdown=lambda md, pages: bad(),
    )
    assert outcome.result.tier == 0
    assert outcome.degraded is True


def test_max_tier_caps_escalation(tmp_path):
    """Backfill can forbid the expensive tier without changing the code path."""
    calls = []
    outcome = parse_with_escalation(
        tmp_path / "p.pdf",
        tier0=lambda p: (calls.append(0), result(0))[1],
        tier1=lambda p: (calls.append(1), result(1))[1],
        tier2=lambda p: (calls.append(2), result(2))[1],
        probe=probe_of,
        assess=bad,
        assess_markdown=lambda md, pages: bad(),
        max_tier=1,
    )
    assert 2 not in calls
    assert outcome.result.tier == 1


def test_unreadable_pdf_does_not_abort_the_paper(tmp_path):
    """A corrupt file fails at probe time; the GPU tiers still get a turn."""

    def broken_probe(_p):
        raise ValueError("corrupt xref table")

    outcome = parse_with_escalation(
        tmp_path / "p.pdf",
        tier0=lambda p: result(0),
        tier1=lambda p: result(1),
        tier2=lambda p: result(2),
        probe=broken_probe,
        assess=bad,
        assess_markdown=lambda md, pages: good(),
    )
    assert outcome.result.tier == 1
    assert any("tier0_error" in n for n in outcome.notes)


def test_tier_0_conversion_crash_falls_through(tmp_path):
    """The probe said the text layer was fine, but conversion blew up."""

    def broken_convert(_p):
        raise ValueError("unsupported filter")

    outcome = parse_with_escalation(
        tmp_path / "p.pdf",
        tier0=broken_convert,
        tier1=lambda p: result(1),
        tier2=lambda p: result(2),
        probe=probe_of,
        assess=good,
        assess_markdown=lambda md, pages: good(),
    )
    assert outcome.result.tier == 1
    assert any("tier0_error" in n for n in outcome.notes)


def test_tier_0_output_is_still_the_fallback_when_all_tiers_fail(tmp_path):
    """Deferred does not mean discarded."""
    calls = []
    outcome = parse_with_escalation(
        tmp_path / "p.pdf",
        tier0=lambda p: (calls.append(0), result(0, "the only text we have"))[1],
        tier1=lambda p: (calls.append(1), result(1, "x"))[1],
        tier2=lambda p: (calls.append(2), result(2, "y"))[1],
        probe=probe_of,
        assess=bad,
        assess_markdown=lambda md, pages: bad(),
    )
    assert 0 in calls, "tier 0 must be converted as a last resort"
    assert outcome.degraded is True
    assert outcome.best_effort.markdown == "the only text we have"


def test_every_tier_crashing_raises(tmp_path):
    def broken(_p):
        raise ValueError("nope")

    with pytest.raises(RuntimeError, match="all parse tiers failed"):
        parse_with_escalation(
            tmp_path / "p.pdf",
            tier0=broken,
            tier1=broken,
            tier2=broken,
            probe=probe_of,
            assess=bad,
            assess_markdown=lambda md, pages: bad(),
        )


def test_outcome_reports_why_it_escalated(tmp_path):
    outcome: TierOutcome = parse_with_escalation(
        tmp_path / "p.pdf",
        tier0=lambda p: result(0),
        tier1=lambda p: result(1),
        tier2=lambda p: result(2),
        probe=probe_of,
        assess=bad,
        assess_markdown=lambda md, pages: good(),
    )
    assert "insufficient_text" in outcome.escalation_reasons
    assert outcome.report.passed, "the kept output is fine; only tier 0 was not"


# --- tier 1 wall-clock budget --------------------------------------------


def test_a_tier1_timeout_falls_through_to_tier2(tmp_path):
    """A per-call limit does not bound a document; this is what does."""
    from app.services.parse.tier1_marker import Tier1Timeout

    calls = []

    def slow(_p):
        calls.append(1)
        raise Tier1Timeout("exceeded its 270s budget")

    outcome = parse_with_escalation(
        tmp_path / "p.pdf",
        tier0=lambda p: result(0),
        tier1=slow,
        tier2=lambda p: result(2, "rescued by tier 2"),
        probe=probe_of,
        assess=bad,
        assess_markdown=lambda md, pages: good(),
    )
    assert outcome.result.tier == 2, "a stalled tier 1 must not lose the paper"
    assert any("tier1_error" in n for n in outcome.notes)
    assert calls == [1]
