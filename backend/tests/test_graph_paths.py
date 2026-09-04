"""Idea trails.

A trail's claim — "these papers lead from here to there, one small step at a
time" — is only worth making if each step is a real neighbour and the route
does not wander. The tests below pin the two design choices that keep it
honest: the hop penalty, and saying so when the corpus has no path.

Named ``test_graph_paths`` because ``test_paths`` already covers the library
containment guard in ``app.core.paths``.
"""

from __future__ import annotations

import os

import numpy as np
import pytest
from scipy import sparse

from app.core.config import Settings
from app.models import (
    Cluster,
    Paper,
    PaperMeta,
    PaperStatus,
    Projection,
    ProjectionRun,
)
from app.services.embed.store import VectorStore
from app.services.project import paths
from app.services.project.paths import (
    HOP_PENALTY,
    MAX_STOPS,
    MIN_SIMILARITY,
    KnnGraph,
    build_graph,
    cache_key,
    find_path,
    graph_for,
    greedy_walk,
    shortest_path,
    thin,
)
from app.services.project.scaling import neighbours_for
from app.workers.embed_handlers import store_slug
from tests.test_graph_api import env  # noqa: F401 - fixture

DIM = 8


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    """The routing graph is cached per process; tests must not see each other's."""
    monkeypatch.setattr(paths, "_CACHE", None)


def unit(vector) -> np.ndarray:
    vector = np.asarray(vector, dtype=np.float64)
    return vector / np.linalg.norm(vector)


def cone(n: int, seed: int = 0) -> np.ndarray:
    """Vectors sharing a large common component, as document embeddings do."""
    rng = np.random.default_rng(seed)
    shared = np.abs(rng.standard_normal(DIM)) + 1.0
    return (shared + rng.standard_normal((n, DIM)) * 0.3).astype(np.float32)


def island(along: int, n: int, seed: int, spread: float = 0.1) -> np.ndarray:
    """n vectors tightly around one axis; two islands on different axes are
    nearly orthogonal, so nothing links them above the similarity floor."""
    rng = np.random.default_rng(seed)
    base = np.zeros(DIM)
    base[along] = 1.0
    return np.vstack([unit(base + rng.standard_normal(DIM) * spread) for _ in range(n)])


# --- building ---------------------------------------------------------------


@pytest.mark.parametrize("n", [10, 64, 200])
def test_the_default_k_is_the_house_neighbourhood_law(n):
    """The on-disk skeleton's k=2 is for drawing; routing needs the law."""
    matrix = cone(n)
    ids = list(range(n))

    by_default = build_graph(matrix, ids)
    explicit = build_graph(matrix, ids, k=neighbours_for(n))

    assert by_default.csr.nnz == explicit.csr.nnz
    assert np.allclose(by_default.csr.toarray(), explicit.csr.toarray())


def test_edge_weights_are_cosine_distance_plus_the_hop_penalty():
    a, b = unit([1, 0.4, 0, 0, 0, 0, 0, 0]), unit([1, 0, 0.4, 0, 0, 0, 0, 0])
    c = unit([1, 0, 0, 0.4, 0, 0, 0, 0])
    graph = build_graph(np.vstack([a, b, c]), [10, 20, 30])

    expected = (1.0 - float(a @ b)) + HOP_PENALTY
    assert graph.csr[0, 1] == pytest.approx(expected, abs=1e-5)
    assert graph.csr[1, 0] == pytest.approx(expected, abs=1e-5)  # symmetric


def test_edges_below_the_similarity_floor_are_not_edges():
    """Two islands: the least-dissimilar paper across the gap is still unrelated."""
    matrix = np.vstack([island(0, 5, seed=1), island(1, 5, seed=2)])
    graph = build_graph(matrix, list(range(10)))

    dense = graph.csr.toarray()
    assert not dense[:5, 5:].any()
    assert dense[:5, :5].any()


def test_an_edge_exists_if_either_end_chose_the_other():
    """A fringe paper picks the centre; the centre does not pick it back."""
    hub = island(0, 6, seed=4, spread=0.05)
    fringe = unit(np.array([1.0, 0.9, 0, 0, 0, 0, 0, 0]))  # cos ~0.74 to the hub
    graph = build_graph(np.vstack([hub, fringe]), list(range(7)), k=2)

    dense = graph.csr.toarray()
    assert dense[6].any()
    assert np.allclose(dense, dense.T)


