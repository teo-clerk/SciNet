#!/usr/bin/env python
"""Build the offline sample library: forty-one public-domain texts, once.

A first-run screen that says "drop your library here" is a door with nothing
behind it for someone who has no library yet, and a demo corpus that has to be
downloaded is a door with a queue in front of it. This bundle is the third
option: chapter-length openings of public-domain works — Plato to Darwin —
small enough to commit to the repository and install with one click and no
network at all. The pipeline reads them as Markdown the way it reads a
reader's own notes.

Every file is named ``Theme_NN-slug.md`` so the same evaluator that scores the
aerospace benchmark can score this one against five hand-assigned themes, and
every file opens with the front matter the metadata stage reads:

    # On Liberty

    Author: John Stuart Mill
    Year: 1859
    Source: Project Gutenberg #34901 …

The texts come from Project Gutenberg, whose header and licence boilerplate are
stripped — the works and these translations are in the public domain; the
Project Gutenberg name is a trademark that attaches to its formatted files,
not to the texts, and is not used in the output. ``SOURCES.md`` credits each
ebook number, translator and edition. The build is reproducible: the SHA-256 of
every source file is recorded in ``sources.lock.json`` and checked on a rebuild.

This is a developer tool, run once, like ``fetch_demo_corpus.py``. The app never
fetches anything; its egress log stays empty.

    uv run python ../scripts/build_sample_bundle.py            # fetch, extract, write
    uv run python ../scripts/build_sample_bundle.py --check    # verify against the lock
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "demo" / "samples" / "history-of-thought"
LOCK = OUT_DIR / "sources.lock.json"
SOURCE_URL = "https://www.gutenberg.org/cache/epub/{id}/pg{id}.txt"
USER_AGENT = "SciNet-sample-bundle/0.1 (+https://github.com/teo-clerk/SciNet)"
PAUSE_S = 1.0

#: Characters kept per work, cut at a paragraph end. About twenty-five printed
#: pages: enough for a chapter's argument, small enough that forty of them fit
#: in two megabytes and parse in seconds.
WINDOW_CHARS = 45_000
#: A paragraph shorter than this near the start is a heading, a dedication or a
#: table of contents line, not the text.
MIN_OPENING_PARAGRAPH = 300

START_RE = re.compile(
    r"\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*"
)
END_RE = re.compile(r"\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK")


@dataclass(frozen=True)
class Work:
    theme: str
    slug: str
    ebook: int
    title: str
    author: str
    year: int
    edition: str
    #: A distinctive phrase from the text's own first sentence, matched loosely,
    #: so the extract begins with the author and not with the translator's
    #: introduction. Empty means "the first substantial paragraph".
    start: str = ""
    #: A word that must appear in the ebook's header, guarding against a wrong
    #: ebook number silently producing the wrong book.
    expect: str = ""


# fmt: off
WORKS: tuple[Work, ...] = (
    # --- the ancient good life ----------------------------------------------
    Work("AncientEthics", "plato-apology", 1656, "Apology", "Plato", -399,
         "Benjamin Jowett's translation (1871)",
         "How you, O Athenians, have been affected by my accusers", "Apology"),
    Work("AncientEthics", "plato-crito", 1657, "Crito", "Plato", -399,
         "Benjamin Jowett's translation (1871)",
         "Why have you come at this hour, Crito", "Crito"),
    Work("AncientEthics", "plato-phaedo", 1658, "Phaedo", "Plato", -380,
         "Benjamin Jowett's translation (1871)",
         "Were you yourself, Phaedo, in the prison with Socrates", "Phaedo"),
    Work("AncientEthics", "plato-meno", 1643, "Meno", "Plato", -385,
         "Benjamin Jowett's translation (1871)",
         "Can you tell me, Socrates, whether virtue is acquired by teaching", "Meno"),
    Work("AncientEthics", "aristotle-ethics", 8438, "The Nicomachean Ethics",
         "Aristotle", -340, "D. P. Chase's translation (1847)",
         "Every art, and every science reduced to a teachable form", "Ethics"),
    Work("AncientEthics", "epictetus-enchiridion", 45109, "The Enchiridion",
         "Epictetus", 125, "Thomas Wentworth Higginson's translation (1865)",
         "There are things which are within our power", "Enchiridion"),
    Work("AncientEthics", "aurelius-meditations", 2680, "Meditations",
         "Marcus Aurelius", 175, "Meric Casaubon's translation (1634)",
         "Of my grandfather Verus I have learned", "Aurelius"),
    Work("AncientEthics", "boethius-consolation", 14328,
         "The Consolation of Philosophy", "Boethius", 524,
         "H. R. James's translation (1897)", "", "Consolation"),
    Work("AncientEthics", "augustine-confessions", 3296, "Confessions",
         "Augustine of Hippo", 400, "E. B. Pusey's translation (1838)",
         "Great art Thou, O Lord, and greatly to be praised", "Confessions"),
    # --- what can be known ----------------------------------------------------
    Work("Knowledge", "descartes-discourse", 59, "Discourse on the Method",
         "René Descartes", 1637, "John Veitch's translation (1850)",
         "Good sense is, of all things among men, the most equally distributed",
         "Descartes"),
    Work("Knowledge", "bacon-novum-organum", 45988, "Novum Organum",
         "Francis Bacon", 1620, "the 1902 Devey edition",
         "minister and interpreter of nature", "Organum"),
    Work("Knowledge", "locke-understanding", 10615,
         "An Essay Concerning Human Understanding", "John Locke", 1689,
         "the 1690 text, volume one",
         "it is the understanding that sets man above", "Understanding"),
    Work("Knowledge", "hume-enquiry", 9662,
         "An Enquiry Concerning Human Understanding", "David Hume", 1748,
         "the 1777 edition",
         "Moral philosophy, or the science of human nature, may be treated",
         "Hume"),
    Work("Knowledge", "berkeley-principles", 4723,
         "A Treatise Concerning the Principles of Human Knowledge",
         "George Berkeley", 1710, "the 1734 edition",
         "Philosophy being nothing else but the study of wisdom and truth",
         "Berkeley"),
    Work("Knowledge", "kant-critique", 4280, "The Critique of Pure Reason",
         "Immanuel Kant", 1781, "J. M. D. Meiklejohn's translation (1855)",
         "Human reason, in one sphere of its cognition, is called upon", "Kant"),
    Work("Knowledge", "spinoza-ethics", 3800, "Ethics", "Baruch Spinoza", 1677,
         "R. H. M. Elwes's translation (1883)",
         "I mean that of which the essence involves existence", "Spinoza"),
    Work("Knowledge", "james-pragmatism", 5116, "Pragmatism", "William James",
         1907, "the 1907 lectures",
         "In the preface to that admirable collection of essays", "Pragmatism"),
    # --- how to live together -------------------------------------------------
    Work("Politics", "aristotle-politics", 6762, "Politics", "Aristotle", -350,
         "William Ellis's translation (1776)",
         "As we see that every city is a society", "Politics"),
    Work("Politics", "machiavelli-prince", 1232, "The Prince",
         "Niccolò Machiavelli", 1532, "W. K. Marriott's translation (1908)",
         "All states, all powers, that have held and hold rule over men",
         "Prince"),
    Work("Politics", "hobbes-leviathan", 3207, "Leviathan", "Thomas Hobbes", 1651,
         "the 1651 text", "the art whereby God hath made and governes the world",
         "Leviathan"),
    Work("Politics", "locke-government", 7370, "Second Treatise of Government",
         "John Locke", 1689, "the 1690 text",
         "It having been shewn in the foregoing discourse", "Government"),
    Work("Politics", "rousseau-social-contract", 46333, "The Social Contract",
         "Jean-Jacques Rousseau", 1762, "G. D. H. Cole's translation (1913)",
         "I mean to inquire if, in the civil order, there can be any sure",
         "Social Contract"),
    Work("Politics", "federalist-papers", 1404, "The Federalist Papers",
         "Alexander Hamilton, James Madison, John Jay", 1788, "the 1788 text",
         "unequivocal experience of the inefficacy of the subsisting federal",
         "Federalist"),
    Work("Politics", "mill-on-liberty", 34901, "On Liberty", "John Stuart Mill",
         1859, "the 1859 text",
         "The subject of this Essay is not the so-called Liberty of the Will",
         "Liberty"),
    Work("Politics", "smith-wealth-of-nations", 3300, "The Wealth of Nations",
         "Adam Smith", 1776, "the 1776 text, from the Introduction",
         "The annual labour of every nation is the fund", "Wealth"),
    # --- conscience and the individual ---------------------------------------
    Work("Conscience", "wollstonecraft-vindication", 3420,
         "A Vindication of the Rights of Woman", "Mary Wollstonecraft", 1792,
         "the 1792 text",
         "After considering the historic page, and viewing the living world",
         "Vindication"),
    Work("Conscience", "kant-metaphysic-of-morals", 5682,
         "Fundamental Principles of the Metaphysic of Morals", "Immanuel Kant",
         1785, "T. K. Abbott's translation (1873)",
         "Ancient Greek philosophy was divided into three sciences", "Morals"),
    Work("Conscience", "emerson-self-reliance", 2944, "Essays: First Series",
         "Ralph Waldo Emerson", 1841, "the 1841 text, from 'Self-Reliance'",
         "I read the other day some verses written by an eminent painter",
         "Emerson"),
    Work("Conscience", "thoreau-civil-disobedience", 71,
         "On the Duty of Civil Disobedience", "Henry David Thoreau", 1849,
         "the 1849 text", "I heartily accept the motto", "Disobedience"),
    Work("Conscience", "douglass-narrative", 23,
         "Narrative of the Life of Frederick Douglass", "Frederick Douglass",
         1845, "the 1845 text", "I was born in Tuckahoe", "Douglass"),
    Work("Conscience", "thoreau-walden", 205, "Walden", "Henry David Thoreau",
         1854, "the 1854 text",
         "When I wrote the following pages, or rather the bulk of them",
         "Walden"),
    Work("Conscience", "mill-utilitarianism", 11224, "Utilitarianism",
         "John Stuart Mill", 1863, "the 1863 text",
         "There are few circumstances among those which make up the present",
         "Utilitarianism"),
    Work("Conscience", "nietzsche-beyond-good-and-evil", 4363,
         "Beyond Good and Evil", "Friedrich Nietzsche", 1886,
         "Helen Zimmern's translation (1906)",
         "The Will to Truth, which is to tempt us to many a hazardous",
         "Nietzsche"),
    # --- the natural world -----------------------------------------------------
    Work("NaturalWorld", "lucretius-nature-of-things", 785,
         "On the Nature of Things", "Lucretius", -55,
         "William Ellery Leonard's verse translation (1916)",
         "Mother of Rome, delight of Gods and men", "Lucretius"),
    Work("NaturalWorld", "newton-opticks", 33504, "Opticks", "Isaac Newton", 1704,
         "the 1730 fourth edition",
         "My Design in this Book is not to explain the Properties of Light",
         "Opticks"),
    Work("NaturalWorld", "darwin-origin", 1228, "On the Origin of Species",
         "Charles Darwin", 1859, "the 1872 sixth edition",
         "When on board H.M.S. 'Beagle,' as naturalist", "Origin"),
    Work("NaturalWorld", "darwin-beagle", 944, "The Voyage of the Beagle",
         "Charles Darwin", 1839, "the 1845 second edition",
         "twice driven back by heavy", "Beagle"),
    Work("NaturalWorld", "darwin-descent-of-man", 2300, "The Descent of Man",
         "Charles Darwin", 1871, "the 1874 second edition",
         "The nature of the following work will be best understood", "Descent"),
    Work("NaturalWorld", "faraday-candle", 14474,
         "The Chemical History of a Candle", "Michael Faraday", 1861,
         "the 1861 lectures", "in return for the honour you do us by coming",
         "Candle"),
    Work("NaturalWorld", "wallace-malay-archipelago", 2530,
         "The Malay Archipelago", "Alfred Russel Wallace", 1869,
         "the 1869 text", "a globe or a map of the Eastern hemisphere",
         "Malay"),
    Work("NaturalWorld", "poincare-science-and-hypothesis", 39713,
         "Science and Hypothesis", "Henri Poincaré", 1902,
         "George Bruce Halsted's translation (1913), in The Foundations of "
         "Science",
         "For a superficial observer, scientific truth is beyond", "Hypothesis"),
)
# fmt: on

THEMES = {
    "AncientEthics": "the ancient good life — Socrates, Aristotle and the Stoics",
    "Knowledge": "what can be known — from Descartes to James",
    "Politics": "how to live together — Aristotle to Mill",
    "Conscience": "conscience and the individual — duty, liberty and character",
    "NaturalWorld": "the natural world — natural philosophy and the sciences",
}


def _loose(phrase: str) -> re.Pattern[str]:
    """Match a phrase across any whitespace, either kind of quote, and single
    or doubled hyphens — the three ways Gutenberg's typography drifts."""
    parts = []
    for word in phrase.split():
        escaped = re.escape(word)
        escaped = escaped.replace("'", "['‘’]").replace('"', '["“”]')
        escaped = escaped.replace(r"\-", "[-–—]{1,2}")
        parts.append(escaped)
    return re.compile(r"\s+".join(parts), re.IGNORECASE)


