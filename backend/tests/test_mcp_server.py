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
from app.models.projection import Cluster, Projection, ProjectionRun
from app.services.project import paths
from app.workers.embed_handlers import open_store


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    """The trail router's kNN graph is cached per process, keyed on the store;
    every fixture here builds a new store, so no test may see another's."""
    monkeypatch.setattr(paths, "_CACHE", None)


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
        # A minimal active map: two one-paper regions, so the region tools
        # exercise the same rows the UI reads.
        run = ProjectionRun(
            model_id="test-embed",
            params_json="{}",
            n_fit=2,
            method="pca",
            is_active=True,
        )
        session.add(run)
        session.flush()
        gravity = Cluster(
            run_id=run.id,
            hdbscan_label=0,
            llm_label="Gravitational Astronomy",
            size=1,
            top_terms_json=json.dumps(["waves", "mergers"]),
        )
        sar = Cluster(
            run_id=run.id,
            hdbscan_label=1,
            llm_label="SAR Learning",
            size=1,
            top_terms_json=json.dumps(["sar", "classification"]),
        )
        session.add_all([gravity, sar])
        session.flush()
        session.add_all(
            [
                Projection(
                    run_id=run.id,
                    paper_id=1,
                    x=0.0,
                    y=0.0,
                    z=0.0,
                    cluster_id=gravity.id,
                    cluster_probability=0.9,
                ),
                Projection(
                    run_id=run.id,
                    paper_id=2,
                    x=1.0,
                    y=0.0,
                    z=0.0,
                    cluster_id=sar.id,
                    cluster_probability=0.8,
                ),
            ]
        )
        session.commit()

    # A real (tiny) vector store: similarity runs the actual cosine path.
    store = open_store(settings)
    store.add(1, np.array([1, 0, 0, 0, 0, 0, 0, 0], dtype=np.float32))
    store.add(2, np.array([0.9, 0.1, 0, 0, 0, 0, 0, 0], dtype=np.float32))
    store.flush()

    # WARMER is process-global and other tests drive it to FAILED or READY
    # (any TestClient lifespan warms for real on a machine that has the
    # model). Patch state, readiness and start so the semantic route
    # reports "warming" deterministically whatever ran before us.
    monkeypatch.setattr(warmup.WARMER, "start", lambda: None)
    monkeypatch.setattr(
        warmup.WARMER,
        "status",
        lambda: warmup.WarmupStatus(
            warmup.WarmupState.WARMING, 1.0, estimated_remaining=20.0
        ),
    )
    monkeypatch.setattr(type(warmup.WARMER), "is_ready", property(lambda self: False))

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


# --- regions and the overview ------------------------------------------------


async def test_regions_are_listed_and_inspectable_by_the_ids_given(mcp_server) -> None:
    """The id an agent gets from list_regions must be the id region_details
    accepts — the tools are a conversation, not two endpoints."""
    async with create_connected_server_and_client_session(
        mcp_server._mcp_server
    ) as session:
        listed = _payload(await session.call_tool("list_regions", {}))
        assert listed["papers_on_map"] == 2
        names = {r["name"] for r in listed["regions"]}
        assert names == {"Gravitational Astronomy", "SAR Learning"}

        sar_id = next(r["id"] for r in listed["regions"] if r["name"] == "SAR Learning")
        details = _payload(
            await session.call_tool("region_details", {"cluster_id": sar_id})
        )
    assert details["name"] == "SAR Learning"
    assert details["member_count"] == 1
    member = details["members"][0]
    assert member["paper_id"] == 2 and member["confidence"] == 0.8


async def test_an_unknown_region_points_at_list_regions(mcp_server) -> None:
    async with create_connected_server_and_client_session(
        mcp_server._mcp_server
    ) as session:
        result = await session.call_tool("region_details", {"cluster_id": 424242})
    assert result.isError
    assert "list_regions" in result.content[0].text


async def test_library_overview_surveys_the_map(mcp_server) -> None:
    async with create_connected_server_and_client_session(
        mcp_server._mcp_server
    ) as session:
        body = _payload(await session.call_tool("library_overview", {}))
    assert body["papers"] == 2
    assert body["papers_on_map"] == 2
    assert len(body["regions"]) == 2
    assert isinstance(body["tags"], list)


