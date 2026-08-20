"""Clustering behaviour on data with a known answer."""

from __future__ import annotations

import numpy as np

from app.services.project.cluster import (
    NOISE_LABEL,
    cluster_embeddings,
    top_terms,
)


def blobs(n_per=25, dim=16, groups=3, seed=0):
    rng = np.random.default_rng(seed)
    centres = rng.standard_normal((groups, dim)) * 8
    xs, truth = [], []
    for g, c in enumerate(centres):
        xs.append(c + rng.standard_normal((n_per, dim)) * 0.4)
        truth += [g] * n_per
    return np.vstack(xs).astype(np.float32), truth


def test_too_few_papers_are_all_noise():
    matrix, _ = blobs(n_per=3)
    assignments, probabilities, clusters = cluster_embeddings(matrix, list(range(9)))
    assert clusters == []
    assert set(assignments.values()) == {NOISE_LABEL}


def test_finds_the_planted_groups():
    matrix, truth = blobs(n_per=25, groups=3)
    ids = list(range(len(truth)))
    assignments, probabilities, clusters = cluster_embeddings(
        matrix, ids, min_cluster_size=5
    )

    assert 2 <= len(clusters) <= 4, f"expected ~3 clusters, got {len(clusters)}"

    # Papers planted together should mostly land together.
    agree = 0
    for i, j in zip(range(0, 25), range(1, 26), strict=False):
        if assignments[i] == assignments[j] != NOISE_LABEL:
            agree += 1
    assert agree > 15


def test_every_paper_gets_an_assignment():
    matrix, truth = blobs(n_per=25)
    ids = list(range(len(truth)))
    assignments, _, _ = cluster_embeddings(matrix, ids, min_cluster_size=5)
    assert set(assignments) == set(ids)


def test_cluster_carries_its_members_and_centroid():
    matrix, truth = blobs(n_per=25)
    _, _, clusters = cluster_embeddings(
        matrix, list(range(len(truth))), min_cluster_size=5
    )
    for cluster in clusters:
        assert cluster.size == len(cluster.paper_ids)
        assert len(cluster.centroid) == matrix.shape[1]


def test_clustering_is_deterministic():
    matrix, truth = blobs(n_per=25)
    ids = list(range(len(truth)))
    a, _, _ = cluster_embeddings(matrix, ids, min_cluster_size=5)
    b, _, _ = cluster_embeddings(matrix, ids, min_cluster_size=5)
    assert a == b


def test_top_terms_ignores_filler():
    terms = top_terms(
        [
            "A Novel Approach to Graph Neural Networks",
            "Graph Neural Networks for Molecules",
            "Scaling Graph Neural Networks",
        ]
    )
    assert "graph" in terms and "neural" in terms and "networks" in terms
    for filler in ("novel", "approach", "the", "for"):
        assert filler not in terms


def test_top_terms_on_empty_input():
    assert top_terms([]) == []


def test_membership_strength_is_reported_per_paper():
    """A low value marks a paper between fields — information, not a defect."""
    matrix, truth = blobs(n_per=25)
    ids = list(range(len(truth)))
    _, probabilities, _ = cluster_embeddings(matrix, ids, min_cluster_size=5)

    assert set(probabilities) == set(ids)
    assert all(0.0 <= p <= 1.0 for p in probabilities.values())


def test_noise_points_have_no_membership_strength():
    matrix, _ = blobs(n_per=3)
    assignments, probabilities, _ = cluster_embeddings(matrix, list(range(9)))
    assert all(probabilities[pid] == 0.0 for pid in assignments)
