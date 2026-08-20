"""Behaviour on a small library.

A personal collection starts at a few dozen papers, and parameters tuned for a
few hundred fail there in a specific way: with a minimum cluster size of eight,
no topic in a sixty-paper library has enough papers to become a cluster, so
everything merges into two or three blobs. Measured on a corpus whose true
grouping is known, that scored ARI 0.354 against 0.641 after these changes.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.services.project.cluster import (
    ABSOLUTE_MIN_CLUSTER_SIZE,
    MAX_FLOOR_CANDIDATES,
    _floor_candidates,
    _neighbours_for,
    choose_min_cluster_size,
    cluster_embeddings,
)
from app.services.project.reducer import ProjectionModel, ProjectionParams


def blobs(n_per, groups, dim=32, seed=0, spread=0.5):
    rng = np.random.default_rng(seed)
    centres = rng.standard_normal((groups, dim)) * 8
    xs, truth = [], []
    for g, c in enumerate(centres):
        xs.append(c + rng.standard_normal((n_per, dim)) * spread)
        truth += [g] * n_per
    return np.vstack(xs).astype(np.float32), truth


# --- neighbourhood scaling -----------------------------------------------


def test_a_small_corpus_gets_a_small_neighbourhood():
    """Fifteen neighbours is a quarter of a sixty-paper library."""
    assert _neighbours_for(60) < 15
    assert _neighbours_for(60) >= 5


def test_a_large_corpus_keeps_the_full_neighbourhood():
    assert _neighbours_for(300) == 15
    assert _neighbours_for(4000) == 15


def test_the_neighbourhood_never_collapses_to_nothing():
    for rows in (10, 20, 30):
        assert _neighbours_for(rows) >= 5


# --- the candidate band ---------------------------------------------------


def test_the_floor_band_starts_at_a_meaningful_group():
    """Two papers together is a coincidence; three is a theme."""
    assert min(_floor_candidates(63)) == ABSOLUTE_MIN_CLUSTER_SIZE


def test_the_band_lets_a_small_library_show_many_regions():
    """With a floor of eight, no topic in a 63-paper library can exist."""
    assert min(_floor_candidates(63)) <= 6


def test_the_band_scales_with_the_corpus():
    assert max(_floor_candidates(1000)) > max(_floor_candidates(63))


def test_the_search_stays_cheap_on_a_large_corpus():
    """Each candidate costs an HDBSCAN fit."""
    assert len(_floor_candidates(4000)) <= MAX_FLOOR_CANDIDATES


def test_the_band_is_never_empty():
    for rows in (10, 30, 63, 300, 4000):
        assert _floor_candidates(rows)


# --- the adaptive floor ---------------------------------------------------


def test_a_floor_is_chosen_for_a_small_corpus():
    import umap

    matrix, _ = blobs(n_per=6, groups=10)
    dense = umap.UMAP(
        n_components=5,
        n_neighbors=_neighbours_for(60),
        min_dist=0.0,
        metric="cosine",
        random_state=42,
    ).fit_transform(matrix)
    floor = choose_min_cluster_size(dense, matrix.shape[0])

    assert ABSOLUTE_MIN_CLUSTER_SIZE <= floor <= max(_floor_candidates(60))


def test_a_degenerate_matrix_falls_back_rather_than_raising():
    """Identical points produce no valid clustering; returning noise for the
    entire library would be worse than grouping it somehow."""
    dense = np.ones((40, 5), dtype=np.float32)
    assert choose_min_cluster_size(dense, 40) == ABSOLUTE_MIN_CLUSTER_SIZE


# --- end to end on a small library ---------------------------------------


def test_many_small_topics_are_not_merged_into_a_blob():
    """The failure this work exists to fix: ten topics collapsing into three."""
    matrix, truth = blobs(n_per=6, groups=10, spread=0.4)
    _, _, clusters = cluster_embeddings(matrix, list(range(len(truth))))

    assert len(clusters) >= 6, (
        f"ten planted topics produced only {len(clusters)} clusters"
    )


def test_a_few_broad_topics_are_not_shattered():
    """The opposite failure: a fixed small floor fragments a simple library."""
    matrix, truth = blobs(n_per=21, groups=3, spread=0.4)
    _, _, clusters = cluster_embeddings(matrix, list(range(len(truth))))

    assert len(clusters) <= 6, f"three planted topics produced {len(clusters)} clusters"


def test_a_small_library_does_not_lose_papers_to_noise():
    matrix, truth = blobs(n_per=6, groups=10, spread=0.4)
    assignments, _, _ = cluster_embeddings(matrix, list(range(len(truth))))

    noise = sum(1 for label in assignments.values() if label == -1)
    assert noise <= len(truth) * 0.25, f"{noise} of {len(truth)} papers unclustered"


# --- the display projection ----------------------------------------------


def test_a_sixty_paper_library_gets_a_umap_layout():
    """PCA is linear; on 63 papers it rendered the fields as overlapping mush
    (silhouette 0.08 against UMAP's 0.38)."""
    matrix, truth = blobs(n_per=6, groups=10)
    model = ProjectionModel("/tmp/small-proj", ProjectionParams())
    model.fit(matrix)
    assert model.method == "umap"


def test_a_handful_of_papers_still_falls_back_to_pca():
    """Below the threshold there is no structure to find, and PCA cannot fail."""
    matrix, _ = blobs(n_per=3, groups=3)
    model = ProjectionModel("/tmp/tiny-proj", ProjectionParams())
    model.fit(matrix)
    assert model.method == "pca"


@pytest.mark.parametrize("rows", [25, 63, 150])
def test_the_layout_is_finite_at_every_small_size(tmp_path, rows):
    matrix, _ = blobs(n_per=max(2, rows // 5), groups=5)
    model = ProjectionModel(tmp_path / f"p{rows}", ProjectionParams())
    coords = model.fit(matrix)
    assert np.isfinite(coords).all()
