"""UMAP projection runs, per-paper coordinates, and clusters.

Multiple runs coexist so that a refit (or a different embedding model) can be
computed in the background and swapped in atomically by flipping ``is_active``.
The previous run stays on disk, which also makes A/B-ing two embedding models a
matter of toggling a row.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.core.types import UtcDateTime, utcnow


class ProjectionRun(Base):
    __tablename__ = "projection_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_id: Mapped[str] = mapped_column(String(128))
    params_json: Mapped[str] = mapped_column(Text)

    n_fit: Mapped[int | None] = mapped_column(Integer)
    method: Mapped[str] = mapped_column(String(16), default="umap")  # umap | pca

    # Persisted together: the reducer, and the exact matrix it was fitted on.
    # Drift detection needs the fit matrix, not just the reducer.
    model_path: Mapped[str | None] = mapped_column(Text)
    fit_matrix_path: Mapped[str | None] = mapped_column(Text)

    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    fitted_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    coords: Mapped[list[Projection]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    clusters: Mapped[list[Cluster]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class Projection(Base):
    __tablename__ = "projections"

    run_id: Mapped[int] = mapped_column(
        ForeignKey("projection_runs.id", ondelete="CASCADE"), primary_key=True
    )
    paper_id: Mapped[int] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True
    )

    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float] = mapped_column(Float)
    z: Mapped[float] = mapped_column(Float)

    # 1 => placed via reducer.transform(), not included in the original fit.
    is_transformed: Mapped[bool] = mapped_column(Boolean, default=False)
    # Mean cosine distance to the k nearest fit-set neighbours. High values mean
    # the paper sits off the fitted manifold and its position is provisional.
    off_manifold: Mapped[float | None] = mapped_column(Float)

    #: HDBSCAN's membership strength, 0..1. Low means the paper sits on a
    #: boundary between fields rather than inside one — which is information
    #: about the paper, not a defect in the clustering.
    cluster_probability: Mapped[float | None] = mapped_column(Float)

    cluster_id: Mapped[int | None] = mapped_column(
        ForeignKey("clusters.id", ondelete="SET NULL"), index=True
    )

    run: Mapped[ProjectionRun] = relationship(back_populates="coords")


class Cluster(Base):
    __tablename__ = "clusters"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("projection_runs.id", ondelete="CASCADE"), index=True
    )
    hdbscan_label: Mapped[int] = mapped_column(Integer)  # -1 = noise
    llm_label: Mapped[str | None] = mapped_column(Text)
    size: Mapped[int] = mapped_column(Integer, default=0)
    centroid_json: Mapped[str | None] = mapped_column(Text)
    top_terms_json: Mapped[str | None] = mapped_column(Text)

    run: Mapped[ProjectionRun] = relationship(back_populates="clusters")
