"""How much of a very long document is worth reading.

A personal library is bimodal and the reference one makes the point: 465
documents, median 24 pages, mean 131, longest 1,015. There are papers, and
there are books, and almost nothing in between — raising the cut from 60 pages
to 150 changes how many documents are affected by thirteen, out of 465.

That gap is what makes a page limit safe. Below it sit the papers, read whole;
above it sit the books, where the cost of reading every page is enormous and
the benefit is small:

* **Compute.** Tier 2 transcribes one page at a time through a vision model at
  8-15 s/page. On the 731-page Kauffman in this library that is a job measured
  in hours — one was killed after seven of them, still unfinished. At 80 pages
  the same book is bounded to roughly fifteen minutes.
* **Diminishing returns.** What the pipeline needs from a book is what it is
  *about*: the title, the preface, the introduction, the shape of the early
  chapters. That is settled well inside eighty pages. Chapter 34 does not move
  a book on the map.

Worth being precise about one thing, because it was the stated reason for this
change and it is not quite right: the downstream models were never actually at
risk of overflowing. ``document_text`` caps its input at 6,000 characters and
SciNCL truncates at 512 tokens regardless; the tagger caps its prompt at 6,000
characters against an 8,192-token context. Those limits already held, and they
held for the 535-page book that produced 1.1 MB of Markdown. The reason to stop
early is the compute, and the compute reason is overwhelming on its own.

**Page counts stay truthful.** A truncated document still records the number of
pages it really has; only the Markdown is short, and it says so.
"""

from __future__ import annotations

import re

#: Marks Markdown that stops before the document does. Written into the file so
#: it travels with the text: every later stage reads the Markdown, not the
#: database row, and each of them wants to know.
TRUNCATION_NOTE = (
    "[Document truncated at {kept} of {total} pages to bound parsing cost. "
    "The remaining pages were not read.]"
)
TRUNCATION_RE = re.compile(r"^\[Document truncated at (\d+) of (\d+) pages", re.M)


def append_note(markdown: str, *, kept: int, total: int) -> str:
    """Record that this Markdown is only the front of a longer document."""
    if kept >= total:
        return markdown
    note = TRUNCATION_NOTE.format(kept=kept, total=total)
    return f"{markdown.rstrip()}\n\n{note}\n"


def was_truncated(markdown: str | None) -> bool:
    """Did this Markdown stop early?

    Used where a length test would otherwise be fooled. The synopsis ladder
    decides a document is a book by how long it is, and truncation is exactly
    the operation that makes a book short — so a 700-page scan cut to 80 pages
    of sparse OCR would come back under the threshold and be treated as a
    preprint, which is the misclassification the length test exists to prevent.
    """
    return bool(markdown) and TRUNCATION_RE.search(markdown) is not None


def full_length(markdown: str | None) -> int | None:
    """The page count the document really had, if it was truncated."""
    match = TRUNCATION_RE.search(markdown or "")
    return int(match.group(2)) if match else None


def page_budget(total: int, limit: int) -> int:
    """How many pages to read from a document of ``total`` pages.

    A limit of zero or less means no limit, so the cap can be turned off
    without the callers growing a second code path.
    """
    if limit <= 0:
        return total
    return min(total, limit)
