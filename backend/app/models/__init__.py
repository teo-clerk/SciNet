"""SQLAlchemy models.

Every model is re-exported here so that a single import of ``app.models``
populates ``Base.metadata`` for Alembic autogenerate.
"""

from app.models.enums import (
    PRIORITY,
    JobKind,
    JobState,
    MetaSource,
    ModelVerdict,
    PaperStatus,
    ProfileSource,
    QuantityStatus,
    TagKind,
    TagStatus,
)
from app.models.model_profile import ModelProfile
from app.models.paper import Chunk, DocVector, MarkdownDoc, Paper, PaperMeta
from app.models.projection import (
    Cluster,
    ClusterLink,
    Projection,
    ProjectionRun,
)
from app.models.quantity import Quantity
from app.models.system import EgressLog, Job, Setting
from app.models.tagging import PaperTag, Tag

__all__ = [
    "PRIORITY",
    "Chunk",
    "Cluster",
    "ClusterLink",
    "DocVector",
    "EgressLog",
    "Job",
    "JobKind",
    "JobState",
    "MarkdownDoc",
    "MetaSource",
    "ModelProfile",
    "ModelVerdict",
    "Paper",
    "PaperMeta",
    "PaperStatus",
    "PaperTag",
    "ProfileSource",
    "Projection",
    "ProjectionRun",
    "Quantity",
    "QuantityStatus",
    "Setting",
    "Tag",
    "TagKind",
    "TagStatus",
]
