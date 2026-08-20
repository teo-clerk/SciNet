"""SQLAlchemy models.

Every model is re-exported here so that a single import of ``app.models``
populates ``Base.metadata`` for Alembic autogenerate.
"""

from app.models.enums import (
    PRIORITY,
    JobKind,
    JobState,
    MetaSource,
    PaperStatus,
    TagKind,
    TagStatus,
)
from app.models.paper import Chunk, DocVector, MarkdownDoc, Paper, PaperMeta
from app.models.projection import Cluster, Projection, ProjectionRun
from app.models.system import EgressLog, Job, Setting
from app.models.tagging import PaperTag, Tag

__all__ = [
    "PRIORITY",
    "Chunk",
    "Cluster",
    "DocVector",
    "EgressLog",
    "Job",
    "JobKind",
    "JobState",
    "MarkdownDoc",
    "MetaSource",
    "Paper",
    "PaperMeta",
    "PaperStatus",
    "PaperTag",
    "Projection",
    "ProjectionRun",
    "Setting",
    "Tag",
    "TagKind",
    "TagStatus",
]
