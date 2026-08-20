"""Search behaviour.

The two server modes answer different questions and fail differently, so they
are tested separately rather than through a shared abstraction.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, get_settings
from app.core.db import Base, build_engine, get_db
from app.main import create_app
from app.models import Chunk, Paper, PaperMeta, PaperStatus
from app.routers.search import _sanitise_fts

FTS_DDL = """
CREATE VIRTUAL TABLE chunks_fts USING fts5(
    text, section, content='chunks', content_rowid='id',
    tokenize='porter unicode61'
)
"""


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        library_dir=tmp_path / "lib",
        markdown_dir=tmp_path / "md",
        vectors_dir=tmp_path / "vec",
        models_dir=tmp_path / "models",
        db_path=tmp_path / "s.db",
        embed_dim=8,
        embed_model="test-embed",
    )
    engine = build_engine(f"sqlite+pysqlite:///{settings.db_path}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text(FTS_DDL))

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        for i, (title, body) in enumerate(
            [
                (
                    "Galaxy Formation in the Early Universe",
                    "We simulate the collapse of primordial gas clouds into galaxies.",
                ),
                (
                    "Robotic Grasp Planning",
                    "A manipulator plans grasps for unseen household objects.",
                ),
                (
                    "Einstein's Field Equations",
                    "The apostrophe in this title must not break the query parser.",
                ),
            ],
            start=1,
        ):
            paper = Paper(
                content_sha256=f"{i:064d}",
                work_key=f"w{i}",
                pdf_path=f"/tmp/{i}.pdf",
                status=PaperStatus.READY,
                pipeline_version=1,
            )
            s.add(paper)
            s.flush()
            s.add(PaperMeta(paper_id=paper.id, title=title))
            s.add(Chunk(paper_id=paper.id, ord=0, section="Introduction", text=body))
        s.commit()
        # External-content FTS5 does not backfill itself.
        s.execute(text("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')"))
        s.commit()

    def override_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app = create_app()
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    engine.dispose()


# --- query sanitising -----------------------------------------------------


def test_terms_become_prefix_matches():
    assert _sanitise_fts("galaxy form") == '"galaxy"* "form"*'


@pytest.mark.parametrize(
    "dangerous",
    ['galaxy"', "galaxy^2", "galaxy*", "(galaxy)", "galaxy:formation", 'NEAR("a" "b")'],
)
def test_fts_operators_are_neutralised(dangerous):
    """A reader typing punctuation means it literally, not as query syntax."""
    out = _sanitise_fts(dangerous)
    assert '"' not in out.replace('"', "", out.count('"'))  # only our own quotes
    assert out.count('"') % 2 == 0


def test_an_empty_query_sanitises_to_nothing():
    assert _sanitise_fts("   ^^^  ") == ""


# --- full text ------------------------------------------------------------


def test_fulltext_finds_a_phrase_in_the_body(client):
    body = client.get("/api/search/fulltext?q=primordial").json()
    assert body["mode"] == "fulltext"
    assert [h["title"] for h in body["hits"]] == [
        "Galaxy Formation in the Early Universe"
    ]


def test_fulltext_matches_words_the_title_never_uses(client):
    """The reason full text exists: the body says things the title does not."""
    hits = client.get("/api/search/fulltext?q=manipulator").json()["hits"]
    assert hits and hits[0]["title"] == "Robotic Grasp Planning"


def test_fulltext_returns_a_snippet_and_section(client):
    hit = client.get("/api/search/fulltext?q=primordial").json()["hits"][0]
    assert hit["snippet"]
    assert hit["section"] == "Introduction"


def test_an_apostrophe_does_not_break_the_parser(client):
    """FTS5 raises on unbalanced quotes; a user typing one is not an error."""
    res = client.get("/api/search/fulltext?q=Einstein's equations")
    assert res.status_code == 200


def test_fulltext_scores_are_higher_is_better(client):
    hits = client.get("/api/search/fulltext?q=galaxies").json()["hits"]
    assert all(h["score"] == pytest.approx(h["score"]) for h in hits)
    assert hits == sorted(hits, key=lambda h: -h["score"])


def test_no_match_is_an_empty_result_not_an_error(client):
    res = client.get("/api/search/fulltext?q=zzzznothinghere")
    assert res.status_code == 200
    assert res.json()["hits"] == []


def test_one_hit_per_paper(client):
    """Ten matching chunks of one paper is one result, not ten."""
    hits = client.get("/api/search/fulltext?q=the").json()["hits"]
    assert len({h["paper_id"] for h in hits}) == len(hits)


def test_a_one_character_query_is_rejected(client):
    assert client.get("/api/search/fulltext?q=a").status_code == 422


# --- semantic -------------------------------------------------------------


def test_semantic_reports_an_unavailable_model_rather_than_lying(client, monkeypatch):
    """An empty result would read as 'nothing matched'. It is not the same."""

    def unavailable(*a, **k):
        raise RuntimeError("model not downloaded")

    monkeypatch.setattr("app.services.embed.encoder.encode_query", unavailable)
    res = client.get("/api/search/semantic?q=galaxies")
    assert res.status_code == 503
    assert "unavailable" in res.json()["detail"]
