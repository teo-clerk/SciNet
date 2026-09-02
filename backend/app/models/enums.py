"""String constants for the TEXT-typed status/kind columns.

Kept as plain strings rather than SQLAlchemy Enum so migrations stay simple and
values remain readable in `sqlite3` sessions.
"""

from __future__ import annotations

from enum import StrEnum


class PaperStatus(StrEnum):
    PENDING = "pending"
    PARSING = "parsing"
    PARSED = "parsed"
    EMBEDDED = "embedded"
    TAGGED = "tagged"
    READY = "ready"
    FAILED = "failed"
    #: Unreadable, and its file has been moved out of the library. Distinct
    #: from FAILED, which leaves the file where it is: this one says the
    #: library on disk no longer matches what the row describes.
    QUARANTINED = "quarantined"


class JobKind(StrEnum):
    PARSE = "parse"
    METADATA = "metadata"
    EMBED = "embed"
    TAG = "tag"
    PROJECT = "project"
    ENRICH = "enrich"


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    DEAD = "dead"


class MetaSource(StrEnum):
    PDF_EMBEDDED = "pdf_embedded"
    #: A book's own package metadata (EPUB OPF, MOBI header). Distinct from
    #: PDF_EMBEDDED because it is far more trustworthy: an EPUB's title is
    #: what the publisher typed, where a PDF's is often the LaTeX template's.
    EBOOK_EMBEDDED = "ebook_embedded"
    REGEX = "regex"
    HEURISTIC = "heuristic"
    #: An abstract assembled from the document's own paragraphs because it
    #: had none. Books land here. Kept distinct so the interface can say so,
    #: and so it is never mistaken for something an author wrote.
    EXTRACTED_DIGEST = "extracted_digest"
    LLM = "llm"
    CROSSREF = "crossref"
    OPENALEX = "openalex"
    ARXIV = "arxiv"
    MANUAL = "manual"


class TagKind(StrEnum):
    DOMAIN = "domain"
    METHOD = "method"
    TASK = "task"
    ARTIFACT = "artifact"


class TagStatus(StrEnum):
    APPROVED = "approved"
    PENDING_REVIEW = "pending_review"
    MERGED = "merged"


class ModelVerdict(StrEnum):
    """What a measurement concluded — the outcome, not a fit prediction."""

    GPU = "gpu"  # fully resident on the card when warmed
    PARTIAL = "partial"  # split between VRAM and system RAM
    CPU_ONLY = "cpu-only"  # served from system RAM: ~20x slower, silently
    UNPROVEN = "unproven"  # never measured; the house rule: not assumed to fit


class ProfileSource(StrEnum):
    BUILTIN = "builtin"  # seeded from the shipped registry
    DISCOVERED = "discovered"  # found installed in an Ollama store
    USER = "user"  # added by hand in the Model Lab


# Job priority — lower runs first. Tagging is deliberately last so the map is
# navigable long before tags finish (see roadmap M2).
PRIORITY = {
    JobKind.PARSE: 10,
    JobKind.METADATA: 20,
    JobKind.EMBED: 30,
    JobKind.PROJECT: 40,
    JobKind.ENRICH: 50,
    JobKind.TAG: 100,
}