# --- trails, where to start, measured values ---------------------------------


async def test_a_trail_between_two_ids_walks_the_map(mcp_server) -> None:
    """Digits are ids, with or without a leading #; the two seeded works are
    neighbours, so the trail is one hop and names both regions in order."""
    async with create_connected_server_and_client_session(
        mcp_server._mcp_server
    ) as session:
        result = await session.call_tool(
            "find_semantic_path", {"from_concept": "#1", "to_concept": "2"}
        )
    body = _payload(result)
    assert [s["paper_id"] for s in body["stops"]] == [1, 2]
    assert body["stops"][0]["position"] == 1
    assert body["complete"] is True and body["hops"] == 1
    assert body["regions_crossed"] == ["Gravitational Astronomy", "SAR Learning"]
    assert "note" not in body


async def test_a_phrase_end_waits_for_the_encoder(mcp_server) -> None:
    async with create_connected_server_and_client_session(
        mcp_server._mcp_server
    ) as session:
        result = await session.call_tool(
            "find_semantic_path", {"from_concept": "waves", "to_concept": "radar"}
        )
    body = _payload(result)
    assert body["status"] == "warming"
    assert "stops" not in body


async def test_a_one_character_end_is_refused_with_the_rule(mcp_server) -> None:
    async with create_connected_server_and_client_session(
        mcp_server._mcp_server
    ) as session:
        result = await session.call_tool(
            "find_semantic_path", {"from_concept": "x", "to_concept": "2"}
        )
    assert result.isError
    assert "two or more characters" in result.content[0].text


async def test_curriculum_resolves_a_region_and_says_when_it_cannot_rank(
    mcp_server,
) -> None:
    """One work is not a reading order; the answer names the region it found
    and says why there is no entry point, instead of inventing one."""
    async with create_connected_server_and_client_session(
        mcp_server._mcp_server
    ) as session:
        result = await session.call_tool(
            "get_curriculum", {"topic_or_cluster": "gravitational astronomy"}
        )
    body = _payload(result)
    assert body["resolved_as"] == "region"
    assert body["region"]["name"] == "Gravitational Astronomy"
    assert body["considered"] == 1
    assert body["entry_point"] is None and body["reading_order"] == []
    assert "at least two" in body["note"]


async def test_curriculum_on_a_topic_waits_for_the_encoder(mcp_server) -> None:
    async with create_connected_server_and_client_session(
        mcp_server._mcp_server
    ) as session:
        result = await session.call_tool(
            "get_curriculum", {"topic_or_cluster": "black hole ringdown"}
        )
    body = _payload(result)
    assert body["status"] == "warming"
    assert "entry_point" not in body


async def test_quantities_with_no_criterion_list_the_kinds(mcp_server) -> None:
    async with create_connected_server_and_client_session(
        mcp_server._mcp_server
    ) as session:
        result = await session.call_tool("query_quantities", {})
    body = _payload(result)
    assert body["kinds"] == []
    assert "kind, a unit or a phrase" in body["note"]


# --- the api being down ------------------------------------------------------


@pytest.fixture
def down_server():
    """Every request is refused at the transport, as a stopped API would."""

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    return build_server(
        base_url="http://scinet.test",
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(refuse)),
    )


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("search_library", {"query": "x"}),
        ("get_paper", {"paper_id": 1}),
        ("read_paper", {"paper_id": 1}),
        ("similar_papers", {"paper_id": 1}),
        ("list_regions", {}),
        ("region_details", {"cluster_id": 1}),
        ("library_overview", {}),
        ("find_semantic_path", {"from_concept": "1", "to_concept": "2"}),
        ("query_quantities", {"quantity_kind": "length"}),
        ("get_curriculum", {"topic_or_cluster": "anything at all"}),
    ],
)
async def test_every_tool_says_how_to_start_the_api(down_server, tool, args) -> None:
    """An MCP tool's failure text is its whole UX: a bare ConnectError tells
    the agent nothing it can relay; naming the command gives the user a fix."""
    async with create_connected_server_and_client_session(
        down_server._mcp_server
    ) as session:
        result = await session.call_tool(tool, args)
    assert result.isError
    text = result.content[0].text
    assert "not answering" in text and "scinet-up" in text
