"""Tidying what a language model wrote before a reader sees it.

Shared by every stage that asks the local model for prose — region overviews,
bridge stories, the plain-English reading of a work — because the failure is
the same everywhere: a length cap applied as a slice ends a sentence mid-word,
which reads as a bug in the pipeline rather than as a limit.
"""

from __future__ import annotations


def trim_to_sentence(text: str, limit: int) -> str:
    """Cut to the last sentence that fits, not to the last character.

    A hard slice ends summaries mid-word ("...linking gut microbiota to suga"),
    which reads as a bug in the pipeline rather than a length cap. Falls back to
    a word boundary when the first sentence is already over the limit.
    """
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    if cut > limit // 3:
        return head[: cut + 1]
    space = head.rfind(" ")
    return (head[:space] if space > 0 else head).rstrip(",;:") + "…"


def collapse(text: object) -> str:
    """One line of single-spaced text, or the empty string for anything else."""
    if not isinstance(text, str):
        return ""
    return " ".join(text.split())
