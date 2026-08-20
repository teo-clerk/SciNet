"""Durable job queue, key-value settings, and the network egress audit log."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.types import UtcDateTime, utcnow
from app.models.enums import JobState


class Job(Base):
    """A unit of pipeline work.

    The queue lives in SQLite rather than Redis so the application has no
    background daemon to install or keep alive. The worker process claims rows
    with an atomic conditional UPDATE — see ``app.workers.queue``.
    """

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    paper_id: Mapped[int | None] = mapped_column(Integer, index=True)

    # Lower runs first. Tagging sits at 100 so the map is usable before tags land.
    priority: Mapped[int] = mapped_column(Integer, default=100)
    state: Mapped[str] = mapped_column(String(16), default=JobState.QUEUED)

    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    last_error: Mapped[str | None] = mapped_column(Text)
    payload_json: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    __table_args__ = (
        # Covers the claim query: WHERE state=? AND kind=? ORDER BY priority, id
        Index("ix_jobs_pick", "state", "kind", "priority", "id"),
    )


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, onupdate=utcnow
    )


class EgressLog(Base):
    """Audit trail for every outbound network call.

    Enrichment is off by default. When it is on, this table is what makes the
    claim "nothing left the machine except these lookups" checkable.
    """

    __tablename__ = "egress_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, index=True)
    service: Mapped[str] = mapped_column(String(32))
    url: Mapped[str] = mapped_column(Text)
    paper_id: Mapped[int | None] = mapped_column(Integer)
    status_code: Mapped[int | None] = mapped_column(Integer)
