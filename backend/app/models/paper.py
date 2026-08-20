"""Paper identity, bibliographic metadata, parse output, and chunks."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.core.types import UtcDateTime, utcnow
from app.models.enums import PaperStatus


class Paper(Base):
    """One physical PDF. Identity, provenance, lifecycle state."""

    __tablename__ = "papers"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Byte-identical dedup.
    content_sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # Logical dedup: doi | arxiv-base-id | slug(title)|first-author-surname.
    # Not unique — collisions are surfaced for review rather than dropped.
    work_key: Mapped[str] = mapped_column(String(512), index=True)

    pdf_path: Mapped[str] = mapped_column(Text)
    pdf_bytes: Mapped[int | None] = mapped_column(Integer)
    page_count: Mapped[int | None] = mapped_column(Integer)

    status: Mapped[str] = mapped_column(
        String(16), default=PaperStatus.PENDING, index=True
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    pipeline_version: Mapped[int] = mapped_column(Integer, default=1)

    added_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, onupdate=utcnow
    )

    meta: Mapped[PaperMeta | None] = relationship(
        back_populates="paper", cascade="all, delete-orphan", uselist=False
    )
    markdown: Mapped[MarkdownDoc | None] = relationship(
        back_populates="paper", cascade="all, delete-orphan", uselist=False
    )
    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="paper", cascade="all, delete-orphan"
    )


class PaperMeta(Base):
    """Bibliographic fields, with per-field provenance.

    ``field_sources_json`` maps each populated field to the MetaSource that won
    it, e.g. ``{"title": "pdf_embedded", "authors": "llm"}``. This is what makes
    it possible to re-run enrichment without clobbering better local values.
    """

    __tablename__ = "paper_meta"

    paper_id: Mapped[int] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True
    )

    title: Mapped[str | None] = mapped_column(Text)
    authors_json: Mapped[str | None] = mapped_column(Text)
    abstract: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)  # LLM-generated

    doi: Mapped[str | None] = mapped_column(String(255), index=True)
    arxiv_id: Mapped[str | None] = mapped_column(String(64), index=True)
    year: Mapped[int | None] = mapped_column(Integer, index=True)
    venue: Mapped[str | None] = mapped_column(Text)
    keywords_json: Mapped[str | None] = mapped_column(Text)

    field_sources_json: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column()

    paper: Mapped[Paper] = relationship(back_populates="meta")


class MarkdownDoc(Base):
    """Result of the tiered parse. One row per paper, replaced on re-parse."""

    __tablename__ = "markdown_docs"

    paper_id: Mapped[int] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True
    )
    md_path: Mapped[str] = mapped_column(Text)
    tier: Mapped[int] = mapped_column(Integer)  # 0 | 1 | 2
    parser: Mapped[str] = mapped_column(String(64))
    parser_version: Mapped[str | None] = mapped_column(String(64))
    quality_score: Mapped[float | None] = mapped_column()
    quality_report_json: Mapped[str | None] = mapped_column(Text)
    char_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    paper: Mapped[Paper] = relationship(back_populates="markdown")


class Chunk(Base):
    """A section-aware slice of the markdown, embedded for search."""

    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    paper_id: Mapped[int] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), index=True
    )
    ord: Mapped[int] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int | None] = mapped_column(Integer)

    paper: Mapped[Paper] = relationship(back_populates="chunks")

    __table_args__ = (Index("ix_chunks_paper_ord", "paper_id", "ord"),)


class DocVector(Base):
    """Row-index map into the document-vector memmap.

    Vectors themselves live in ``data/vectors/doc_vectors.f32`` — 4k x 1024 f32
    is 16 MB, so brute-force cosine over the whole corpus is ~2 ms and an ANN
    index would be pure complexity.
    """

    __tablename__ = "doc_vectors"

    paper_id: Mapped[int] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True
    )
    row_idx: Mapped[int] = mapped_column(Integer, unique=True)
    model_id: Mapped[str] = mapped_column(String(128), index=True)
    dim: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
