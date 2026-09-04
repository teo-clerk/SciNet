"""Idea trails: the chain of papers that leads from one to another.

"How does this paper relate to that one?" has two bad answers. A single
similarity number says how far apart they are and nothing about the ground in
between; a straight line through the projection crosses whatever UMAP happened
to put there. The good answer is a sequence of real papers, each a small step
from the last, and that is a shortest path through a neighbour graph built in
the original embedding space.

The graph here is built on the fly, not read from the skeleton on disk. The
skeleton draws k=2 edges because at k=10 the map is grey fog; but two edges per
node leaves a graph that is barely connected, and a trail that gives up after
three hops because the skeleton ran out of edges is not a trail. Routing uses
the house neighbourhood law from ``scaling`` — about 7 edges at 60 papers, 60
at 4,000 — which is dense enough to route through and is never drawn.

Edge weights are ``(1 - cosine) + HOP_PENALTY``. Pure ``1 - cosine`` lets
Dijkstra crawl through twenty near-identical papers to save 0.02 of distance,
which is a chain nobody reads. A neighbour hop typically costs 0.10–0.30, so
a penalty of 0.05 makes a detour worth taking only when it saves more than
about a sixth of a hop — enough to prefer a direct step, not enough to skip a
genuinely intermediate paper.

When two papers are not connected at all — two islands in a library that
spans unrelated fields — there is no honest path, and the module says so:
``complete`` is false and the trail is a greedy climb toward the target that
then jumps. The jump is shown rather than hidden.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import dijkstra

from app.services.project.scaling import neighbours_for
from app.services.project.skeleton import MIN_SIMILARITY

#: Added to every edge. See the module docstring for the reasoning.
HOP_PENALTY = 0.05
#: Stops shown. A trail longer than this is thinned to a sample of itself.
MAX_STOPS = 14
#: Steps the greedy fallback may take before it jumps to the target.
MAX_GREEDY_STEPS = 12

__all__ = [
    "HOP_PENALTY",
    "MAX_STOPS",
    "MIN_SIMILARITY",
    "KnnGraph",
    "PathResult",
    "build_graph",
    "cache_key",
    "find_path",
    "graph_for",
    "greedy_walk",
    "shortest_path",
    "thin",
]


@dataclass(frozen=True)
class KnnGraph:
    paper_ids: tuple[int, ...]
    index_of: dict[int, int]
    #: Symmetric distances; a row's non-zeros are the node's neighbours.
    csr: sparse.csr_matrix
    #: Unit-normalised rows, kept for the greedy walk and for reporting the
    #: cosine between consecutive stops without another pass over the store.
    unit: np.ndarray
    #: What the graph was built from, so a cache can tell whether it is stale.
    key: tuple


@dataclass(frozen=True)
class PathResult:
    stops: tuple[int, ...]
    #: True when every consecutive pair is a real neighbour edge. False means
    #: the last step is a jump across a gap the corpus does not bridge.
    complete: bool
    hops: int


def _unit(matrix: np.ndarray) -> np.ndarray:
    matrix = np.asarray(matrix, dtype=np.float32)
    return matrix / np.clip(np.linalg.norm(matrix, axis=-1, keepdims=True), 1e-12, None)


def build_graph(
    matrix: np.ndarray,
    paper_ids: Sequence[int],
    *,
    k: int | None = None,
    key: tuple = (),
) -> KnnGraph:
    """The routing graph over a set of vectors.

    Symmetrised: an edge exists if either end chose the other as a neighbour.
    A one-directional kNN graph leaves a paper on the fringe of a region able
    to reach the centre but not to be reached from it, and a trail should not
    depend on which end the reader typed first.
    """
    ids = tuple(int(p) for p in paper_ids)
    n = len(ids)
    unit = _unit(matrix)
    index_of = {pid: i for i, pid in enumerate(ids)}
    if n < 2:
        return KnnGraph(ids, index_of, sparse.csr_matrix((n, n)), unit, key)

    k = min(neighbours_for(n) if k is None else k, n - 1)

    # The same n x n product the skeleton pays for; at 4,000 papers it is a
    # 64 MB float32 matrix and a few tens of milliseconds.
    similarity = unit @ unit.T
    np.fill_diagonal(similarity, -np.inf)
    top = np.argpartition(-similarity, kth=k - 1, axis=1)[:, :k]

    rows = np.repeat(np.arange(n), k)
    cols = top.ravel()
    sims = similarity[rows, cols]
    keep = sims >= MIN_SIMILARITY
    rows, cols, sims = rows[keep], cols[keep], sims[keep]

    # Distances are strictly positive (cosine <= 1 gives at least HOP_PENALTY),
    # so the sparse matrix's implicit zero never collides with a real edge.
    distance = (1.0 - sims) + HOP_PENALTY
    directed = sparse.coo_matrix((distance, (rows, cols)), shape=(n, n)).tocsr()
    # Where both directions exist they carry the same cosine, so the maximum
    # is simply "whichever side has the edge".
    undirected = directed.maximum(directed.T).tocsr()
    return KnnGraph(ids, index_of, undirected, unit, key)


def _neighbours(graph: KnnGraph, node: int) -> np.ndarray:
    start, stop = graph.csr.indptr[node], graph.csr.indptr[node + 1]
    return graph.csr.indices[start:stop]


def shortest_path(graph: KnnGraph, src: int, dst: int) -> list[int] | None:
    """Paper ids along the cheapest route, or None when none exists."""
    s, t = graph.index_of[src], graph.index_of[dst]
    if s == t:
        return [src]

    distances, predecessors = dijkstra(
        graph.csr, directed=False, indices=s, return_predecessors=True
    )
    if not np.isfinite(distances[t]):
        return None

    route = [t]
    while route[-1] != s:
        route.append(int(predecessors[route[-1]]))
    return [graph.paper_ids[i] for i in reversed(route)]


def greedy_walk(
    graph: KnnGraph, src: int, dst: int, *, max_steps: int = MAX_GREEDY_STEPS
) -> list[int]:
    """Climb toward the target along neighbour edges, then jump to it.

    Each step goes to the neighbour most similar to the destination, and only
    if it is more similar than where we already stand. The walk therefore ends
    at the paper in the source's connected region that is closest to the
    target — the natural place to show the gap from.
    """
    s, t = graph.index_of[src], graph.index_of[dst]
    target = graph.unit[t]

    route = [s]
    visited = {s}
    current = s
    for _ in range(max_steps):
        if current == t:
            break
        best, best_similarity = None, float(graph.unit[current] @ target)
        for neighbour in _neighbours(graph, current):
            if neighbour in visited:
                continue
            similarity = float(graph.unit[neighbour] @ target)
            if similarity > best_similarity:
                best, best_similarity = int(neighbour), similarity
        if best is None:
            break
        route.append(best)
        visited.add(best)
        current = best

    if route[-1] != t:
        route.append(t)
    return [graph.paper_ids[i] for i in route]


def find_path(graph: KnnGraph, src: int, dst: int) -> PathResult:
    """The trail between two papers, honest about whether it is one."""
    if src == dst:
        return PathResult(stops=(src,), complete=True, hops=0)

    route = shortest_path(graph, src, dst)
    if route is not None:
        return PathResult(stops=tuple(route), complete=True, hops=len(route) - 1)

    walk = greedy_walk(graph, src, dst)
    return PathResult(stops=tuple(walk), complete=False, hops=len(walk) - 1)


def thin(stops: Sequence[int], limit: int = MAX_STOPS) -> list[int]:
    """At most ``limit`` stops, both ends kept, the middle sampled evenly.

    A forty-hop trail is real but unreadable; showing every fourth paper keeps
    its shape. The endpoints are never sampled away — they are the question.
    """
    if len(stops) <= limit:
        return list(stops)
    limit = max(limit, 2)
    positions = np.linspace(0, len(stops) - 1, num=limit).round().astype(int)
    return [stops[i] for i in dict.fromkeys(positions.tolist())]


# --- process-wide cache ------------------------------------------------------

_CACHE: KnnGraph | None = None
_CACHE_LOCK = threading.Lock()


def cache_key(store, settings) -> tuple:
    """What has to be unchanged for a cached graph to still be right.

    The count alone is not enough: a re-embedded paper keeps the count and
    changes a vector. The header file is rewritten on every write to the
    store, so its modification time catches that case.
    """
    return (
        settings.embed_model,
        store.count,
        store.meta_path.stat().st_mtime_ns,
    )


def graph_for(store, settings) -> KnnGraph:
    """The routing graph for the whole store, rebuilt only when the store changes."""
    global _CACHE
    key = cache_key(store, settings)
    with _CACHE_LOCK:
        if _CACHE is not None and _CACHE.key == key:
            return _CACHE
        matrix, ids = store.matrix()
        _CACHE = build_graph(matrix, ids, key=key)
        return _CACHE