# --- routing ----------------------------------------------------------------


def hand_built(pure: dict[tuple[int, int], float], penalty: float) -> KnnGraph:
    """A graph from named pure cosine distances, so the weights are exact."""
    n = 1 + max(max(pair) for pair in pure)
    dense = np.zeros((n, n))
    for (a, b), distance in pure.items():
        dense[a, b] = dense[b, a] = distance + penalty
    return KnnGraph(
        paper_ids=tuple(range(n)),
        index_of={i: i for i in range(n)},
        csr=sparse.csr_matrix(dense),
        unit=np.eye(n, DIM),
        key=(),
    )


DETOUR = {(0, 3): 0.30, (0, 1): 0.095, (1, 2): 0.095, (2, 3): 0.095}


def test_the_hop_penalty_prefers_fewer_hops_when_distances_are_close():
    """Three hops of 0.095 beat one of 0.30 in pure cosine distance (0.285
    against 0.30) — a chain nobody reads. The penalty tips it the other way."""
    assert shortest_path(hand_built(DETOUR, penalty=0.0), 0, 3) == [0, 1, 2, 3]
    assert shortest_path(hand_built(DETOUR, penalty=HOP_PENALTY), 0, 3) == [0, 3]


def test_a_genuinely_intermediate_paper_is_still_visited():
    """The penalty must not flatten every trail into a single leap."""
    far = {(0, 2): 0.60, (0, 1): 0.15, (1, 2): 0.15}
    assert shortest_path(hand_built(far, penalty=HOP_PENALTY), 0, 2) == [0, 1, 2]


def test_two_islands_fall_back_to_a_greedy_walk():
    matrix = np.vstack([island(0, 6, seed=1), island(1, 6, seed=2)])
    ids = list(range(100, 112))
    graph = build_graph(matrix, ids)

    result = find_path(graph, 100, 111)

    assert result.complete is False
    assert result.stops[0] == 100
    assert result.stops[-1] == 111
    assert result.hops == len(result.stops) - 1
    assert shortest_path(graph, 100, 111) is None


def test_a_connected_pair_is_complete():
    matrix = cone(30)
    graph = build_graph(matrix, list(range(30)))
    result = find_path(graph, 0, 29)
    assert result.complete is True
    assert result.stops[0] == 0 and result.stops[-1] == 29
    # Every consecutive pair is a real edge.
    for a, b in zip(result.stops, result.stops[1:], strict=False):
        assert graph.csr[graph.index_of[a], graph.index_of[b]] > 0


def test_the_greedy_walk_stops_when_nothing_improves():
    """The source already leans toward the target more than any neighbour;
    there is nowhere to climb, so the walk is the jump alone."""
    src = unit([1.0, 0.3, 0, 0, 0, 0, 0, 0])
    neighbours = [unit([1.0, 0, 0.1 * (i + 1), 0, 0, 0, 0, 0]) for i in range(3)]
    dst = unit([0, 1.0, 0, 0, 0, 0, 0, 0])
    graph = build_graph(np.vstack([src, *neighbours, dst]), [1, 2, 3, 4, 5])

    assert greedy_walk(graph, 1, 5) == [1, 5]


def test_the_greedy_walk_climbs_monotonically_toward_the_target():
    matrix = np.vstack([island(0, 8, seed=7, spread=0.3), island(1, 4, seed=8)])
    ids = list(range(12))
    graph = build_graph(matrix, ids)
    target = graph.unit[graph.index_of[11]]

    walk = greedy_walk(graph, 0, 11)

    climb = [float(graph.unit[graph.index_of[pid]] @ target) for pid in walk[:-1]]
    assert climb == sorted(climb)
    assert len(set(walk)) == len(walk)


def test_the_same_paper_at_both_ends_is_a_single_stop():
    graph = build_graph(cone(5), [1, 2, 3, 4, 5])
    assert find_path(graph, 3, 3) == paths.PathResult(stops=(3,), complete=True, hops=0)


# --- thinning ---------------------------------------------------------------


def test_thinning_keeps_both_endpoints_and_respects_the_limit():
    stops = list(range(100, 140))
    thinned = thin(stops, MAX_STOPS)

    assert len(thinned) == MAX_STOPS
    assert thinned[0] == 100 and thinned[-1] == 139
    assert thinned == sorted(thinned)  # order preserved


