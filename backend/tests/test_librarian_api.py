"""The librarian's stream, end to end with fakes at the two Ollama seams.

The planning call and the token stream are monkeypatched (scripted plans;
scripted tokens that include a citation split across token boundaries and a
citation the tools never earned); the tools, the lease, and the citation
gate run for real. What the client receives is the contract under test:
tool_call frames as retrieval happens, map directives derived only from
evidence, validated prose, and a done frame that names what was stripped.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core import warmup
from app.core.config import Settings, get_settings
from app.core.db import Base, build_engine, get_db
from app.main import create_app
from app.models import Paper, PaperMeta
from app.models.enums import PaperStatus
from app.routers import librarian as librarian_router
from app.workers import lease

FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, section, content='chunks', content_rowid='id',
    tokenize='porter unicode61')
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    settings = Settings(
        data_dir=tmp_path,
        library_dir=tmp_path / "library",
        markdown_dir=tmp_path / "markdown",
        vectors_dir=tmp_path / "vectors",
        models_dir=tmp_path / "models",
        db_path=tmp_path / "l.db",
        embed_model="test-embed",
        embed_dim=8,
    )
    engine = build_engine(f"sqlite+pysqlite:///{settings.db_path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory() as session:
        session.execute(text(FTS_DDL))
        session.add(
            Paper(
                id=7,
                content_sha256="7" * 64,
                work_key="w7",
                pdf_path=str(tmp_path / "7.pdf"),
                status=PaperStatus.READY,
            )
        )
        session.add(PaperMeta(paper_id=7, title="Kalman Filtering in Orbit"))
        session.flush()
        session.execute(
            text(
                "INSERT INTO chunks (id, paper_id, ord, section, text) VALUES "
                "(1, 7, 0, 'Intro', 'The Kalman filter estimates state.')"
            )
        )
        session.execute(text("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')"))
        session.commit()

    warmup.WARMER.reset()
    monkeypatch.setattr(warmup.WARMER, "start", lambda: None)

    plans = iter(
        [{"action": "search_fulltext", "query": "kalman"}, {"action": "answer"}]
    )

    async def fake_plan(model, prompt, schema, settings):
        return next(plans, {"action": "answer"})

    async def fake_tokens(model, prompt, settings):
        # A citation split across tokens, and one the tools never earned.
        for token in ("Covered in [#", "7]. Nothing supports [#99].", " Done."):
            yield token

    monkeypatch.setattr(librarian_router, "_ollama_json", fake_plan)
    monkeypatch.setattr(librarian_router, "_stream_answer_tokens", fake_tokens)
    monkeypatch.setattr(
        librarian_router, "resolve", lambda task, settings, db: "fake-llm:1b"
    )

    app = create_app()

    def override_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as c:
        c.scinet_factory = factory
        yield c
    app.dependency_overrides.clear()
    engine.dispose()


def collect_frames(client) -> list[tuple[str, dict]]:
    frames: list[tuple[str, dict]] = []
    with client.stream(
        "POST", "/api/librarian/ask", json={"question": "what about kalman?"}
    ) as response:
        assert response.status_code == 200
        kind = None
        for line in response.iter_lines():
            if line.startswith("event: "):
                kind = line.removeprefix("event: ")
            elif line.startswith("data: ") and kind:
                frames.append((kind, json.loads(line.removeprefix("data: "))))
                kind = None
    return frames


def test_the_stream_carries_the_whole_conversation(client) -> None:
    frames = collect_frames(client)
    kinds = [k for k, _ in frames]

    assert "tool_call" in kinds and "done" in kinds
    tool = next(p for k, p in frames if k == "tool_call")
    assert tool["action"] == "search_fulltext"

    directives = [p for k, p in frames if k == "map_directive"]
    assert {d["type"] for d in directives} == {"highlight", "fly_to", "trail"}
    assert directives[0]["paper_ids"] == [7], "directives carry only evidence"

    answer = "".join(p["text"] for k, p in frames if k == "answer_token")
    assert "[#7]" in answer, "the earned citation survives a token split"
    assert "#99" not in answer, "the unearned citation is stripped"

    done = next(p for k, p in frames if k == "done")
    assert done["cited"] == [7] and done["dropped"] == [99]


def test_the_lease_is_taken_and_released_around_the_turn(client) -> None:
    collect_frames(client)
    with client.scinet_factory() as session:
        assert lease.active(session) is None, "released in finally"
        held_before = lease.current(session)
        assert held_before is not None, "a lease row was written during the turn"


def test_an_empty_question_is_a_400(client) -> None:
    assert client.post("/api/librarian/ask", json={"question": "  "}).status_code == 400
