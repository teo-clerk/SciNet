"""Decides whether tier-0 output is good enough, or the PDF needs the GPU.

This gate is the whole economics of the pipeline. Tier 0 costs ~0.05 s/page on
CPU; tier 1 costs ~1-3 s/page on the GPU and tier 2 ~10 s/page. Escalating
everything turns a 90-minute backfill into a multi-day one; escalating nothing
puts unreadable text into the embeddings, where it silently poisons the map.

Two classes of check, because they behave differently:

* **Hard** — unambiguous evidence the text is unusable (no text at all, every
  space missing, replacement characters). One is enough to escalate.
* **Soft** — suspicious but individually explainable by a legitimate document
  (unusual alphabet, few function words). Two must agree before escalating,
  which is what keeps non-English and formula-dense papers on tier 0.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from app.services.parse.stopwords import STOPWORDS

# --- thresholds -----------------------------------------------------------
MIN_CHARS_PER_PAGE = 200
MIN_LINE_LEN_FOR_WHITESPACE_CHECK = 20
MAX_MISSING_WHITESPACE_RATIO = 0.30
MAX_REPLACEMENT_RATIO = 0.01
MAX_MOJIBAKE_RATIO = 0.004
MIN_LINES_FOR_WHITESPACE_CHECK = 5
MIN_ALPHA_RATIO = 0.35
MAX_IMAGE_AREA_RATIO = 0.80
#: Text density above which a page-sized image is irrelevant. A scanned book
#: that was OCR'd carries a full-page scan *and* a good text layer, and on this
#: library that combination is common: 7 of 60 sampled PDFs — 12%, all of them
#: books — were failing on the image ratio alone while yielding 300 to 2,900
#: characters a page. Sending those to the vision model costs eight to fifteen
#: seconds a page to reproduce text that was already there and correct.
#:
#: Deliberately far above MIN_CHARS_PER_PAGE: the point is not "some text
#: survived" but "this text layer is doing its job", which is the only thing
#: that makes the image behind it uninteresting.
AMPLE_CHARS_PER_PAGE = 300

# Soft signals — need two to agree.
MIN_STOPWORD_HIT_RATE = 0.06
MAX_NONASCII_RATIO = 0.06
WORD_LENGTH_RANGE = (2.5, 12.0)

WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# Whitespace is Unicode category Cc but is obviously not corruption; only other
# control characters indicate a broken extraction.
BENIGN_CONTROL = frozenset("\n\r\t\f\v")

# Signature of UTF-8 bytes decoded as Latin-1 — the most common encoding
# failure. A 2-byte sequence surfaces as "Ã"/"Â" followed by another high
# character, and a 3-byte one as the literal "â€". Genuine French, Spanish or
# Portuguese prose carries "é"/"à"/"ã" as single characters and never produces
# these pairs, so the check is specific enough to be treated as conclusive.
MOJIBAKE_RE = re.compile(r"[\u00c3\u00c2\u00c5][\u0080-\u00ff]|\u00e2\u20ac")


@dataclass(frozen=True)
class TextProbe:
    """What the gate needs to judge a document, independent of how it was read."""

    text: str
    #: What the document really is.
    page_count: int
    font_count: int = 0
    image_area_ratio: float = 0.0
    #: Pages the text was taken from. Below page_count when a long document was
    #: sampled rather than read whole. Every per-page metric must divide by
    #: this and not by page_count, or a 731-page book probed to eighty pages
    #: reports a ninth of its real text density and fails as "insufficient
    #: text" — the sample would be judged as though the rest were blank.
    pages_sampled: int | None = None

    @property
    def measured_pages(self) -> int:
        return max(self.pages_sampled or self.page_count, 1)


@dataclass(frozen=True)
class QualityReport:
    passed: bool
    score: float
    reasons: tuple[str, ...] = ()
    metrics: dict[str, float] = field(default_factory=dict)


def _metrics(probe: TextProbe) -> dict[str, float]:
    text = probe.text
    pages = probe.measured_pages
    n = len(text)

    if n == 0:
        return {
            "chars_per_page": 0.0,
            "missing_whitespace_ratio": 0.0,
            "replacement_ratio": 0.0,
            "alpha_ratio": 0.0,
            "nonascii_ratio": 0.0,
            "stopword_hit_rate": 0.0,
            "mean_word_length": 0.0,
            "mojibake_ratio": 0.0,
            "image_area_ratio": probe.image_area_ratio,
        }

    # Only long lines count: headers, page numbers and equation fragments are
    # legitimately space-free and would otherwise dominate the ratio.
    long_lines = [
        ln
        for ln in text.splitlines()
        if len(ln.strip()) >= MIN_LINE_LEN_FOR_WHITESPACE_CHECK
    ]
    # Below a handful of long lines the ratio is too noisy to act on: a title
    # page with two lines would read as 50% broken.
    missing_ws = (
        sum(1 for ln in long_lines if " " not in ln.strip()) / len(long_lines)
        if len(long_lines) >= MIN_LINES_FOR_WHITESPACE_CHECK
        else 0.0
    )

    replacement = sum(
        1
        for c in text
        if c == "\ufffd"
        or (unicodedata.category(c) == "Cc" and c not in BENIGN_CONTROL)
    )
    mojibake_hits = len(MOJIBAKE_RE.findall(text))
    alpha = sum(1 for c in text if c.isalpha())
    nonascii = sum(1 for c in text if ord(c) > 127)

    words = [w.casefold() for w in WORD_RE.findall(text)]
    hits = sum(1 for w in words if w in STOPWORDS)

    return {
        "chars_per_page": n / pages,
        "missing_whitespace_ratio": missing_ws,
        "replacement_ratio": replacement / n,
        "alpha_ratio": alpha / n,
        "nonascii_ratio": nonascii / n,
        "stopword_hit_rate": hits / len(words) if words else 0.0,
        "mean_word_length": (sum(len(w) for w in words) / len(words) if words else 0.0),
        "mojibake_ratio": mojibake_hits * 2 / n,
        "image_area_ratio": probe.image_area_ratio,
    }


def _hard_failures(probe: TextProbe, m: dict[str, float]) -> list[str]:
    reasons: list[str] = []

    if m["chars_per_page"] < MIN_CHARS_PER_PAGE:
        reasons.append("insufficient_text")
    if m["missing_whitespace_ratio"] > MAX_MISSING_WHITESPACE_RATIO:
        reasons.append("missing_whitespace")
    if m["replacement_ratio"] > MAX_REPLACEMENT_RATIO:
        reasons.append("replacement_characters")
    if m["mojibake_ratio"] > MAX_MOJIBAKE_RATIO:
        reasons.append("double_encoded_text")
    if m["alpha_ratio"] < MIN_ALPHA_RATIO:
        reasons.append("not_word_like")
    if (
        probe.image_area_ratio > MAX_IMAGE_AREA_RATIO
        and m["chars_per_page"] < AMPLE_CHARS_PER_PAGE
    ):
        # The rule is "a page that is one big scan has no usable text layer,
        # even when a stray caption extracts cleanly". A document yielding
        # hundreds of characters a page is not a stray caption; it is a scan
        # somebody already ran through OCR, and the image behind the text says
        # nothing about whether the text is good.
        reasons.append("page_is_image")
    # No embedded fonts *and* no meaningful text means a pure raster scan.
    if probe.font_count == 0 and m["chars_per_page"] < MIN_CHARS_PER_PAGE:
        reasons.append("no_text_layer")

    return reasons


def _soft_signals(m: dict[str, float]) -> list[str]:
    signals: list[str] = []

    if m["stopword_hit_rate"] < MIN_STOPWORD_HIT_RATE:
        signals.append("few_function_words")
    if m["nonascii_ratio"] > MAX_NONASCII_RATIO:
        signals.append("unusual_alphabet")
    lo, hi = WORD_LENGTH_RANGE
    if not (lo <= m["mean_word_length"] <= hi):
        signals.append("odd_word_lengths")

    return signals


def assess(probe: TextProbe) -> QualityReport:
    """Judge tier-0 output. ``passed`` False means escalate to the next tier."""
    m = _metrics(probe)

    hard = _hard_failures(probe, m)
    soft = _soft_signals(m)

    # One hard failure is conclusive; soft signals need corroboration, which is
    # what keeps Spanish prose and formula-dense pages on tier 0.
    escalate = bool(hard) or len(soft) >= 2
    reasons = tuple(dict.fromkeys(hard + (soft if len(soft) >= 2 else [])))

    # A readable confidence number for the UI, not a decision input.
    score = max(0.0, min(1.0, 1.0 - 0.34 * len(hard) - 0.15 * len(soft)))

    return QualityReport(
        passed=not escalate,
        score=score,
        reasons=reasons,
        metrics={k: round(v, 4) for k, v in m.items()},
    )
