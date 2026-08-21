"""Cross-cluster bridges.

The claim a bridge makes — "these two areas of your library are connected" — is
easy to make falsely. Centroid proximity alone would draw a line between every
pair of regions in a small corpus, so a link also requires papers that actually
sit between them.
"""

from __future__ import annotations

import numpy as np

from app.services.project.bridges import (
    BOUNDARY_CONFIDENCE,
    MAX_LINKS,
    find_bridges,
    shared_terms,
)


def two_clusters(separation: float, n_per: int = 12, dim: int = 16, seed: int = 0):
    """Two blobs in a shared cone, as normalised document embeddings actually sit.

    Real embeddings are unit vectors that share a large common component — a
    corpus of scientific prose has pairwise cosines around 0.4 to 0.9, not
    around zero. Modelling the clusters as blobs about the origin instead would
    make every centroid a random direction and every similarity meaningless.
    """
    rng = np.random.default_rng(seed)
    shared = np.abs(rng.standard_normal(dim)) + 1.0  # the common ground
    axis = np.zeros(dim)
    axis[0] = separation  # what separates them

    left = shared - axis + rng.standard_normal((n_per, dim)) * 0.15
    right = shared + axis + rng.standard_normal((n_per, dim)) * 0.15
    matrix = np.vstack([left, right]).astype(np.float32)

    paper_ids = list(range(n_per * 2))
    assignments = {p: (0 if p < n_per else 1) for p in paper_ids}
    probabilities = dict.fromkeys(paper_ids, 1.0)
    return matrix, paper_ids, assignments, probabilities


CLUSTER_IDS = {0: 100, 1: 200}


def test_confident_papers_alone_do_not_make_a_bridge():
    """Two regions can be adjacent without anything spanning them, and drawing
    that line invites the reader to believe in a link the corpus lacks."""
    matrix, ids, assignments, probabilities = two_clusters(separation=0.5)
    assert find_bridges(matrix, ids, assignments, probabilities, CLUSTER_IDS) == []


def test_a_boundary_paper_creates_a_bridge():
    matrix, ids, assignments, probabilities = two_clusters(separation=0.5)
    # One paper unsure of its cluster, sitting between the two.
    matrix[0] = (matrix[0] + matrix[12]) / 2
    probabilities[0] = 0.2

    bridges = find_bridges(matrix, ids, assignments, probabilities, CLUSTER_IDS)
    assert len(bridges) == 1
    assert bridges[0].source_id == 100
    assert bridges[0].target_id == 200
    assert 0 in bridges[0].bridge_paper_ids


def test_distant_regions_are_never_linked():
    """However unsure a paper is, unrelated regions must not be joined."""
    matrix, ids, assignments, probabilities = two_clusters(separation=40.0)
    probabilities[0] = 0.1
    assert find_bridges(matrix, ids, assignments, probabilities, CLUSTER_IDS) == []


def test_a_single_cluster_has_nothing_to_bridge():
    matrix, ids, assignments, probabilities = two_clusters(separation=0.5)
    assignments = dict.fromkeys(ids, 0)
    assert find_bridges(matrix, ids, assignments, probabilities, {0: 100}) == []


def test_pairs_are_ordered_so_a_link_is_stored_once():
    matrix, ids, assignments, probabilities = two_clusters(separation=0.5)
    matrix[0] = (matrix[0] + matrix[12]) / 2
    probabilities[0] = 0.2

    bridges = find_bridges(matrix, ids, assignments, probabilities, CLUSTER_IDS)
    assert bridges[0].source_id < bridges[0].target_id


def test_the_number_of_links_is_capped():
    """Past a dozen the map is a mesh and communicates nothing."""
    rng = np.random.default_rng(3)
    groups = 12
    per = 6
    matrix = np.vstack(
        [
            rng.standard_normal((per, 16)) * 0.4 + rng.standard_normal(16) * 0.5
            for _ in range(groups)
        ]
    ).astype(np.float32)
    ids = list(range(groups * per))
    assignments = {p: p // per for p in ids}
    probabilities = dict.fromkeys(ids, 0.1)  # everything is a boundary paper
    cluster_ids = {g: 100 + g for g in range(groups)}

    bridges = find_bridges(matrix, ids, assignments, probabilities, cluster_ids)
    assert len(bridges) <= MAX_LINKS


def test_links_are_ordered_by_strength():
    rng = np.random.default_rng(5)
    matrix = np.vstack(
        [
            rng.standard_normal((6, 16)) * 0.3 + rng.standard_normal(16) * 0.4
            for _ in range(4)
        ]
    ).astype(np.float32)
    ids = list(range(24))
    assignments = {p: p // 6 for p in ids}
    probabilities = dict.fromkeys(ids, 0.1)
    bridges = find_bridges(
        matrix, ids, assignments, probabilities, {g: 100 + g for g in range(4)}
    )
    similarities = [b.similarity for b in bridges]
    assert similarities == sorted(similarities, reverse=True)


def test_a_paper_firmly_in_its_cluster_is_not_evidence():
    """A paper at the centre of its region is not spanning anything."""
    matrix, ids, assignments, probabilities = two_clusters(separation=0.5)
    matrix[0] = (matrix[0] + matrix[12]) / 2
    probabilities[0] = BOUNDARY_CONFIDENCE + 0.1

    assert find_bridges(matrix, ids, assignments, probabilities, CLUSTER_IDS) == []


def test_shared_terms_preserve_the_first_ranking():
    assert shared_terms(["neural", "vision", "cortex"], ["cortex", "neural"]) == [
        "neural",
        "cortex",
    ]


def test_shared_terms_of_unrelated_vocabularies_is_empty():
    assert shared_terms(["galaxy", "redshift"], ["protein", "genome"]) == []
