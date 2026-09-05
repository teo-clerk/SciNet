"""The three research tools against a library big enough to mean something.

The two-paper fixture in test_mcp_server pins each tool's contract at the
edges — warming, an unrankable region, the kinds list. This one seeds two
regions joined by one bridge paper, a plain-English reading on one work, and
a few measured values, so a trail has stops to cross, a curriculum has works
to order, and a quantity query has sentences to return.
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
from app.mcp.server import _region_matches, build_server
from app.models import PaperInsight, Quantity, QuantityStatus
from app.models.enums import PaperStatus
from app.models.paper import Paper, PaperMeta
from app.models.projection import Cluster, Projection, ProjectionRun
from app.services.embed.store import VectorStore
from app.services.project import paths
from app.workers.embed_handlers import store_slug

DIM = 8


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(paths, "_CACHE", None)


def unit(vector) -> np.ndarray:
    vector = np.asarray(vector, dtype=np.float64)
    return vector / np.linalg.norm(vector)


def island(along: int, n: int, seed: int, spread: float = 0.05) -> np.ndarray:
    rng = np.random.default_rng(seed)
    base = np.zeros(DIM)
    base[along] = 1.0
    return np.vstack([unit(base + rng.standard_normal(DIM) * spread) for _ in range(n)])


@pytest.fixture
def library(tmp_path, monkeypatch):
    """Alpha (5) and Beta (5), nearly orthogonal, joined by one bridge paper.

    Returns (app, ids) where ids = {"alpha": [...], "beta": [...], "bridge": id}.
    The encoder is pinned *warming*; a test that needs a phrase to embed
    patches it ready and gives encode_query a vector of its own.
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
        embed_dim=DIM,
    )
    settings.vectors_dir.mkdir(parents=True, exist_ok=True)
    engine = build_engine(f"sqlite+pysqlite:///{settings.db_path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    store = VectorStore(
        settings.vectors_dir / f"doc_vectors__{store_slug(settings.embed_model)}",
        dim=DIM,
        model_id=settings.embed_model,
    )

    alpha, beta = island(0, 5, seed=11), island(1, 5, seed=12)
    bridge = unit([1.0, 1.0, 0, 0, 0, 0, 0, 0])
    rows = (
        [("Alpha", v) for v in alpha]
        + [("Beta", v) for v in beta]
        + [("Bridge", bridge)]
    )
    ids: dict[str, list[int]] = {"Alpha": [], "Beta": [], "Bridge": []}

    with factory() as s:
        run = ProjectionRun(
            model_id="test-embed",
            params_json="{}",
            n_fit=11,
            method="pca",
            is_active=True,
        )
        s.add(run)
        s.flush()
        clusters = {
            name: Cluster(
                run_id=run.id,
                hdbscan_label=i,
                size=5,
                llm_label=f"{name} Region",
                top_terms_json=json.dumps([name.lower()]),
            )
            for i, name in enumerate(("Alpha", "Beta"))
        }
        s.add_all(clusters.values())
        s.flush()
        for i, (region, vector) in enumerate(rows, start=1):
            paper = Paper(
                id=i,
                content_sha256=f"{i:064d}",
                work_key=f"w{i}",
                pdf_path=str(tmp_path / f"{i}.pdf"),
                status=PaperStatus.READY,
            )
            s.add(paper)
            s.flush()
            s.add(
                PaperMeta(
                    paper_id=i,
                    title=f"{region} work number {i}",
                    year=2000 + i,
                    abstract=f"On the {region.lower()} question.",
                )
            )
            cluster = clusters.get(region)
            s.add(
                Projection(
                    run_id=run.id,
                    paper_id=i,
                    x=0.0,
                    y=0.0,
                    z=0.0,
                    cluster_id=cluster.id if cluster else None,
                    cluster_probability=0.9 if cluster else None,
                )
            )
            store.add(i, vector.astype(np.float32))
            ids[region].append(i)
        bridge_id = ids["Bridge"][0]
        s.add(
            PaperInsight(
                paper_id=bridge_id,
                genre="research-paper",
                question="What joins the alpha and beta traditions?",
                argument="They share a method.",
                significance="It lets a reader cross.",
                claims_json="[]",
                entities_json="[]",
                grounded=True,
                model_id="test-llm",
                pipeline_version=1,
            )
        )
        first_alpha, first_beta = ids["Alpha"][0], ids["Beta"][0]
        s.add_all(
            [
                Quantity(
                    paper_id=first_alpha,
                    chunk_ord=0,
                    section="Results",
                    quantity_kind="frequency",
                    value_si=40.0,
                    unit_si="hertz",
                    value_original="40",
                    unit_original="Hz",
                    context_sentence="Oscillations peaked at 40 Hz during the task.",
                    confidence=0.95,
                    status=QuantityStatus.AUTO,
                ),
                Quantity(
                    paper_id=first_beta,
                    chunk_ord=0,
                    section="Methods",
                    quantity_kind="frequency",
                    value_si=2000.0,
                    unit_si="hertz",
                    value_original="2",
                    unit_original="kHz",
                    context_sentence="Sampling ran at 2 kHz throughout.",
                    confidence=0.95,
                    status=QuantityStatus.AUTO,
                ),
                Quantity(
                    paper_id=first_beta,
                    chunk_ord=1,
                    section="Methods",
                    quantity_kind="frequency",
                    value_si=10.0,
                    unit_si="hertz",
                    value_original="10",
                    unit_original="Hz",
                    context_sentence="A rejected 10 Hz reading.",
                    confidence=0.4,
                    status=QuantityStatus.REJECTED,
                ),
            ]
        )
        s.commit()
    store.flush()

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
    yield app, {"alpha": ids["Alpha"], "beta": ids["Beta"], "bridge": bridge_id}, store
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
def mcp(library):
    app, ids, store = library
    server = build_server(
        base_url="http://scinet.test",
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://scinet.test"
        ),
    )
    return server, ids, store


