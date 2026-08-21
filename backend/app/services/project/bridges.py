"""Finding where two regions of the map meet.

Clusters tell a reader what is distinct. In a library that spans disciplines,
what is *shared* is often the more interesting question — which is invisible on
a map that only draws separations.

A bridge is claimed on two kinds of evidence, and both are required:

*Centroid proximity* says the two regions occupy nearby ground in the embedding
space. On its own it is weak: any two clusters in a small corpus are somewhat
close, and a threshold alone would draw a line between every pair.

*Boundary papers* are the concrete part. A paper that HDBSCAN placed in one
cluster with low confidence, and which is genuinely close to another cluster's
centroid, is a paper doing the work of connecting them. Without at least one,
the regions are merely adjacent rather than linked, and the distinction matters:
a line drawn without it invites the reader to believe in a relationship the
corpus does not contain.

Everything here runs in the original embedding space. UMAP distorts global
distance deliberately, so two regions can be neighbours on screen and unrelated
in meaning.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)

#: A floor, not the test. Below this nothing is plausibly related whatever the
#: corpus looks like.
MIN_CENTROID_SIMILARITY = 0.45

#: The real test is relative. How similar two centroids are means nothing in
#: isolation: in a library where every paper is biology-adjacent, all clusters
#: score above 0.85 and an absolute threshold marks every pair as bridged; in a
#: library spanning mathematics and medicine the same threshold finds nothing.
#: A pair must instead stand out against this corpus's own distribution — be
#: closer than this quantile of all cluster pairs.
RELATIVE_QUANTILE = 0.6
#: A paper this unsure of its own cluster is a candidate for sitting between two.
BOUNDARY_CONFIDENCE = 0.75
#: How close a boundary paper must be to the other centroid to count as evidence.
MIN_BRIDGE_SIMILARITY = 0.45
#: Papers listed per link. Enough to justify the claim, few enough to read.
MAX_BRIDGE_PAPERS = 6
#: Total links drawn. Past this the map becomes a mesh and says nothing.
MAX_LINKS = 12


@dataclass
class Bridge:
    source_id: int
    target_id: int
    similarity: float
    #: Papers that sit between the two regions, strongest first.
    bridge_paper_ids: list[int] = field(default_factory=list)
    shared_terms: list[str] = field(default_factory=list)


def _unit(matrix: np.ndarray) -> np.ndarray:
    return matrix / np.clip(np.linalg.norm(matrix, axis=-1, keepdims=True), 1e-12, None)


def find_bridges(
    matrix: np.ndarray,
    paper_ids: list[int],
    assignments: dict[int, int],
    probabilities: dict[int, float],
    cluster_ids: dict[int, int],
    *,
    min_similarity: float = MIN_CENTROID_SIMILARITY,
    max_links: int = MAX_LINKS,
) -> list[Bridge]:
    """Which regions are linked, and by which papers.

    ``assignments`` maps paper id to HDBSCAN label, ``cluster_ids`` maps that
    label to the stored cluster row id, and ``probabilities`` gives each
    paper's membership strength.
    """
    labels = sorted({label for label in assignments.values() if label in cluster_ids})
    if len(labels) < 2:
        return []

    index_of = {pid: i for i, pid in enumerate(paper_ids)}
    normalised = _unit(matrix)

    centroids: dict[int, np.ndarray] = {}
    for label in labels:
        rows = [
            index_of[pid]
            for pid, lab in assignments.items()
            if lab == label and pid in index_of
        ]
        if rows:
            centroids[label] = _unit(normalised[rows].mean(axis=0))

    # Every pairwise similarity first, so "close" can be judged against how
    # close this particular library's regions generally are.
    pairs: list[tuple[float, int, int]] = []
    for i, left in enumerate(labels):
        for right in labels[i + 1 :]:
            if left in centroids and right in centroids:
                pairs.append((float(centroids[left] @ centroids[right]), left, right))
    if not pairs:
        return []

    relative_floor = float(np.quantile([p[0] for p in pairs], RELATIVE_QUANTILE))
    threshold = max(min_similarity, relative_floor)
    logger.info(
        "bridge threshold %.3f (absolute floor %.2f, corpus quantile %.3f)",
        threshold,
        min_similarity,
        relative_floor,
    )

    bridges: list[Bridge] = []
    for similarity, left, right in pairs:
        if similarity < threshold:
            continue

        papers = _boundary_papers(
            left,
            right,
            centroids,
            normalised,
            index_of,
            assignments,
            probabilities,
        )
        if not papers:
            # Adjacent, but nothing actually spans them. Drawing this would
            # assert a connection the corpus does not support.
            continue

        bridges.append(
            Bridge(
                source_id=cluster_ids[left],
                target_id=cluster_ids[right],
                similarity=round(similarity, 4),
                bridge_paper_ids=papers,
            )
        )

    bridges.sort(key=lambda b: -b.similarity)
    if len(bridges) > max_links:
        logger.info("keeping the %d strongest of %d links", max_links, len(bridges))
    return bridges[:max_links]


def _boundary_papers(
    left: int,
    right: int,
    centroids: dict[int, np.ndarray],
    normalised: np.ndarray,
    index_of: dict[int, int],
    assignments: dict[int, int],
    probabilities: dict[int, float],
) -> list[int]:
    """Papers in one of the two clusters that lean toward the other."""
    scored: list[tuple[float, int]] = []

    for paper_id, label in assignments.items():
        if label not in (left, right) or paper_id not in index_of:
            continue
        # A paper firmly inside its own cluster is not spanning anything.
        if probabilities.get(paper_id, 1.0) > BOUNDARY_CONFIDENCE:
            continue

        other = right if label == left else left
        affinity = float(normalised[index_of[paper_id]] @ centroids[other])
        if affinity >= MIN_BRIDGE_SIMILARITY:
            scored.append((affinity, paper_id))

    scored.sort(reverse=True)
    return [paper_id for _, paper_id in scored[:MAX_BRIDGE_PAPERS]]


def shared_terms(
    left_terms: list[str], right_terms: list[str], limit: int = 8
) -> list[str]:
    """Vocabulary both regions use, in the order the first ranks it."""
    right = set(right_terms)
    return [term for term in left_terms if term in right][:limit]
