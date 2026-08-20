"""Tier escalation: spend GPU time only where CPU extraction actually failed.

Tier 0 costs ~0.05 s/page on CPU and handles most PDFs. Tier 1 (Marker+Surya)
costs ~1-3 s/page on the GPU. Tier 2 (a vision model, page by page) costs
~10 s/page and exists only for documents the other two cannot read at all.

Two properties matter more than the tier logic itself:

* **A paper is never lost.** If every tier fails its quality check, the longest
  output still wins and the paper is flagged ``degraded`` rather than dropped.
  Partial text is worth more than a hole in the map.
* **A missing tier is not fatal.** Marker is a heavy optional dependency; if it
  is not installed the router falls through instead of stranding the document.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.services.parse.quality import QualityReport, TextProbe
from app.services.parse.quality import assess as assess_probe
from app.services.parse.tier0_pymupdf import ParseResult

logger = logging.getLogger(__name__)

Parser = Callable[[Path], ParseResult]
Prober = Callable[[Path], TextProbe]
Assessor = Callable[[TextProbe], QualityReport]
MarkdownAssessor = Callable[[str, int], QualityReport]


class ParserUnavailable(RuntimeError):
    """A tier's dependency is not installed. Fall through rather than fail."""


@dataclass
class TierOutcome:
    """What the router decided, and enough context to explain it in the UI."""

    result: ParseResult
    #: Quality of the output that was ultimately kept.
    report: QualityReport
    #: Why tier 0 was rejected. Empty when tier 0 was good enough. Kept separate
    #: from ``report`` because "why did this cost GPU time" and "is the result
    #: any good" are different questions and the UI asks both.
    escalation_reasons: tuple[str, ...] = ()
    degraded: bool = False
    best_effort: ParseResult | None = None
    notes: list[str] = field(default_factory=list)


def assess_markdown_default(markdown: str, page_count: int) -> QualityReport:
    """Judge a higher tier's output with the same gate used on tier 0."""
    return assess_probe(TextProbe(text=markdown, page_count=page_count, font_count=1))


def parse_with_escalation(
    path: Path | str,
    *,
    tier0: Parser,
    tier1: Parser,
    tier2: Parser,
    probe: Prober,
    assess: Assessor = assess_probe,
    assess_markdown: MarkdownAssessor = assess_markdown_default,
    max_tier: int = 2,
) -> TierOutcome:
    """Run the cheapest parser that produces usable text.

    Probing a PDF's text layer costs ~2 ms/page; converting it to Markdown costs
    ~225 ms/page. That 100x gap shapes the control flow: the probe decides the
    tier, and Markdown conversion only happens for output that will actually be
    kept. Tier 0's conversion is therefore deferred — for the ~15% of documents
    that escalate, it runs only if every richer tier also fails.
    """
    path = Path(path)
    notes: list[str] = []
    attempts: list[tuple[ParseResult, QualityReport]] = []

    # --- probe: cheap, and decides everything ---------------------------
    tier0_probe_ok = True
    try:
        report = assess(probe(path))
    except Exception as exc:  # noqa: BLE001 - a corrupt PDF must not kill the run
        logger.warning("tier 0 probe failed on %s: %s", path.name, exc)
        notes.append(f"tier0_error: {exc}")
        tier0_probe_ok = False
        report = QualityReport(passed=False, score=0.0, reasons=("tier0_failed",))

    if report.passed:
        try:
            return TierOutcome(result=tier0(path), report=report, notes=notes)
        except Exception as exc:  # noqa: BLE001
            logger.warning("tier 0 conversion failed on %s: %s", path.name, exc)
            notes.append(f"tier0_error: {exc}")
            tier0_probe_ok = False

    # --- tiers 1 and 2: progressively more expensive --------------------
    for tier, parser in ((1, tier1), (2, tier2)):
        if tier > max_tier:
            notes.append(f"tier{tier}_skipped: above max_tier={max_tier}")
            continue
        try:
            attempt = parser(path)
        except ParserUnavailable as exc:
            notes.append(f"tier{tier}_unavailable: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001
            logger.warning("tier %d failed on %s: %s", tier, path.name, exc)
            notes.append(f"tier{tier}_error: {exc}")
            continue

        attempt_report = assess_markdown(attempt.markdown, attempt.page_count)
        attempts.append((attempt, attempt_report))
        if attempt_report.passed:
            return TierOutcome(
                result=attempt,
                report=attempt_report,
                escalation_reasons=report.reasons,
                notes=notes,
            )

    # --- nothing passed: now the deferred tier-0 conversion is worth it --
    if tier0_probe_ok:
        try:
            attempts.insert(0, (tier0(path), report))
        except Exception as exc:  # noqa: BLE001
            notes.append(f"tier0_error: {exc}")

    if not attempts:
        raise RuntimeError(f"all parse tiers failed for {path.name}: {notes}")

    # Keep the highest tier reached as the recorded result, so the database
    # reflects how hard the pipeline tried, but surface the longest output as
    # the one most likely to be worth reading.
    last_result, last_report = max(attempts, key=lambda a: a[0].tier)
    longest = max(attempts, key=lambda a: len(a[0].markdown))[0]

    return TierOutcome(
        result=last_result,
        report=last_report,
        escalation_reasons=report.reasons,
        degraded=True,
        best_effort=longest,
        notes=notes,
    )
