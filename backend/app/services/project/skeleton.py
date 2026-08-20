"""The faint global graph drawn under the map.

A scatter of dots shows where papers sit but not that they are related. A
handful of edges per node restores that without drowning the view: connecting
each paper to its k nearest neighbours in embedding space traces the manifold
the projection was trying to flatten, so clusters read as connected regions
rather than as coincidental piles.

k is deliberately tiny. At k=2 a 4,000-paper corpus draws about 6,000 segments
in one call; at k=10 it draws 40,000 and reads as grey fog that hides exactly
the structure it was added to show.

Neighbours are found in the original embedding space, never in the rendered
coordinates. UMAP distorts global distance on purpose, so a skeleton built from
screen positions would draw the projection's artefacts as if they were
relationships.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_K = 2
#: Below this cosine similarity an edge says "these were the least dissimilar
#: papers available", not "these are related". Drawing it is a lie.
MIN_SIMILARITY = 0.35


def build_skeleton(
    matrix: np.ndarray,
    paper_ids: list[int],
    *,
    k: int = DEFAULT_K,
    min_similarity: float = MIN_SIMILARITY,
) -> np.ndarray:
    """Undirected kNN edges as an (n, 2) array of paper ids."""
    rows = matrix.shape[0]
    if rows < 2:
        return np.zeros((0, 2), dtype=np.int64)

    normalised = matrix / np.clip(
        np.linalg.norm(matrix, axis=1, keepdims=True), 1e-12, None
    )
    similarity = normalised @ normalised.T
    np.fill_diagonal(similarity, -np.inf)  # never link a paper to itself

    k = min(k, rows - 1)
    # argpartition rather than a full sort: only the top k per row matters, and
    # at 4,000 papers that is the difference between O(n^2 log n) and O(n^2).
    top = np.argpartition(-similarity, kth=k - 1, axis=1)[:, :k]

    edges: set[tuple[int, int]] = set()
    for row in range(rows):
        for column in top[row]:
            if similarity[row, column] < min_similarity:
                continue
            a, b = paper_ids[row], paper_ids[int(column)]
            # Canonical ordering collapses a->b and b->a into one segment.
            edges.add((a, b) if a < b else (b, a))

    logger.info("skeleton: %d edges over %d papers (k=%d)", len(edges), rows, k)
    return (
        np.array(sorted(edges), dtype=np.int64)
        if edges
        else np.zeros((0, 2), dtype=np.int64)
    )


def skeleton_path(base: Path) -> Path:
    return base.with_suffix(".skeleton.npy")


def save(base: Path, edges: np.ndarray) -> Path:
    path = skeleton_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(path, edges)
    return path


def load(base: Path) -> np.ndarray:
    path = skeleton_path(base)
    if not path.exists():
        return np.zeros((0, 2), dtype=np.int64)
    return np.load(path)
