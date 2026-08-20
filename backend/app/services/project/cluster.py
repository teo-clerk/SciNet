"""Clustering the corpus, and naming the clusters.

Clustering runs on a *separate* higher-dimensional UMAP, not on the 3-D display
coordinates. The 3-D projection is optimised for something a human can look at,
which means it sacrifices exactly the local density structure HDBSCAN needs;
run over it, HDBSCAN over-fragments and invents boundaries the data does not
have.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)

CLUSTER_UMAP_COMPONENTS = 10
MIN_CLUSTER_SIZE = 8
#: A cluster should be a recognisable region of the library, not a handful of
#: papers, so the floor scales with the corpus — capped, because past a few
#: thousand papers the useful number of regions stops growing with the count.
MAX_MIN_CLUSTER_SIZE = 25
ROWS_PER_CLUSTER_FLOOR = 25
MIN_ROWS_TO_CLUSTER = 30
NOISE_LABEL = -1

#: Excess-of-mass, HDBSCAN's default. "leaf" scored better on one real corpus
#: (ARI 0.681 against 0.561) and was briefly adopted for it — but tested across
#: corpus sizes it degrades badly on clean data, from ARI 0.670 at 75 rows to
#: 0.237 at 300, while eom stays exact at every size. The apparent win was
#: overfitting to a single library.
#:
#: What eom does do is merge genuinely adjacent fields: on that corpus it put
#: astrophysics, climate science and earth science in one region. That is a
#: defensible reading of the semantics rather than an error — those fields
#: share most of their vocabulary — and the actual defect it exposed was in
#: naming, which described the region from an unrepresentative sample of it.
CLUSTER_SELECTION_METHOD = "eom"


@dataclass
class Cluster:
    label: int
    paper_ids: list[int]
    centroid: list[float]
    top_terms: list[str] = field(default_factory=list)
    name: str | None = None

    @property
    def size(self) -> int:
        return len(self.paper_ids)


def cluster_embeddings(
    matrix: np.ndarray,
    paper_ids: list[int],
    *,
    min_cluster_size: int = MIN_CLUSTER_SIZE,
    random_state: int = 42,
) -> tuple[dict[int, int], dict[int, float], list[Cluster]]:
    """Group papers by semantic similarity.

    Returns a paper_id -> label map (label -1 is noise), a paper_id ->
    membership-strength map, and the clusters.
    """
    rows = matrix.shape[0]
    if rows < MIN_ROWS_TO_CLUSTER:
        logger.info("only %d rows; too few to cluster meaningfully", rows)
        return (
            {pid: NOISE_LABEL for pid in paper_ids},
            {pid: 0.0 for pid in paper_ids},
            [],
        )

    import hdbscan
    import umap

    neighbors = min(15, max(2, rows - 1))
    components = min(CLUSTER_UMAP_COMPONENTS, max(2, rows - 2), matrix.shape[1])
    reducer = umap.UMAP(
        n_components=components,
        n_neighbors=neighbors,
        min_dist=0.0,  # tight packing helps density-based clustering
        metric="cosine",
        random_state=random_state,
    )
    dense = reducer.fit_transform(matrix)

    floor = max(
        min_cluster_size, min(MAX_MIN_CLUSTER_SIZE, rows // ROWS_PER_CLUSTER_FLOOR)
    )
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=max(2, min(floor, rows // 4)),
        # min_samples=1 keeps the mutual-reachability smoothing minimal. Raising
        # it was measured to be strictly worse here: with eom it collapsed the
        # corpus to four clusters, and with leaf it discarded up to a quarter of
        # the library as noise.
        min_samples=1,
        metric="euclidean",
        cluster_selection_method=CLUSTER_SELECTION_METHOD,
    )
    labels = clusterer.fit_predict(dense)

    assignments = {
        pid: int(label) for pid, label in zip(paper_ids, labels, strict=True)
    }
    # How strongly each paper belongs to the cluster it was given. HDBSCAN
    # computes this and it was previously discarded; a low value marks a paper
    # sitting between fields, which is information about the paper rather than
    # a defect in the clustering.
    probabilities = {
        pid: float(strength)
        for pid, strength in zip(paper_ids, clusterer.probabilities_, strict=True)
    }

    clusters: list[Cluster] = []
    for label in sorted({int(x) for x in labels if x != NOISE_LABEL}):
        members = [pid for pid, lab in assignments.items() if lab == label]
        rows_for = [i for i, lab in enumerate(labels) if lab == label]
        clusters.append(
            Cluster(
                label=label,
                paper_ids=members,
                centroid=matrix[rows_for].mean(axis=0).tolist(),
            )
        )

    logger.info(
        "found %d cluster(s), %d noise point(s)",
        len(clusters),
        sum(1 for x in labels if x == NOISE_LABEL),
    )
    return assignments, probabilities, clusters


STOP_TERMS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "using",
    "based",
    "our",
    "are",
    "was",
    "were",
    "have",
    "has",
    "can",
    "which",
    "these",
    "such",
    "than",
    "then",
    "them",
    "their",
    "its",
    "into",
    "also",
    "more",
    "paper",
    "papers",
    "study",
    "results",
    "method",
    "methods",
    "approach",
    "model",
    "models",
    "data",
    "new",
    "novel",
    "propose",
    "proposed",
}


def top_terms(titles: list[str], limit: int = 12) -> list[str]:
    """Distinctive words in a cluster's titles.

    Frequency over a stopword filter — crude, but it only has to give the LLM
    something concrete to name, and it costs nothing.
    """
    counts: Counter[str] = Counter()
    for title in titles:
        for word in title.lower().replace("-", " ").split():
            token = "".join(c for c in word if c.isalnum())
            if len(token) > 3 and token not in STOP_TERMS and not token.isdigit():
                counts[token] += 1
    return [term for term, _ in counts.most_common(limit)]