async def call(server, tool: str, args: dict) -> dict:
    async with create_connected_server_and_client_session(
        server._mcp_server
    ) as session:
        result = await session.call_tool(tool, args)
    assert not result.isError, result.content[0].text
    return json.loads(result.content[0].text)


# --- trails ------------------------------------------------------------------


async def test_a_trail_crosses_the_bridge_and_names_the_regions(mcp) -> None:
    server, ids, _ = mcp
    body = await call(
        server,
        "find_semantic_path",
        {"from_concept": str(ids["alpha"][0]), "to_concept": str(ids["beta"][0])},
    )

    stop_ids = [s["paper_id"] for s in body["stops"]]
    assert stop_ids[0] == ids["alpha"][0] and stop_ids[-1] == ids["beta"][0]
    assert ids["bridge"] in stop_ids[1:-1], "the only route runs through the bridge"
    assert body["complete"] is True and body["hops"] == len(stop_ids) - 1
    assert body["regions_crossed"] == ["Alpha Region", "Beta Region"]
    positions = [s["position"] for s in body["stops"]]
    assert positions == list(range(1, len(stop_ids) + 1))


async def test_a_stop_with_a_reading_carries_its_central_question(mcp) -> None:
    server, ids, _ = mcp
    body = await call(
        server,
        "find_semantic_path",
        {"from_concept": str(ids["alpha"][1]), "to_concept": str(ids["beta"][2])},
    )
    by_id = {s["paper_id"]: s for s in body["stops"]}
    bridge = by_id[ids["bridge"]]
    assert bridge["core_question"] == "What joins the alpha and beta traditions?"
    assert bridge["region"] is None  # the bridge sits in no region
    # every stop but the last says how close the next one is
    assert all(s["similarity_to_next"] is not None for s in body["stops"][:-1])
    assert body["stops"][-1]["similarity_to_next"] is None


