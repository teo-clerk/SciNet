"""The librarian's tools return evidence, or say plainly why they cannot.

Every result carries the paper ids it rests on — the citation gate admits
nothing else, so a tool that returned prose without ids would silently
disarm the whole guarantee.
"""

from __future__ import annotations

import numpy as np
import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.models import MarkdownDoc, Paper, PaperMeta
from app.models.enums import PaperStatus
from app.services.librarian import tools
from app.workers.embed_handlers import open_store

FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, section, content='chunks', content_rowid='id',
    tokenize='porter unicode61')
"""


@pytest.fixture
def env(tmp_path, db):
    settings = Settings(
        data_dir=tmp_path,
        library_dir=tmp_path / "library",
        markdown_dir=tmp_path / "markdown",
        vectors_dir=tmp_path / "vectors",
        models_dir=tmp_path / "models",
        db_path=tmp_path / "t.db",
        embed_model="test-embed",
        embed_dim=8,
    )
    settings.vectors_dir.mkdir(parents=True, exist_ok=True)

    db.execute(text(FTS_DDL))
    for i, title in (
        (1, "Kalman Filtering for Orbit Determination"),
        (2, "Deep Learning for SAR Imagery"),
    ):
        db.add(
            Paper(
                id=i,
                content_sha256=f"{i:064d}",
                work_key=f"w{i}",
                pdf_path=str(tmp_path / f"{i}.pdf"),
                status=PaperStatus.READY,
            )
        )
        db.add(PaperMeta(paper_id=i, title=title))
    db.flush()  # papers must exist before the raw chunk inserts reference them
    db.execute(
        text(
            "INSERT INTO chunks (id, paper_id, ord, section, text) VALUES "
            "(1, 1, 0, 'Intro', 'The Kalman filter estimates spacecraft state.'), "
            "(2, 2, 0, 'Intro', 'Convolutional networks classify SAR scenes.')"
        )
    )
    db.execute(text("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')"))

    md = tmp_path / "markdown" / "000001.md"
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text("A" * 100 + "B" * 100, encoding="utf-8")
    db.add(MarkdownDoc(paper_id=1, md_path=str(md), tier=0, parser="test"))
    db.flush()

    store = open_store(settings)
    store.add(1, np.array([1, 0, 0, 0, 0, 0, 0, 0], dtype=np.float32))
    store.add(2, np.array([0.9, 0.1, 0, 0, 0, 0, 0, 0], dtype=np.float32))
    store.flush()
    return settings


def test_fulltext_returns_evidence_with_snippets(env, db) -> None:
    result = tools.search_fulltext(db, "kalman")
    assert result.paper_ids == (1,)
    assert "Kalman" in result.summary and "[#1]" in result.summary


def test_fulltext_says_plainly_when_nothing_matches(env, db) -> None:
    result = tools.search_fulltext(db, "zzzunfindable")
    assert result.paper_ids == ()
    assert "no full-text matches" in result.summary


def test_semantic_searches_by_vector(env, db, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.embed.encoder.encode_query",
        lambda q, settings=None, device=None: np.array(
            [1, 0, 0, 0, 0, 0, 0, 0], dtype=np.float32
        ),
    )
    result = tools.search_semantic(db, env, "state estimation")
    assert result.paper_ids[0] == 1
    assert "similarity" in result.summary


def test_similar_excludes_the_anchor(env, db) -> None:
    result = tools.similar(db, env, paper_id=1)
    assert 1 not in result.paper_ids
    assert result.paper_ids == (2,)


def test_read_windows_and_reports_extent(env, db) -> None:
    result = tools.read_window(db, 1, offset=90, window=20)
    assert result.paper_ids == (1,)
    assert "chars 90-110 of 200" in result.summary
    assert "AAAAAAAAAABBBBBBBBBB" in result.summary


def test_missing_documents_are_stated_not_raised(env, db) -> None:
    assert "not been parsed" in tools.read_window(db, 2).summary
    assert "no embedding" in tools.similar(db, env, paper_id=99).summary
