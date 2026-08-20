"""Shared fixtures.

Each test gets an isolated on-disk SQLite database. On-disk rather than
``:memory:`` because the queue's behaviour under WAL and concurrent connections
is part of what is being tested.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.core.db import Base, build_engine


@pytest.fixture
def engine(tmp_path):
    eng = build_engine(f"sqlite+pysqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def sf(engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def db(sf) -> Session:
    session = sf()
    yield session
    session.close()


@pytest.fixture(scope="session")
def pdf_fixtures(tmp_path_factory) -> dict[str, Path]:
    """Synthetic PDFs, one per failure mode the parse pipeline must handle."""
    from tests.fixtures.generate import build_all

    return build_all(tmp_path_factory.mktemp("pdfs"))
