"""Finding the paragraph that says what a document is about.

A paper tells you directly: it has an abstract, and the abstract is the single
most informative field on the map, because it is what the author wrote to say
what the work is. A book tells you nothing of the sort. It opens with a title
page, a copyright notice listing every printing since 1974, a dedication, and a
table of contents — several thousand characters of front matter containing
almost no information about the subject.

So the abstract is found by a ladder, and each rung is a weaker claim than the
one above it:

1. a heading that says "Abstract";
2. an unlabelled opening paragraph that has the shape of one;
3. the opening of a preface, foreword or introduction — where a book does in
   fact state its subject, just without calling it an abstract;
4. failing all of that, a digest assembled from the document's own most
   topical paragraphs.

Only the last rung invents anything, and it is the one books usually land on.

**The digest is deliberately the length of an abstract.** That is not a
performance limit — the encoder truncates long inputs anyway — it is about
comparability. The document vector places a document on the map, and a corpus
where books contribute four thousand characters and papers twelve hundred puts
a systematic difference between books and papers into the geometry. The map
would then cluster by *format*, showing you a "books" region, which is the one
thing it must never do: the whole point is that a book about protein folding
sits next to the papers about protein folding.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum

from app.services.parse.stopwords import STOPWORDS

#: Roughly the length of a journal abstract. See the module docstring.
TARGET_SYNOPSIS_CHARS = 1600
MAX_SYNOPSIS_CHARS = 2400
MIN_PARAGRAPH_CHARS = 200

#: How many slices the document is divided into when sampling for the digest.
#: One paragraph is taken from each, so a five-hundred-page book is not
#: characterised entirely by its first chapter.
DIGEST_SEGMENTS = 6
#: The document's own vocabulary: the most frequent content words, which for a
#: book on protein folding are the words a summary of it would have to use.
TOPIC_TERMS = 40
MIN_TERM_LENGTH = 4

#: A heading under which an author says what the *book* is. This is the
#: closest thing a book has to an abstract, and it is explicitly labelled,
#: which makes it better evidence than any shape heuristic.
PREFACE_RE = re.compile(
    r"^#{1,6}\s*\**\s*(?:[IVXivx0-9]+[.\s)]+)?\s*"
    r"(?:preface|foreword|forward|prologue|about\s+this\s+book|"
    r"pr[eé]face|prefazione|vorwort|pr[oó]logo|synopsis)\b",
    re.IGNORECASE | re.MULTILINE,
)

#: A heading under which an author starts on the subject matter. Weaker: an
#: introduction is about the field, not about this work, so it is tried after
#: the shape heuristic rather than before it.
INTRODUCTION_RE = re.compile(
    r"^#{1,6}\s*\**\s*(?:[IVXivx0-9]+[.\s)]+)?\s*"
    r"(?:introduction|introducci[oó]n|einleitung|introduzione|"
    r"overview|general\s+introduction|summary)\b",
    re.IGNORECASE | re.MULTILINE,
)

#: Longest a document can be and still plausibly be a paper — roughly sixty-
#: five printed pages. Above it, the shape heuristic is switched off: its rule
#: is "the first substantial paragraph near the top is the abstract", which is
#: true of a preprint and false of a five-hundred-page book, where the first
#: substantial paragraph is the opening of chapter one. Applying it anyway
#: gives every book a summary that describes its first chapter.
MAX_PAPER_CHARS = 120_000

WORD_RE = re.compile(rf"[^\W\d_]{{{MIN_TERM_LENGTH},}}", re.UNICODE)
SENTENCE_END_RE = re.compile(r"[.!?](?:\s|$)")

#: A figure or table caption. It is prose, and it is about a figure rather
#: than about the document — a paper's best-scoring paragraph is frequently its
#: richest caption, and a textbook is full of them.
CAPTION_RE = re.compile(
    r"^\**\s*(?:fig(?:ure)?|table|chart|scheme|box|plate|exhibit)\s*\.?\s*"
    r"[0-9IVX]+\b",
    re.IGNORECASE,
)

#: A converted table arrives as a run of pipe-delimited cells. It reads as
#: prose to every other test here — long enough, punctuated, not especially
#: capitalised — and a keywords table was being returned as a paper's abstract.
TABLE_RE = re.compile(r"\|.*\|.*\|")

#: How much of a paragraph's opening is examined for publishing boilerplate.
#: A copyright notice *leads* with it; a real abstract that merely contains the
#: phrase does so because the extractor ran past its end into a page footer.
#: Searching the whole paragraph rejected two genuine abstracts on the real
#: corpus for carrying "All rights reserved" a thousand characters in.
BOILERPLATE_HEAD_CHARS = 200

#: Front matter and running furniture that reads like prose but says nothing.
BOILERPLATE_RE = re.compile(
    r"all\s+rights\s+reserved|no\s+part\s+of\s+this\s+(?:book|publication)|"
    r"library\s+of\s+congress|british\s+library\s+cataloguing|"
    r"printed\s+in\s+the\s+united|isbn|first\s+published|"
    # "some rights reserved" and "all rights reserved" are the same notice.
    r"rights\s+reserved|exclusive\s+licensee|copyright\s*©|"
    r"typeset\s+by|cover\s+design|reprinted|this\s+page\s+intentionally",
    re.IGNORECASE,
)


class Strategy(StrEnum):
    """Which rung of the ladder produced the text."""

    LABELLED = "labelled_abstract"
    PREFACE = "preface"
    UNLABELLED = "unlabelled_abstract"
    INTRODUCTION = "introduction"
    DIGEST = "topical_digest"


@dataclass(frozen=True)
class Synopsis:
    text: str
    strategy: Strategy


def is_boilerplate(text: str) -> bool:
    """Does this paragraph *open* as a copyright or cataloguing notice?"""
    return bool(BOILERPLATE_RE.search(text[:BOILERPLATE_HEAD_CHARS]))


def _paragraphs(markdown: str) -> list[str]:
    return [block.strip() for block in markdown.split("\n\n") if block.strip()]


def is_furniture(text: str) -> bool:
    """Is this a piece of the document rather than a statement about it?

    Shared by both prose tests. They differ in their thresholds — one is
    tuned for a paper's front matter and one for a book's body — but they
    must agree about what is not writing at all, and they did not: a fragment
    beginning mid-sentence was rejected by one and accepted by the other, so
    which rung of the ladder you landed on decided whether you got it.
    """
    if is_boilerplate(text):
        return True
    if CAPTION_RE.match(text):
        return True
    if TABLE_RE.search(text):
        return True
    # Begins mid-sentence, so it is the tail of a paragraph the converter split
    # — across a page break, a column, or in OCR output, where it is the norm.
    # Quoting from the middle of a sentence reads as damage whatever else it
    # says. Scripts without letter case are unaffected: islower() is False.
    return text[:1].islower()


def is_prose(paragraph: str) -> bool:
    """Is this running text, or is it furniture?

    Tables of contents, index pages, author lists and copyright notices all
    reach this function, and each has a shape: short, heavy with capitals and
    digits, or missing sentence punctuation entirely. Prose has sentences.
    """
    text = " ".join(paragraph.split())
    if len(text) < MIN_PARAGRAPH_CHARS:
        return False
    if text.startswith("#") or text.startswith(">") or text.startswith("- "):
        return False
    if is_furniture(text):
        return False
    if len(SENTENCE_END_RE.findall(text)) < 2:
        return False

    words = text.split()
    if not words:
        return False
    # A contents listing or an index is mostly capitalised tokens and numbers.
    capitalised = sum(1 for w in words if w[:1].isupper())
    digits = sum(1 for w in words if any(c.isdigit() for c in w))
    return capitalised / len(words) < 0.4 and digits / len(words) < 0.15


def topic_terms(markdown: str, limit: int = TOPIC_TERMS) -> set[str]:
    """The document's characteristic vocabulary.

    Frequency over the whole document with function words removed. Crude, and
    it does not need to be otherwise: the job is only to recognise a paragraph
    that is *about the book's subject* rather than about its printing history,
    and the subject is what the book keeps talking about.
    """
    counts = Counter(
        word for word in WORD_RE.findall(markdown.lower()) if word not in STOPWORDS
    )
    return {word for word, _ in counts.most_common(limit)}


def _score(paragraph: str, terms: set[str]) -> float:
    """How much of the document's vocabulary this paragraph covers.

    Distinct terms, not total occurrences: a paragraph that repeats one word
    twenty times is not twenty times more informative, and rewarding that picks
    out chapter headings and running heads.
    """
    words = {w for w in WORD_RE.findall(paragraph.lower())}
    if not words:
        return 0.0
    return len(words & terms) / (1 + len(words) / 100)


def labelled_abstract(markdown: str) -> str | None:
    """Rung 1, delegated to the existing paper-shaped extractor."""
    from app.services.metadata.extract import extract_abstract

    return extract_abstract(markdown)


def unlabelled_abstract(markdown: str) -> str | None:
    """An abstract with no heading, for documents short enough to have one."""
    if len(markdown) > MAX_PAPER_CHARS:
        return None

    from app.services.metadata.extract import extract_abstract_unlabelled

    return extract_abstract_unlabelled(markdown)


def preface(markdown: str) -> str | None:
    return _section_opening(markdown, PREFACE_RE)


def introduction(markdown: str) -> str | None:
    return _section_opening(markdown, INTRODUCTION_RE)


def _section_opening(markdown: str, heading: re.Pattern[str]) -> str | None:
    """The first prose under a matching heading.

    Every matching heading is tried, not just the first: a book's preface is
    sometimes a page of acknowledgements, and the one two headings later is
    where the subject actually gets stated.
    """
    for match in heading.finditer(markdown):
        collected: list[str] = []
        for paragraph in _paragraphs(markdown[match.end() :]):
            if paragraph.startswith("#"):
                break
            if not is_prose(paragraph):
                continue
            collected.append(" ".join(paragraph.split()))
            if sum(len(p) for p in collected) >= TARGET_SYNOPSIS_CHARS:
                break
        if collected:
            return " ".join(collected)[:MAX_SYNOPSIS_CHARS]
    return None


def topical_digest(markdown: str) -> str | None:
    """Rung 4: the document's most topical paragraphs, spread across it.

    Sampled across the whole document rather than taken from the front. A
    book's first pages are its least informative, and its best single paragraph
    for this purpose is as likely to be in chapter nine as in chapter one.
    Taking the top-scoring paragraphs globally instead would reliably return
    six paragraphs from whichever chapter is densest, which describes that
    chapter rather than the book.
    """
    candidates = [(index, p) for index, p in enumerate(_paragraphs(markdown))]
    prose = [(index, p) for index, p in candidates if is_prose(p)]
    if not prose:
        return None

    terms = topic_terms(markdown)
    if not terms:
        return None

    span = max(1, len(prose) // DIGEST_SEGMENTS)
    picked: list[tuple[int, str]] = []
    for start in range(0, len(prose), span):
        segment = prose[start : start + span]
        if not segment:
            continue
        best = max(segment, key=lambda item: _score(item[1], terms))
        picked.append(best)
        if len(picked) >= DIGEST_SEGMENTS:
            break

    # Restored to document order: the digest reads as an excerpt rather than a
    # ranked list, and the embedding is not sensitive to it either way.
    picked.sort(key=lambda item: item[0])

    # Each segment gets an equal share of the budget rather than filling it in
    # order. Taking whole paragraphs until the budget ran out sounded fine and
    # was not: one long paragraph swallows the whole allowance, and the digest
    # collapses back to a single passage from a single chapter — the exact
    # failure sampling across the document was meant to prevent.
    share = max(1, TARGET_SYNOPSIS_CHARS // max(1, len(picked)))
    out = [_excerpt(" ".join(paragraph.split()), share) for _, paragraph in picked]
    out = [piece for piece in out if piece]
    if not out:
        return None
    return " ".join(out)[:MAX_SYNOPSIS_CHARS]


def _excerpt(text: str, limit: int) -> str:
    """The opening of a paragraph, cut at a sentence end where possible."""
    if len(text) <= limit:
        return text
    window = text[:limit]
    ends = list(SENTENCE_END_RE.finditer(window))
    if ends:
        return window[: ends[-1].end()].strip()
    cut = window.rfind(" ")
    return (window[:cut] if cut > 0 else window).strip() + "…"


#: Ordered strongest evidence first. A labelled abstract is what the author
#: wrote to answer this question; a preface is the same thing under a different
#: name; the shape heuristic is a guess; an introduction is about the field
#: rather than the work; the digest is assembled by us.
LADDER = (
    (Strategy.LABELLED, labelled_abstract),
    (Strategy.PREFACE, preface),
    (Strategy.UNLABELLED, unlabelled_abstract),
    (Strategy.INTRODUCTION, introduction),
    (Strategy.DIGEST, topical_digest),
)


def find_synopsis(markdown: str | None) -> Synopsis | None:
    """The best available statement of what this document is about."""
    if not markdown or not markdown.strip():
        return None
    for strategy, finder in LADDER:
        text = finder(markdown)
        if text and text.strip():
            return Synopsis(" ".join(text.split()), strategy)
    return None