def strip_gutenberg(raw: str) -> str:
    """The text between Project Gutenberg's start and end markers."""
    start = START_RE.search(raw)
    body = raw[start.end() :] if start else raw
    end = END_RE.search(body)
    return body[: end.start()] if end else body


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _opening_index(paragraphs: list[str], work: Work) -> int:
    if work.start:
        anchor = _loose(work.start)
        for i, paragraph in enumerate(paragraphs):
            if anchor.search(paragraph):
                return i
        raise LookupError(f"anchor not found: {work.start!r}")
    for i, paragraph in enumerate(paragraphs):
        if len(paragraph) >= MIN_OPENING_PARAGRAPH and paragraph.count(". ") >= 2:
            return i
    raise LookupError("no substantial paragraph found")


def extract_window(text: str, work: Work, limit: int = WINDOW_CHARS) -> str:
    """From the work's first sentence onward, cut at a paragraph end."""
    paragraphs = _paragraphs(text)
    start = _opening_index(paragraphs, work)
    kept: list[str] = []
    total = 0
    for paragraph in paragraphs[start:]:
        # Gutenberg hard-wraps at ~70 columns; unwrap so the Markdown is prose.
        flat = re.sub(r"\s*\n\s*", " ", paragraph).strip()
        if total + len(flat) > limit and kept:
            break
        kept.append(flat)
        total += len(flat) + 2
    return "\n\n".join(kept)


