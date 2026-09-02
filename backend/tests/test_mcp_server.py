"""The MCP server against the real API, entirely in process.

No socket, no lifespan, no model: the tools' httpx factory is backed by
``httpx.ASGITransport`` wrapping ``create_app()`` with the same dependency
overrides the API tests use, and the MCP side runs over the SDK's in-memory
transport. What these tests pin is the *contract* an agent sees — small
trimmed outputs, a warming answer instead of an empty result, errors that
say what to do — because an MCP tool's failure text is its entire UX.
"""

from __future__ import annotations

import json

import httpx
import numpy as np
import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from sqlalchemy.orm import sessionmaker

from app.core import warmup
from app.core.config import Settings, get_settings
from app.core.db import Base, build_engine, get_db
from app.main import create_app
from app.mcp.server import build_server
from app.models.enums import PaperStatus
from app.models.paper import MarkdownDoc, Paper, PaperMeta
from app.workers.embed_handlers import open_store


@pytest.fixture
def api_app(tmp_path, monkeypatch):
    """The real FastAPI app on an isolated database, with two seeded papers.

    WARMER.start is a no-op so the semantic route reports "warming"
    deterministically instead of spawning a real model-loading thread.
    """
    settings = Settings(
        data_dir=tmp_path,
        library_dir=tmp_path / "library",
        markdown_dir=tmp_path / "markdown",
        vectors_dir=tmp_path / "vectors",
        models_dir=tmp_path / "models",
        db_path=tmp_path / "api.db",
        enrichment_enabled=False,
        embed_model="test-embed",
        embed_dim=8,
    )
    settings.vectors_dir.mkdir(parents=True, exist_ok=True)
    engine = build_engine(f"sqlite+pysqlite:///{settings.db_path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory() as session:
        for i, (title, year) in enumerate(
            [
                ("Gravitational Waves from Neutron Star Mergers", 2019),
                ("Deep Learning for SAR Image Classification", 2022),
            ],
            start=1,
        ):
            session.add(
                Paper(
                    id=i,
                    content_sha256=f"{i:064d}",
                    work_key=f"work-{i}",
                    pdf_path=str(tmp_path / f"{i}.pdf"),
                    status=PaperStatus.READY,
                )
            )
            session.add(
                PaperMeta(
                    paper_id=i,
                    title=title,
                    year=year,
                    abstract=f"An abstract about {title.lower()}.",
                    authors_json=json.dumps(["A. Author", "B. Author"]),
                )
            )
        # Paper 1 has parsed markdown on disk — a known 1,000-character text so
        # the read_paper windowing arithmetic is checkable to the character.
        md_path = tmp_path / "markdown" / "000" / "000001.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text("0123456789" * 100, encoding="utf-8")
        session.add(
            MarkdownDoc(paper_id=1, md_path=str(md_path), tier=0, parser="test")
        )
        session.commit()

    # A real (tiny) vector store: similarity runs the actual cosine path.
    store = open_store(settings)
    store.add(1, np.array([1, 0, 0, 0, 0, 0, 0, 0], dtype=np.float32))
    store.add(2, np.array([0.9, 0.1, 0, 0, 0, 0, 0, 0], dtype=np.float32))
    store.flush()

    monkeypatch.setattr(warmup.WARMER, "start", lambda: None)

    app = create_app()

    def override_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    yield app
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
def mcp_server(api_app):
    return build_server(
        base_url="http://scinet.test",
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api_app), base_url="http://scinet.test"
        ),
    )


def _payload(result) -> dict:
    assert not result.isError, result.content[0].text
    return json.loads(result.content[0].text)


# --- the contract ------------------------------------------------------------


async def test_the_tools_are_discoverable(mcp_server) -> None:
    async with create_connected_server_and_client_session(
        mcp_server._mcp_server
    ) as session:
        tools = {t.name for t in (await session.list_tools()).tools}
    assert {"search_library", "get_paper"} <= tools


