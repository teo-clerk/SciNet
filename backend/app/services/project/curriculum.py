"""Where to start reading a region of the map.

A cluster tells a reader that forty papers belong together. It does not tell
them which one to open first, and the honest answer — "the one everything else
here assumes you have read" — is not written down anywhere. This module makes
the best guess the corpus itself supports.

Four signals go into the score, and they are weighted by how much evidence
each one actually carries:

*Centrality* (0.45) is the only signal every set has. The paper closest to the
region's mean vector is the one that shares the most with everything else
there, which is what "representative" means in embedding space. It is
rank-normalised within the set rather than used raw: a tight region has
cosines of 0.85–0.95 and a loose one 0.4–0.7, and the weight should mean the
same thing in both.

*Introduction cues* (0.30) are the strongest evidence of "readable first" — a
paper calling itself a survey or a primer has told you what it is for — but
they are sparse. Most regions have none, and the weight has to be large enough
that one cue can overturn a small centrality gap without letting a survey of
the wrong subject win outright.

*Year* (0.15) is a weak prior. Foundational works are older, but so are
superseded ones, and the score cannot tell them apart. An unknown year is
neutral, not penalised — the same rule the time scrubber applies to undated
papers.

*Length* (0.10) is a tiebreaker. A 400-page book is a poorer first step than a
20-page primer on the same subject, however central it is.

The reading order that follows the entry point is a walk, not a ranking: each
next stop is the unvisited paper most similar to the last one, pulled slightly
toward whatever scored well. A ranked list would jump between sub-topics on
every step; a pure nearest-neighbour walk would never surface a readable paper
that happened to sit off to one side.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

#: Stops in a reading order. Past a dozen the list stops being a plan and
#: becomes the cluster again.
MAX_READING_ORDER = 12

WEIGHT_CENTRALITY = 0.45
WEIGHT_INTRO = 0.30
WEIGHT_YEAR = 0.15
WEIGHT_LENGTH = 0.10

#: The walk balances coherence against readability. Similarity dominates so the
#: order stays inside one sub-topic until it is exhausted; the score term pulls
#: a well-scored paper forward among otherwise equal neighbours.
WALK_SIMILARITY_WEIGHT = 0.6
WALK_SCORE_WEIGHT = 0.4

#: A document at or under this many pages is unambiguously "short"; at or over
#: the upper bound it is a book, and the length signal bottoms out.
SHORT_PAGES = 30
LONG_PAGES = 300

#: Abstract cues are only sought this far in. A paper describing itself does so
#: in its first sentences; a cue deep in the abstract is about something else.
ABSTRACT_WINDOW = 600

#: The component threshold above which a reason is worth stating. Below it the
#: signal contributed less than half its weight and saying so would be noise.
REASON_THRESHOLD = 0.5

TITLE_CUES = re.compile(
    r"\b(introduction|introductory|primer|tutorial|survey|review|overview"
    r"|handbook|foundations?|principles|elements of|outline of|guide to"
    r"|lectures? on|what is|an essay on|a short history|for beginners)\b",
    re.IGNORECASE,
)
ABSTRACT_CUES = re.compile(
    r"\b(we (review|survey)|this (survey|review|tutorial|primer)|overview of"
    r"|introduces? the reader|for (newcomers|non-specialists|a general audience)"
    r"|accessible introduction)\b",
    re.IGNORECASE,
)


#: Fewer letters than this is a number or a symbol, not a title.
MIN_TITLE_LETTERS = 4


@dataclass(frozen=True)
class Candidate:
    paper_id: int
    title: str | None
    abstract: str | None
    year: int | None
    pages: int | None


def presentable(candidate: Candidate) -> bool:
    """Can this work be recommended by name?

    An entry point is a suggestion the reader follows by its title. A work
    whose title the pipeline never recovered, or recovered as a template's own
    placeholder ("Paper Title (use style: paper title)" was the recommended
    start of the largest region on the benchmark), is still a point in the set
    and still a stop on a trail — it is simply not something to recommend.
    """
    from app.services.metadata.extract import TEMPLATE_RE

    title = (candidate.title or "").strip()
    if not title or TEMPLATE_RE.search(title) is not None:
        return False
    # A Markdown table row the converter left at the top of a document
    # ("| 0 |") is a title the pipeline recovered wrongly, not one to hand a
    # reader; so is anything with fewer letters than a word.
    if "|" in title:
        return False
    return len(re.findall(r"[^\W\d_]", title)) >= MIN_TITLE_LETTERS


@dataclass(frozen=True)
class Scored:
    paper_id: int
    score: float
    #: Raw cosine to the set's centre, kept so a caller can see how tight the
    #: region is; the score itself uses the rank.
    centrality: float
    reasons: tuple[str, ...]


def _unit(matrix: np.ndarray) -> np.ndarray:
    return matrix / np.clip(np.linalg.norm(matrix, axis=-1, keepdims=True), 1e-12, None)


def intro_cue(title: str | None, abstract: str | None) -> tuple[float, str | None]:
    """How strongly a paper announces itself as an introduction.

    A title cue is worth twice an abstract cue: "A Survey of X" is the paper's
    own statement of purpose, while "we review" in an abstract is often one
    sentence of a research paper positioning its contribution.
    """
    if title:
        match = TITLE_CUES.search(title)
        if match:
            return 1.0, match.group(0).lower()
    if abstract:
        match = ABSTRACT_CUES.search(abstract[:ABSTRACT_WINDOW])
        if match:
            return 0.5, match.group(0).lower()
    return 0.0, None


def _rank_normalised(values: np.ndarray) -> np.ndarray:
    """1 for the largest value, 0 for the smallest, ties sharing a rank.

    Competition ranking — a paper's rank is the number of papers strictly
    ahead of it — so two identical vectors score identically instead of one
    being penalised for the order it arrived in.
    """
    n = values.shape[0]
    if n < 2:
        return np.ones(n)
    descending = np.sort(-values)
    ranks = np.searchsorted(descending, -values, side="left")
    return 1.0 - ranks / (n - 1)


def _year_scores(candidates: Sequence[Candidate]) -> list[float]:
    known = sorted({c.year for c in candidates if c.year is not None})
    if len(known) < 2:
        return [0.5] * len(candidates)
    y_min, y_max = known[0], known[-1]
    return [
        1.0 - (c.year - y_min) / (y_max - y_min) if c.year is not None else 0.5
        for c in candidates
    ]


def _length_score(pages: int | None) -> float:
    if pages is None:
        return 0.5
    if pages <= SHORT_PAGES:
        return 1.0
    if pages >= LONG_PAGES:
        return 0.0
    return 1.0 - (pages - SHORT_PAGES) / (LONG_PAGES - SHORT_PAGES)


def _reasons(
    candidate: Candidate,
    *,
    centrality: float,
    intro: float,
    cue: str | None,
    year: float,
    year_spread: bool,
    length: float,
) -> tuple[str, ...]:
    """Plain-English grounds for the score, one per signal that earned its keep.

    Each phrase is written to be true at the threshold that emits it: a year
    score of exactly 0.5 is the middle of the span, which is not "the
    earliest", so the wording steps down as the score does.
    """
    reasons: list[str] = []
    if centrality >= 1.0:
        reasons.append("closest to the centre of this region")
    elif centrality >= REASON_THRESHOLD:
        reasons.append("near the centre of this region")

    if intro >= 1.0:
        reasons.append(f"reads as an introduction — the title says '{cue}'")
    elif intro >= REASON_THRESHOLD:
        reasons.append(f"reads as an introduction — the abstract says '{cue}'")

    if year_spread and candidate.year is not None:
        if year >= 1.0:
            reasons.append(f"among the earliest here ({candidate.year})")
        elif year >= REASON_THRESHOLD:
            reasons.append(f"from the earlier half of this region ({candidate.year})")

    if candidate.pages is not None:
        if candidate.pages <= SHORT_PAGES:
            reasons.append(f"short ({candidate.pages} pages)")
        elif length >= REASON_THRESHOLD:
            reasons.append(f"not a long read ({candidate.pages} pages)")
    return tuple(reasons)


def rank_entry_points(
    candidates: Sequence[Candidate], matrix: np.ndarray
) -> list[Scored]:
    """Every candidate scored, best first. Rows of ``matrix`` align with them.

    Ties break on raw centrality, then on the earlier year, then on the lower
    paper id, so the same set always ranks the same way.
    """
    n = len(candidates)
    if matrix.shape[0] != n:
        raise ValueError(
            f"{n} candidates but {matrix.shape[0]} vectors; rows must align"
        )
    if n < 2:
        return []

    unit = _unit(np.asarray(matrix, dtype=np.float64))
    centre = _unit(unit.mean(axis=0))
    raw_centrality = unit @ centre
    centrality = _rank_normalised(raw_centrality)

    years = _year_scores(candidates)
    year_spread = len({c.year for c in candidates if c.year is not None}) >= 2

    scored: list[Scored] = []
    for i, candidate in enumerate(candidates):
        intro, cue = intro_cue(candidate.title, candidate.abstract)
        length = _length_score(candidate.pages)
        score = (
            WEIGHT_CENTRALITY * centrality[i]
            + WEIGHT_INTRO * intro
            + WEIGHT_YEAR * years[i]
            + WEIGHT_LENGTH * length
        )
        scored.append(
            Scored(
                paper_id=candidate.paper_id,
                score=round(float(score), 4),
                centrality=round(float(raw_centrality[i]), 4),
                reasons=_reasons(
                    candidate,
                    centrality=float(centrality[i]),
                    intro=intro,
                    cue=cue,
                    year=years[i],
                    year_spread=year_spread,
                    length=length,
                ),
            )
        )

    year_of = {c.paper_id: c.year for c in candidates}
    scored.sort(
        key=lambda s: (
            -s.score,
            -s.centrality,
            year_of[s.paper_id] if year_of[s.paper_id] is not None else float("inf"),
            s.paper_id,
        )
    )
    return scored


def reading_order(
    candidates: Sequence[Candidate],
    matrix: np.ndarray,
    *,
    limit: int = MAX_READING_ORDER,
) -> list[int]:
    """A walk through the set, starting at the entry point.

    Each step goes to the unvisited paper maximising a blend of similarity to
    the previous stop and its own entry-point score. Similarity keeps the walk
    inside one sub-topic until that sub-topic is exhausted; the score term
    means that among several equally close neighbours the readable one comes
    first.
    """
    scored = rank_entry_points(candidates, matrix)
    if not scored:
        return []

    index_of = {c.paper_id: i for i, c in enumerate(candidates)}
    unit = _unit(np.asarray(matrix, dtype=np.float64))
    scores = np.zeros(len(candidates))
    for s in scored:
        scores[index_of[s.paper_id]] = s.score

    order = [index_of[scored[0].paper_id]]
    visited = np.zeros(len(candidates), dtype=bool)
    visited[order[0]] = True
    stops = min(limit, len(candidates))
    while len(order) < stops:
        similarity = unit @ unit[order[-1]]
        pull = WALK_SIMILARITY_WEIGHT * similarity + WALK_SCORE_WEIGHT * scores
        pull[visited] = -np.inf
        # argmax takes the first maximum, so a tie resolves by candidate
        # position — arbitrary, but the same arbitrary answer every time.
        nxt = int(np.argmax(pull))
        order.append(nxt)
        visited[nxt] = True
    return [candidates[i].paper_id for i in order]