def _year_label(year: int) -> str:
    return f"c. {-year} BC" if year < 0 else str(year)


def render(work: Work, body: str) -> str:
    """The Markdown file: title heading, labelled front matter, the text."""
    return "\n".join(
        [
            f"# {work.title}",
            "",
            f"Author: {work.author}",
            # A BC date has no year the metadata stage could file it under; the
            # sample gives it the year of the edition instead, in the note.
            f"Year: {work.year if work.year > 0 else ''}".rstrip(),
            f"Source: Project Gutenberg ebook #{work.ebook}, {work.edition}; "
            f"public domain. Written {_year_label(work.year)}.",
            "",
            body,
            "",
        ]
    )


def filename(work: Work, ordinal: int) -> str:
    return f"{work.theme}_{ordinal:02d}-{work.slug}.md"


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(client: httpx.Client, work: Work) -> bytes:
    response = client.get(SOURCE_URL.format(id=work.ebook))
    response.raise_for_status()
    return response.content


def verify_header(raw: str, work: Work) -> None:
    """A wrong ebook number produces the wrong book, silently, unless checked."""
    head = raw[:3000]
    if work.expect and work.expect.lower() not in head.lower():
        raise LookupError(f"header does not mention {work.expect!r}")


def write_sources(works: list[tuple[Work, str]]) -> None:
    lines = [
        "# Sources",
        "",
        "Every text in this folder is in the public domain, as is the translation "
        "or edition named. Files were built by `scripts/build_sample_bundle.py` "
        "from Project Gutenberg's plain-text editions, with the Project Gutenberg "
        "header and licence removed; the Project Gutenberg name is a trademark "
        "of the Project Gutenberg Literary Archive Foundation and attaches to "
        "their formatted files, not to these texts.",
        "",
        "Each file is a chapter-length opening (about 45,000 characters) of the "
        "work, not the whole work. Themes are hand-assigned for the clustering "
        "benchmark and are one defensible reading among several.",
        "",
        "| file | work | edition | ebook |",
        "|---|---|---|---|",
    ]
    for work, name in works:
        lines.append(
            f"| `{name}` | {work.author}, *{work.title}* "
            f"({_year_label(work.year)}) | {work.edition} | "
            f"[#{work.ebook}](https://www.gutenberg.org/ebooks/{work.ebook}) |"
        )
    (OUT_DIR / "SOURCES.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_sample_json(count: int) -> None:
    (OUT_DIR / "sample.json").write_text(
        json.dumps(
            {
                "name": "history-of-thought",
                "title": "History of Thought",
                "blurb": (
                    f"{count} short public-domain texts, from Plato to Darwin — "
                    "the ancient good life, what can be known, how to live "
                    "together, conscience, and the natural world. Installs "
                    "offline in seconds; the map takes a few minutes to build."
                ),
                "kind": "bundled",
                "themes": THEMES,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="re-download and compare against sources.lock.json; write nothing",
    )
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    lock: dict[str, str] = json.loads(LOCK.read_text()) if LOCK.exists() else {}
    new_lock: dict[str, str] = {}
    written: list[tuple[Work, str]] = []
    failures = 0
    ordinals: dict[str, int] = {}

    with httpx.Client(
        timeout=60, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    ) as client:
        for work in WORKS:
            ordinals[work.theme] = ordinals.get(work.theme, 0) + 1
            name = filename(work, ordinals[work.theme])
            try:
                data = fetch(client, work)
                digest = sha256_of(data)
                raw = data.decode("utf-8", errors="replace")
                verify_header(raw, work)
                body = extract_window(strip_gutenberg(raw), work)
            except Exception as exc:  # noqa: BLE001 - report and carry on
                failures += 1
                print(f"  [fail]  {name}: {exc}")
                time.sleep(PAUSE_S)
                continue

            pinned = lock.get(str(work.ebook))
            drift = (
                " (source changed since the lock)"
                if pinned and pinned != digest
                else ""
            )
            new_lock[str(work.ebook)] = digest
            if not args.check:
                (OUT_DIR / name).write_text(render(work, body), encoding="utf-8")
            written.append((work, name))
            print(f"  [ok]    {name}  {len(body) / 1024:.0f} KB{drift}")
            time.sleep(PAUSE_S)

    if not args.check and written:
        write_sources(written)
        write_sample_json(len(written))
        LOCK.write_text(json.dumps(new_lock, indent=2, sort_keys=True) + "\n")

    print(f"\n{len(written)} written, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
