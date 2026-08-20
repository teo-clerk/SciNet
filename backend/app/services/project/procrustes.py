"""Aligning a refitted projection onto the previous one.

UMAP's output orientation is arbitrary. Two fits of nearly identical data can
come out rotated, reflected and rescaled relative to each other, so a naive
refit teleports every node to a new position. That is not a cosmetic problem:
the whole value of a spatial map is that the user learns where things are, and
a map that reshuffles whenever a paper is added teaches nothing.

Orthogonal Procrustes recovers the rigid transform that best carries the new
embedding onto the old one, over the papers both runs share. What remains after
applying it is genuine change in the data rather than an artefact of the
optimiser's starting point.
"""

from __future__ import annotations

import numpy as np
from scipy.linalg import orthogonal_procrustes


def align_to(
    candidate: np.ndarray,
    reference: np.ndarray,
    shared: slice | np.ndarray | None = None,
) -> np.ndarray:
    """Rigidly map ``candidate`` onto ``reference``.

    ``shared`` selects the rows of ``candidate`` that correspond, row for row,
    to ``reference``; the recovered transform is then applied to *all* rows, so
    papers new in this run travel with the ones that anchor the alignment.

    Rotation and reflection come from ``orthogonal_procrustes``; uniform scale
    and translation are solved separately, which keeps the transform rigid —
    an anisotropic fit would distort the very distances the map conveys.
    """
    candidate = np.asarray(candidate, dtype=np.float64)
    reference = np.asarray(reference, dtype=np.float64)

    subset = candidate if shared is None else candidate[shared]
    if subset.shape != reference.shape:
        raise ValueError(
            f"shared subset {subset.shape} does not match reference {reference.shape}"
        )
    if subset.shape[0] < 2:
        return candidate.astype(np.float32)

    source_centre = subset.mean(axis=0)
    target_centre = reference.mean(axis=0)
    source = subset - source_centre
    target = reference - target_centre

    source_scale = np.linalg.norm(source)
    target_scale = np.linalg.norm(target)
    if source_scale < 1e-12 or target_scale < 1e-12:
        return candidate.astype(np.float32)

    source_unit = source / source_scale
    target_unit = target / target_scale

    rotation, _ = orthogonal_procrustes(source_unit, target_unit)
    scale = target_scale / source_scale

    aligned = (candidate - source_centre) @ rotation * scale + target_centre
    return aligned.astype(np.float32)


def mean_displacement(a: np.ndarray, b: np.ndarray) -> float:
    """Average distance a node moves between two layouts."""
    return float(np.linalg.norm(np.asarray(a) - np.asarray(b), axis=1).mean())