def test_a_short_trail_is_not_thinned():
    assert thin([1, 2, 3]) == [1, 2, 3]
    assert thin(list(range(MAX_STOPS))) == list(range(MAX_STOPS))


# --- the cache --------------------------------------------------------------


@pytest.fixture
def store_and_settings(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        library_dir=tmp_path / "lib",
        markdown_dir=tmp_path / "md",
        vectors_dir=tmp_path / "vec",
        models_dir=tmp_path / "models",
        db_path=tmp_path / "c.db",
        embed_dim=DIM,
        embed_model="test-embed",
    )
    store = VectorStore(
        settings.vectors_dir / "doc_vectors", dim=DIM, model_id="test-embed"
    )
    for pid, row in enumerate(cone(6), start=1):
        store.add(pid, row)
    return store, settings


def rewrite_later(store: VectorStore, paper_id: int) -> None:
    """Re-embed one paper as if a millisecond had passed.

    The header is rewritten on every write, so the modification time moves;
    the explicit bump stands in for the clock on filesystems whose timestamps
    are coarser than a test is fast.
    """
    before = store.meta_path.stat().st_mtime_ns
    store.add(paper_id, cone(1, seed=99)[0])
    later = max(store.meta_path.stat().st_mtime_ns, before + 1_000_000)
    os.utime(store.meta_path, ns=(later, later))


def test_the_cache_key_changes_when_a_vector_is_rewritten(store_and_settings):
    """The count alone misses a re-embedded paper; the header's mtime does not."""
    store, settings = store_and_settings
    before = cache_key(store, settings)

    rewrite_later(store, paper_id=3)

    after = cache_key(store, settings)
    assert store.count == 6
    assert after != before
    assert after[:2] == before[:2]


def test_an_unchanged_store_returns_the_cached_graph(store_and_settings):
    store, settings = store_and_settings
    assert graph_for(store, settings) is graph_for(store, settings)


def test_a_rewritten_store_rebuilds_the_graph(store_and_settings):
    store, settings = store_and_settings
    first = graph_for(store, settings)
    rewrite_later(store, paper_id=3)
    second = graph_for(store, settings)
    assert second is not first
    assert second.key != first.key


# --- through the API --------------------------------------------------------


def seed_bridge(factory, settings):
    """Two regions joined by one paper that sits between them.

    Returns ``(alpha_ids, beta_ids, bridge_id)``. Alpha and Beta are nearly
    orthogonal, so the only route between them is through the bridge.
    """
    store = VectorStore(
        settings.vectors_dir / f"doc_vectors__{store_slug(settings.embed_model)}",
        dim=settings.embed_dim,
        model_id=settings.embed_model,
    )
    alpha, beta = island(0, 5, seed=11, spread=0.05), island(1, 5, seed=12, spread=0.05)
    bridge = unit([1.0, 1.0, 0, 0, 0, 0, 0, 0])
    rows = (
        [("Alpha", v) for v in alpha]
        + [("Beta", v) for v in beta]
        + [("Bridge", bridge)]
    )

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
            name: Cluster(run_id=run.id, hdbscan_label=i, size=5, llm_label=name)
            for i, name in enumerate(("Alpha", "Beta"))
        }
        s.add_all(clusters.values())
        s.flush()

        ids: dict[str, list[int]] = {"Alpha": [], "Beta": [], "Bridge": []}
        for i, (region, vector) in enumerate(rows):
            paper = Paper(
                content_sha256=f"{i:064d}",
                work_key=f"w{i}",
                pdf_path=f"/tmp/{i}.pdf",
                status=PaperStatus.READY,
                pipeline_version=1,
            )
            s.add(paper)
            s.flush()
            s.add(PaperMeta(paper_id=paper.id, title=f"{region} {i}", year=2000 + i))
            cluster = clusters.get(region)
            s.add(
                Projection(
                    run_id=run.id,
                    paper_id=paper.id,
                    x=0.0,
                    y=0.0,
                    z=0.0,
                    cluster_id=cluster.id if cluster else None,
                )
            )
            store.add(paper.id, vector.astype(np.float32))
            ids[region].append(paper.id)
        s.commit()
    store.flush()
    return ids["Alpha"], ids["Beta"], ids["Bridge"][0]