# --- measured values ---------------------------------------------------------


async def test_quantities_by_kind_and_range_come_with_their_sentences(mcp) -> None:
    server, ids, _ = mcp
    body = await call(
        server,
        "query_quantities",
        {"quantity_kind": "frequency", "min_val": 1, "max_val": 100},
    )
    assert body["count"] == 1 and body["papers"] == 1 and body["returned"] == 1
    [row] = body["rows"]
    assert row["paper_id"] == ids["alpha"][0]
    assert row["paper_title"] == f"Alpha work number {ids['alpha'][0]}"
    assert row["sentence"] == "Oscillations peaked at 40 Hz during the task."
    assert row["unit_si"] == "hertz" and row["value_original"] == "40"


async def test_quantities_by_phrase_and_by_unit_token(mcp) -> None:
    server, _, _ = mcp
    phrase = await call(server, "query_quantities", {"query": "sampling"})
    assert [r["value_original"] for r in phrase["rows"]] == ["2"]

    token = await call(server, "query_quantities", {"unit": "kHz"})
    assert token["count"] == 1 and token["rows"][0]["unit_original"] == "kHz"

    everything = await call(server, "query_quantities", {"quantity_kind": "frequency"})
    assert everything["count"] == 2, "the rejected reading never appears"


async def test_the_kinds_list_reports_only_trusted_rows(mcp) -> None:
    server, _, _ = mcp
    body = await call(server, "query_quantities", {})
    [kind] = body["kinds"]
    assert kind["kind"] == "frequency" and kind["count"] == 2
    assert kind["min_value"] == 40.0 and kind["max_value"] == 2000.0


# --- where to start ----------------------------------------------------------


async def test_curriculum_for_a_region_ranks_and_orders_its_works(mcp) -> None:
    server, ids, _ = mcp
    body = await call(server, "get_curriculum", {"topic_or_cluster": "alpha region"})

    assert body["resolved_as"] == "region"
    assert body["region"]["name"] == "Alpha Region" and body["region"]["size"] == 5
    assert body["considered"] == 5
    entry = body["entry_point"]
    assert entry is not None and entry["paper_id"] in ids["alpha"]
    assert entry["reasons"], "every entry point says why"
    order = body["reading_order"]
    assert [o["position"] for o in order] == list(range(1, len(order) + 1))
    assert all(o["title"].startswith("Alpha work") for o in order)
    assert "note" not in body


async def test_curriculum_for_a_topic_ranks_the_semantic_hits(mcp, monkeypatch) -> None:
    """No region is called "beta things", so the phrase is a topic: it is
    embedded, searched, and the hits are ranked through the entry-point
    endpoint — the one POST the client makes."""
    server, ids, store = mcp
    monkeypatch.setattr(type(warmup.WARMER), "is_ready", property(lambda self: True))
    monkeypatch.setattr(
        warmup.WARMER,
        "status",
        lambda: warmup.WarmupStatus(warmup.WarmupState.READY, 26.0),
    )
    target = store.get(ids["beta"][1])
    monkeypatch.setattr(
        "app.services.embed.encoder.encode_query", lambda text, **_: target
    )

    body = await call(server, "get_curriculum", {"topic_or_cluster": "beta things"})

    assert body["resolved_as"] == "topic" and body["topic"] == "beta things"
    assert body["region"] is None
    assert body["considered"] >= 2
    assert body["entry_point"] is not None
    assert all(o["title"] for o in body["reading_order"])


def test_region_matching_is_by_whole_words() -> None:
    assert _region_matches("active inference", "Active Inference Theory")
    assert _region_matches("Active Inference Theory, please", "Active Inference Theory")
    assert not _region_matches("ai", "Network Physiology And Brain Dynamics")
    assert not _region_matches("free energy", "Active Inference Theory")
    assert not _region_matches("anything", None)
