"""SQLite engine and session factory.

Concurrency model: the worker process is the *only* writer of paper data; the
API reads. WAL plus a generous busy timeout keeps the two out of each other's
way — see the "SQLite writer discipline" edge case in the design plan.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


def _apply_pragmas(dbapi_conn: sqlite3.Connection, _record: object) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA temp_store=MEMORY")
    cur.close()


def build_engine(url: str | None = None) -> Engine:
    settings = get_settings()
    settings.ensure_dirs()
    engine = create_engine(
        url or settings.database_url,
        future=True,
        # check_same_thread=False is required because FastAPI serves requests
        # from a threadpool; the pragmas above make concurrent access safe.
        connect_args={"check_same_thread": False},
    )
    event.listen(engine, "connect", _apply_pragmas)
    return engine


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = build_engine()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for worker-side code."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency — read-oriented, no implicit commit."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()
