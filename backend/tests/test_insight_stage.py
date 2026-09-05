"""The INSIGHT stage: queued at embed time, drained after TAG, one row per work.

Driven the way the worker drives it against seeded rows. The model call is
faked at the function seam — the call itself is tested in test_insight.py —
so what is pinned here is the plumbing: when the job is queued, what the
handler reads, that a second run replaces rather than accumulates, and that
the reading reaches the paper endpoint and the routing table.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, get_settings
from app.core.db import Base, build_engine, get_db
from app.core.model_store import PRIVATE_OLLAMA
from app.main import create_app
from app.models import (
    PRIORITY,
    Job,
    JobKind,
    MarkdownDoc,
    Paper,
    PaperInsight,
    PaperMeta,
    PaperStatus,
)
from app.services.models import discovery
from app.workers import insight_handlers
from app.workers.embed_handlers import handle_embed
from app.workers.insight_handlers import handle_insight
from app.workers.queue import enqueue
from app.workers.runner import STAGE_ORDER

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from backfill_insights import works_needing_a_reading  # noqa: E402

OPENING = (
    "The subject of this essay is not the so-called liberty of the will, but "
    "civil, or social liberty: the nature and limits of the power which can be "
    "legitimately exercised by society over the individual. A question seldom "
    "stated, and hardly ever discussed, in general terms."
)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        library_dir=tmp_path / "lib",
        markdown_dir=tmp_path / "md",
        vectors_dir=tmp_path / "vec",
        models_dir=tmp_path / "models",
        db_path=tmp_path / "s.db",
        embed_dim=8,
        embed_model="test-embed",
    )


def seed(db, tmp_path, paper_id: int, *, with_markdown: bool = True) -> Paper:
    paper = Paper(
        id=paper_id,
        content_sha256=f"{paper_id:064d}",
        work_key=f"w{paper_id}",
        pdf_path=str(tmp_path / f"{paper_id}.md"),
        status=PaperStatus.EMBEDDED,
    )
    db.add(paper)
    db.add(
        PaperMeta(
            paper_id=paper_id,
            title="On Liberty",
            authors_json=json.dumps(["John Stuart Mill"]),
            year=1859,
            abstract="The subject of this essay is civil, or social liberty.",
        )
    )
    if with_markdown:
        md = tmp_path / f"{paper_id}.parsed.md"
        md.write_text(
            f"# On Liberty\n\n{OPENING}\n\n## Of the Liberty of Thought\n\n{OPENING}",
            encoding="utf-8",
        )
        db.add(
            MarkdownDoc(
                paper_id=paper_id,
                md_path=str(md),
                tier=0,
                parser="text",
                char_count=len(OPENING),
            )
        )
    db.flush()
    return paper


def _reading() -> dict:
    return {
        "genre": "essay-or-commentary",
        "question": "How far may society limit an individual's freedom?",
        "argument": "Society may only interfere to prevent harm to others.",
        "significance": "It draws the line liberal societies still argue over.",
        "claims": ["The only purpose of power over a member is self-protection."],
        "entities": [{"name": "John Stuart Mill", "kind": "person"}],
        "grounded": True,
    }


# --- the queue --------------------------------------------------------------------


def test_insight_is_the_last_stage_and_shares_the_llm_era():
    assert STAGE_ORDER[-1] is JobKind.INSIGHT
    assert PRIORITY[JobKind.INSIGHT] == PRIORITY[JobKind.TAG] + 1


def test_embedding_queues_the_reading_beside_tagging(
    db, tmp_path, settings, monkeypatch
):
    """Queued from the embed stage, not the tag stage, so a dead TAG job does
    not also lose the plain-English reading."""
    monkeypatch.setattr(
        "app.services.embed.encoder.encode_documents",
        lambda texts, settings=None: np.ones((len(texts), 8), dtype=np.float32),
    )
    seed(db, tmp_path, 1)
    job = enqueue(db, JobKind.EMBED, paper_id=1)
    db.commit()

    handle_embed(db, job, settings)

    kinds = {j.kind for j in db.scalars(select(Job).where(Job.paper_id == 1))}
    assert JobKind.TAG in kinds
    assert JobKind.INSIGHT in kinds


def test_the_stage_can_be_switched_off(db, tmp_path, settings, monkeypatch):
    monkeypatch.setattr(
        "app.services.embed.encoder.encode_documents",
        lambda texts, settings=None: np.ones((len(texts), 8), dtype=np.float32),
    )
    seed(db, tmp_path, 2)
    job = enqueue(db, JobKind.EMBED, paper_id=2)
    db.commit()

    handle_embed(db, job, settings.model_copy(update={"insight_enabled": False}))

    kinds = {j.kind for j in db.scalars(select(Job).where(Job.paper_id == 2))}
    assert JobKind.TAG in kinds
    assert JobKind.INSIGHT not in kinds


# --- the handler ------------------------------------------------------------------


def test_the_handler_reads_the_work_and_writes_one_row(
    db, tmp_path, settings, monkeypatch
):
    calls: list[dict] = []

    def fake_distill(**kwargs):
        calls.append(kwargs)
        return _reading()

    monkeypatch.setattr(insight_handlers, "distill_paper", fake_distill)
    seed(db, tmp_path, 3)
    job = enqueue(db, JobKind.INSIGHT, paper_id=3)
    db.commit()

    handle_insight(db, job, settings)
    handle_insight(db, job, settings)  # a second run replaces, never accumulates

    rows = db.scalars(select(PaperInsight).where(PaperInsight.paper_id == 3)).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.question.startswith("How far")
    assert json.loads(row.claims_json)[0].startswith("The only purpose")
    assert json.loads(row.entities_json) == [
        {"name": "John Stuart Mill", "kind": "person"}
    ]
    assert row.grounded is True
    # Provenance is the routed model, which with no pin is the configured one.
    assert row.model_id == settings.llm_model
    assert row.pipeline_version == settings.pipeline_version

    sent = calls[0]
    assert sent["title"] == "On Liberty"
    assert sent["authors"] == ["John Stuart Mill"]
    assert sent["year"] == 1859
    # The title heading rides along, exactly as it does for the tag stage.
    assert sent["headings"] == ["On Liberty", "Of the Liberty of Thought"]
    assert sent["excerpt"].startswith("The subject of this essay")
    assert sent["model"] == settings.llm_model


def test_the_handler_never_touches_the_paper_status(
    db, tmp_path, settings, monkeypatch
):
    """READY belongs to the tag stage; a failed or late reading is still a
    work on the map."""
    monkeypatch.setattr(insight_handlers, "distill_paper", lambda **kw: _reading())
    paper = seed(db, tmp_path, 4)
    paper.status = PaperStatus.READY
    db.flush()
    job = enqueue(db, JobKind.INSIGHT, paper_id=4)
    db.commit()

    handle_insight(db, job, settings)
    assert db.get(Paper, 4).status == PaperStatus.READY


def test_a_work_without_markdown_still_gets_a_reading(
    db, tmp_path, settings, monkeypatch
):
    calls: list[dict] = []
    monkeypatch.setattr(
        insight_handlers, "distill_paper", lambda **kw: calls.append(kw) or _reading()
    )
    seed(db, tmp_path, 5, with_markdown=False)
    job = enqueue(db, JobKind.INSIGHT, paper_id=5)
    db.commit()

    handle_insight(db, job, settings)
    assert calls[0]["excerpt"] is None
    assert calls[0]["headings"] == []
    assert db.get(PaperInsight, 5) is not None


# --- the API ------------------------------------------------------------------------


@pytest.fixture
def client(settings, monkeypatch):
    engine = build_engine(f"sqlite+pysqlite:///{settings.db_path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    # The Model Lab's two live seams, faked as test_models_api.py does.
    monkeypatch.setattr(
        "app.routers.models.discover",
        lambda settings=None: [
            discovery.DiscoveredModel("qwen3:8b", "configured", 5325, "Q4_K_M")
        ],
    )
    monkeypatch.setattr(PRIVATE_OLLAMA, "resident_models", lambda: [])

    app = create_app()
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as c:
        yield c, factory
    app.dependency_overrides.clear()
    engine.dispose()


def test_the_paper_endpoint_carries_the_reading(client, tmp_path):
    c, factory = client
    with factory() as s:
        seed(s, tmp_path, 7)
        s.add(
            PaperInsight(
                paper_id=7,
                genre="essay-or-commentary",
                question="How far may society limit freedom?",
                argument="Only to prevent harm to others.",
                significance="The line liberal societies still argue over.",
                claims_json=json.dumps(["Over himself the individual is sovereign."]),
                entities_json=json.dumps([{"name": "Mill", "kind": "person"}]),
                grounded=False,
                model_id="qwen3:8b",
                pipeline_version=1,
            )
        )
        s.commit()

    body = c.get("/api/papers/7").json()
    insight = body["insight"]
    assert insight["question"].startswith("How far")
    assert insight["claims"] == ["Over himself the individual is sovereign."]
    assert insight["entities"] == [{"name": "Mill", "kind": "person"}]
    assert insight["grounded"] is False
    assert insight["model_id"] == "qwen3:8b"


def test_a_paper_without_a_reading_says_so_with_null(client, tmp_path):
    c, factory = client
    with factory() as s:
        seed(s, tmp_path, 8)
        s.commit()
    assert c.get("/api/papers/8").json()["insight"] is None


def test_the_routing_table_lists_the_new_task(client):
    """/api/models resolves every TaskKind; a kind without a legacy field is
    a KeyError there, which is why the route is not optional."""
    c, _ = client
    body = c.get("/api/models").json()
    assert body["tasks"]["insight"] == "qwen3:8b"
    assert "insight" in body["pinnable"]


# --- the backfill -------------------------------------------------------------------


def test_backfill_selects_parsed_works_without_a_reading(db, tmp_path):
    seed(db, tmp_path, 10)
    seed(db, tmp_path, 11)
    seed(db, tmp_path, 12, with_markdown=False)  # not parsed: nothing to read
    db.add(
        PaperInsight(
            paper_id=11,
            genre="other",
            question="q",
            argument="a",
            significance="s",
            claims_json="[]",
            entities_json="[]",
            grounded=True,
            model_id="an-older-model",
            pipeline_version=1,
        )
    )
    db.flush()

    assert works_needing_a_reading(db) == [10]
    assert sorted(works_needing_a_reading(db, refresh_model="qwen3:8b")) == [10, 11]
    assert works_needing_a_reading(db, refresh_model="an-older-model") == [10]
