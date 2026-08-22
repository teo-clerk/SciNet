"""Projection parameters as functions of corpus size.

Every constant here used to be a constant, tuned against whichever library was
in front of us. That works until the library changes size, and then it fails in
one of two directions, both of which the reader sees immediately:

* **Too local.** ``n_neighbors`` fixed at 15 is a quarter of a 60-paper library
  and a thirtieth of a 500-paper one. Left at 15 as the corpus grows, UMAP
  smooths over less and less of the global structure, and the map becomes a
  spray of small lumps with no continents.
* **Too coarse.** A ``min_cluster_size`` floor that tops out at 25 means a
  5,000-paper library is asked to find regions of 25 — hundreds of them, most
  of them noise, and the reader gets a white cloud.

So they scale. The law throughout is **square root**, not linear: what should
grow with the corpus is the *resolution* of the map, and resolution grows with
the square root of the sample, not with the sample. A library ten times larger
does not have ten times as many meaningful regions in it, and asking for that
is how a map turns back into a list.

The square root also happens to reproduce the tuning that was measured on the
small corpus, which is the strongest evidence available that it is the right
shape: ``round(rows / 8)`` was chosen by hand at 57 papers and gives 7;
``sqrt(57)`` gives 7.5. The old rule was the square root all along, over the
narrow range where it had been checked.
"""

from __future__ import annotations

import math

#: UMAP's neighbourhood, clamped at both ends. Below 5 the embedding is noise;
#: above 60 it is averaging over so much of the corpus that local structure —
#: the thing the map is for — stops surviving, and the cost grows with it.
MIN_NEIGHBOURS = 5
MAX_NEIGHBOURS = 60

#: Smallest group that counts as a region. Three papers on one subject is a
#: theme; two is a coincidence. This one is genuinely constant — it is a
#: statement about meaning, not about scale.
ABSOLUTE_MIN_CLUSTER_SIZE = 3

#: Ceiling on the floor search. Expressed against the square root so that the
#: number of regions a corpus *can* resolve grows sub-linearly: at 500 papers
#: this allows regions of ~45, at 5,000 of ~140 — roughly 11 and 35 regions
#: respectively, which is the range a person can still navigate.
FLOOR_CEILING_FACTOR = 2.0

#: How many floors to evaluate. Each costs one HDBSCAN fit, which is cheap at
#: 500 rows and not free at 5,000.
MAX_FLOOR_CANDIDATES = 8

#: Below this there is nothing to cluster: HDBSCAN needs enough points for a
#: density estimate to mean anything.
MIN_ROWS_TO_CLUSTER = 30

#: ``min_samples`` controls how conservative the density estimate is. One was
#: measured as strictly best on the 57-paper corpus — raising it collapsed the
#: corpus to four clusters — and that measurement is about a corpus where every
#: point is scarce. At thousands of points a floor of one makes the estimate
#: chain through sparse bridges between genuinely separate regions, so it grows,
#: slowly, and only once the corpus is large enough for the measurement not to
#: apply.
MIN_SAMPLES_FLOOR_ROWS = 200


def neighbours_for(rows: int) -> int:
    """UMAP neighbourhood size for a corpus of ``rows`` papers.

    n_neighbors is the dial between local and global structure: small values
    preserve fine detail and shatter the whole, large values recover continents
    and dissolve neighbourhoods.
    """
    if rows < 2:
        return MIN_NEIGHBOURS
    # Two laws, and the smaller wins. `rows / 8` is the rule that was measured
    # by hand on the small corpus, where every paper is scarce and locality is
    # everything; `sqrt(rows)` is what keeps a large corpus from being smoothed
    # into a single blob. They cross at 64 papers, so the minimum is the first
    # below that and the second above — one expression, no regime boundary, and
    # it reproduces both measurements where each was taken.
    scaled = round(min(rows / 8, math.sqrt(rows)))
    bounded = max(MIN_NEIGHBOURS, min(MAX_NEIGHBOURS, scaled))
    # Never more neighbours than there are other points to be neighbours with.
    return min(bounded, rows - 1)


def floor_candidates(rows: int) -> list[int]:
    """Plausible minimum cluster sizes, for the search to score.

    Bounded at both ends. An unconstrained search picks whatever scores best on
    separation, and "two enormous blobs" scores very well on separation while
    telling the reader nothing.
    """
    upper = max(
        ABSOLUTE_MIN_CLUSTER_SIZE + 1,
        round(FLOOR_CEILING_FACTOR * math.sqrt(rows)),
    )
    span = list(range(ABSOLUTE_MIN_CLUSTER_SIZE, upper + 1))
    if len(span) <= MAX_FLOOR_CANDIDATES:
        return span
    step = (len(span) - 1) / (MAX_FLOOR_CANDIDATES - 1)
    return sorted({span[round(index * step)] for index in range(MAX_FLOOR_CANDIDATES)})


def min_samples_for(rows: int, floor: int) -> int:
    """How conservative HDBSCAN's density estimate should be.

    Stays at 1 for small corpora, where it was measured to be strictly best and
    where anything higher throws away points a small library cannot spare. Past
    a few hundred papers it grows with the square root of the floor, which
    keeps sparse chains between adjacent regions from merging them without
    declaring large parts of the corpus noise.
    """
    if rows < MIN_SAMPLES_FLOOR_ROWS:
        return 1
    return max(1, min(floor, round(math.sqrt(floor))))


def max_cluster_share(rows: int) -> float:
    """The largest share of the corpus one cluster may hold.

    A region covering most of the map is the unclustered state wearing a label.
    The bound relaxes as the corpus grows: half of 57 papers is a catch-all,
    while half of 5,000 could be a genuine super-field with real internal
    structure that a later refit will resolve.
    """
    if rows < 200:
        return 0.5
    return 0.6 if rows < 1000 else 0.7
