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
    REGEX = "regex"
    HEURISTIC = "heuristic"
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