def test_a_trail_by_ids_crosses_the_planted_bridge(env):  # noqa: F811
    client, factory, settings = env
    alpha, beta, bridge = seed_bridge(factory, settings)

    res = client.get(f"/api/graph/path?from={alpha[0]}&to={beta[0]}")

    assert res.status_code == 200
    body = res.json()
    stops = [s["paper_id"] for s in body["stops"]]
    assert body["complete"] is True
    assert body["thinned"] is False
    assert stops[0] == alpha[0] and stops[-1] == beta[0]
    assert bridge in stops
    assert body["hops"] == len(stops) - 1
    assert body["from"] == {"paper_id": alpha[0], "anchored_from_text": None}

    assert body["stops"][0]["cluster_label"] == "Alpha"
    assert body["stops"][-1]["cluster_label"] == "Beta"
    assert body["stops"][-1]["similarity_to_next"] is None
    assert all(s["similarity_to_next"] is not None for s in body["stops"][:-1])
    # Each step is a real neighbour; the leap the reader should notice is the
    # one onto and off the bridge, and both are still well above the floor.
    assert min(s["similarity_to_next"] for s in body["stops"][:-1]) > MIN_SIMILARITY


def test_an_id_without_a_vector_is_a_404(env):  # noqa: F811
    client, factory, settings = env
    alpha, _beta, _bridge = seed_bridge(factory, settings)
    assert client.get(f"/api/graph/path?from={alpha[0]}&to=999").status_code == 404


def test_identical_ends_are_a_400(env):  # noqa: F811
    client, factory, settings = env
    alpha, _beta, _bridge = seed_bridge(factory, settings)
    assert (
        client.get(f"/api/graph/path?from={alpha[0]}&to={alpha[0]}").status_code == 400
    )


def test_a_missing_end_is_a_400(env):  # noqa: F811
    client, factory, settings = env
    alpha, _beta, _bridge = seed_bridge(factory, settings)
    assert client.get(f"/api/graph/path?from={alpha[0]}").status_code == 400
    assert client.get("/api/graph/path").status_code == 400


def test_a_text_end_while_warming_says_so(env, monkeypatch):  # noqa: F811
    """'Still starting' is not 'unavailable', exactly as for search."""
    from app.core.warmup import WARMER, WarmupState, WarmupStatus

    client, factory, settings = env
    alpha, _beta, _bridge = seed_bridge(factory, settings)
    monkeypatch.setattr(type(WARMER), "is_ready", property(lambda self: False))
    monkeypatch.setattr(
        WARMER, "status", lambda: WarmupStatus(WarmupState.WARMING, 4.0, None, 22.0)
    )
    monkeypatch.setattr(WARMER, "start", lambda: None)

    res = client.get(f"/api/graph/path?from={alpha[0]}&to_text=beta things")

    assert res.status_code == 503
    detail = res.json()["detail"]
    assert detail["state"] == "warming"
    assert detail["estimated_remaining"] == 22.0


def test_a_text_end_anchors_to_the_nearest_paper(env, monkeypatch):  # noqa: F811
    from app.core.warmup import WARMER, WarmupState, WarmupStatus

    client, factory, settings = env
    alpha, beta, _bridge = seed_bridge(factory, settings)
    monkeypatch.setattr(type(WARMER), "is_ready", property(lambda self: True))
    monkeypatch.setattr(WARMER, "status", lambda: WarmupStatus(WarmupState.READY, 26.0))
    # The phrase embeds to (almost) the second Beta paper's own vector.
    target = VectorStore(
        settings.vectors_dir / f"doc_vectors__{store_slug(settings.embed_model)}",
        dim=settings.embed_dim,
        model_id=settings.embed_model,
    ).get(beta[1])
    monkeypatch.setattr(
        "app.services.embed.encoder.encode_query", lambda text, **_: target
    )

    res = client.get(f"/api/graph/path?from={alpha[0]}&to_text=beta things")

    assert res.status_code == 200
    body = res.json()
    assert body["to"] == {"paper_id": beta[1], "anchored_from_text": "beta things"}
    assert body["stops"][-1]["paper_id"] == beta[1]
    assert body["complete"] is True