async def test_title_search_finds_the_seeded_paper(mcp_server) -> None:
    async with create_connected_server_and_client_session(
        mcp_server._mcp_server
    ) as session:
        result = await session.call_tool(
            "search_library", {"query": "gravitational", "mode": "title"}
        )
    body = _payload(result)
    assert body["mode"] == "title"
    assert [h["paper_id"] for h in body["hits"]] == [1]
    assert body["hits"][0]["year"] == 2019


async def test_semantic_search_reports_warming_instead_of_empty(mcp_server) -> None:
    """An empty hit list would read as "nothing matched" — a false statement
    while the engine is still loading. The agent must be told to retry."""
    async with create_connected_server_and_client_session(
        mcp_server._mcp_server
    ) as session:
        result = await session.call_tool(
            "search_library", {"query": "anything", "mode": "semantic"}
        )
    body = _payload(result)
    assert body["status"] == "warming"
    assert "hits" not in body


async def test_get_paper_returns_the_essentials_and_no_bookkeeping(mcp_server) -> None:
    async with create_connected_server_and_client_session(
        mcp_server._mcp_server
    ) as session:
        result = await session.call_tool("get_paper", {"paper_id": 2})
    body = _payload(result)
    assert body["title"] == "Deep Learning for SAR Image Classification"
    assert body["authors"] == ["A. Author", "B. Author"]
    assert body["abstract"].startswith("An abstract")
    # Bookkeeping stays out of an agent's context.
    for noise in ("work_key", "pdf_bytes", "added_at", "parse"):
        assert noise not in body


async def test_an_unknown_paper_is_a_clear_error(mcp_server) -> None:
    async with create_connected_server_and_client_session(
        mcp_server._mcp_server
    ) as session:
        result = await session.call_tool("get_paper", {"paper_id": 999})
    assert result.isError
    assert "no paper 999" in result.content[0].text


async def test_an_invalid_mode_is_refused_with_the_valid_ones(mcp_server) -> None:
    async with create_connected_server_and_client_session(
        mcp_server._mcp_server
    ) as session:
        result = await session.call_tool(
            "search_library", {"query": "x", "mode": "vibes"}
        )
    assert result.isError
    assert "semantic" in result.content[0].text


# --- reading -----------------------------------------------------------------


async def test_read_paper_pages_by_window(mcp_server) -> None:
    async with create_connected_server_and_client_session(
        mcp_server._mcp_server
    ) as session:
        first = _payload(
            await session.call_tool(
                "read_paper", {"paper_id": 1, "offset": 0, "window": 300}
            )
        )
        last = _payload(
            await session.call_tool(
                "read_paper", {"paper_id": 1, "offset": 900, "window": 300}
            )
        )
    assert first["total_chars"] == 1000
    assert len(first["text"]) == 300 and first["next_offset"] == 300
    assert len(last["text"]) == 100 and last["next_offset"] is None


async def test_reading_an_unparsed_paper_says_so(mcp_server) -> None:
    async with create_connected_server_and_client_session(
        mcp_server._mcp_server
    ) as session:
        result = await session.call_tool("read_paper", {"paper_id": 2})
    assert result.isError
    assert "not been parsed" in result.content[0].text


# --- similarity --------------------------------------------------------------


async def test_similar_papers_ranks_by_real_cosine(mcp_server) -> None:
    async with create_connected_server_and_client_session(
        mcp_server._mcp_server
    ) as session:
        body = _payload(await session.call_tool("similar_papers", {"paper_id": 1}))
    assert [s["paper_id"] for s in body["similar"]] == [2]
    hit = body["similar"][0]
    assert hit["title"] == "Deep Learning for SAR Image Classification"
    assert hit["similarity"] > 0.9


async def test_similarity_without_an_embedding_is_a_clear_error(mcp_server) -> None:
    async with create_connected_server_and_client_session(
        mcp_server._mcp_server
    ) as session:
        result = await session.call_tool("similar_papers", {"paper_id": 999})
    assert result.isError
    assert "no embedding" in result.content[0].text
