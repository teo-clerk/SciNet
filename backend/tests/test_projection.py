"""Persistent 3-D projection with incremental inserts.

Two properties matter more than the projection quality itself:

**Persistence.** A fitted reducer and the exact matrix it saw are written to
disk, so reopening the app never refits.

**Stability.** UMAP's output orientation is arbitrary — two fits of nearly
identical data can be rotated, reflected and rescaled relative to each other. A
naive refit therefore teleports every node and destroys the spatial memory the
user has built of their own library. Procrustes alignment is what prevents it.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.services.project.procrustes import align_to
from app.services.project.reducer import ProjectionModel, ProjectionParams


def clustered(n_per: int = 40, dim: int = 16, seed: int = 0):
    """Three well-separated blobs, so cluster membership is checkable."""
    rng = np.random.default_rng(seed)
    centres = rng.standard_normal((3, dim)) * 6
    xs, labels = [], []
    for label, centre in enumerate(centres):
        xs.append(centre + rng.standard_normal((n_per, dim)))
        labels += [label] * n_per
    matrix = np.vstack(xs).astype(np.float32)
    return matrix, np.array(labels)


# --- Procrustes -----------------------------------------------------------


def test_alignment_undoes_a_rotation():
    rng = np.random.default_rng(1)
    reference = rng.standard_normal((50, 3))

    theta = 0.9
    rotation = np.array(
        [
            [np.cos(theta), -np.sin(theta), 0],
            [np.sin(theta), np.cos(theta), 0],
            [0, 0, 1],
        ]
    )
    rotated = reference @ rotation.T

    recovered = align_to(rotated, reference)
    assert np.abs(recovered - reference).max() < 1e-6


def test_alignment_undoes_a_reflection():
    """UMAP output is reflection-arbitrary; a mirrored map is unrecognisable."""
    rng = np.random.default_rng(2)
    reference = rng.standard_normal((50, 3))
    mirrored = reference * np.array([1, -1, 1])

    recovered = align_to(mirrored, reference)
    assert np.abs(recovered - reference).max() < 1e-6


def test_alignment_undoes_translation_and_scale():
    rng = np.random.default_rng(3)
    reference = rng.standard_normal((40, 3))
    moved = reference * 3.5 + np.array([10.0, -4.0, 2.0])

    recovered = align_to(moved, reference)
    assert np.abs(recovered - reference).max() < 1e-5


def test_alignment_reduces_displacement_massively():
    """The number that matters: how far nodes move across a refit."""
    rng = np.random.default_rng(4)
    reference = rng.standard_normal((80, 3))
    theta = 2.1
    rotation = np.array(
        [
            [np.cos(theta), -np.sin(theta), 0],
            [np.sin(theta), np.cos(theta), 0],
            [0, 0, 1],
        ]
    )
    refit = reference @ rotation.T + 0.02 * rng.standard_normal((80, 3))

    before = np.linalg.norm(refit - reference, axis=1).mean()
    after = np.linalg.norm(align_to(refit, reference) - reference, axis=1).mean()
    assert after < before / 10


def test_alignment_on_a_shared_subset():
    """A refit covers more papers than the previous run; only shared ones align."""
    rng = np.random.default_rng(5)
    reference = rng.standard_normal((30, 3))
    theta = 0.6
    rotation = np.array(
        [
            [np.cos(theta), -np.sin(theta), 0],
            [np.sin(theta), np.cos(theta), 0],
            [0, 0, 1],
        ]
    )
    grown = np.vstack([reference, rng.standard_normal((10, 3))]) @ rotation.T

    recovered = align_to(grown, reference, shared=slice(0, 30))
    assert np.abs(recovered[:30] - reference).max() < 1e-6
    assert recovered.shape == (40, 3)


# --- fit / transform / persist -------------------------------------------


def test_pca_is_used_below_the_umap_threshold(tmp_path):
    matrix, _ = clustered(n_per=8)
    model = ProjectionModel(tmp_path / "proj", ProjectionParams(min_umap_rows=100))
    coords = model.fit(matrix)

    assert model.method == "pca", "UMAP on a tiny corpus is unstable and meaningless"
    assert coords.shape == (matrix.shape[0], 3)


def test_umap_is_used_above_the_threshold(tmp_path):
    matrix, _ = clustered(n_per=40)
    model = ProjectionModel(tmp_path / "proj", ProjectionParams(min_umap_rows=50))
    coords = model.fit(matrix)

    assert model.method == "umap"
    assert coords.shape == (matrix.shape[0], 3)


def test_fit_separates_known_clusters(tmp_path):
    matrix, labels = clustered(n_per=40)
    model = ProjectionModel(tmp_path / "proj", ProjectionParams(min_umap_rows=50))
    coords = model.fit(matrix)

    # Within-cluster spread must be clearly tighter than between-cluster.
    centroids = np.array([coords[labels == c].mean(axis=0) for c in range(3)])
    within = np.mean(
        [
            np.linalg.norm(coords[labels == c] - centroids[c], axis=1).mean()
            for c in range(3)
        ]
    )
    between = np.linalg.norm(centroids[:, None] - centroids[None], axis=-1)
    between = between[between > 0].mean()
    assert between > within * 2


def test_model_persists_and_reloads_without_refitting(tmp_path):
    matrix, _ = clustered(n_per=40)
    model = ProjectionModel(tmp_path / "proj", ProjectionParams(min_umap_rows=50))
    model.fit(matrix)
    model.save()

    reloaded = ProjectionModel.load(tmp_path / "proj")
    assert reloaded.method == model.method
    assert reloaded.is_fitted
    assert reloaded.fit_matrix.shape == matrix.shape


def test_transform_places_a_new_point_without_refitting(tmp_path):
    matrix, labels = clustered(n_per=40)
    model = ProjectionModel(tmp_path / "proj", ProjectionParams(min_umap_rows=50))
    model.fit(matrix)

    # A point drawn from cluster 0 should land near cluster 0.
    member = matrix[labels == 0]
    newcomer = member.mean(axis=0) + 0.1

    placed = model.transform(newcomer[None, :])
    assert placed.shape == (1, 3)

    coords = model.coords
    centroids = np.array([coords[labels == c].mean(axis=0) for c in range(3)])
    nearest = int(np.argmin(np.linalg.norm(centroids - placed[0], axis=1)))
    assert nearest == 0


def test_transform_works_after_a_reload(tmp_path):
    """The point of persistence: inserts stay cheap across restarts."""
    matrix, labels = clustered(n_per=40)
    model = ProjectionModel(tmp_path / "proj", ProjectionParams(min_umap_rows=50))
    model.fit(matrix)
    model.save()

    reloaded = ProjectionModel.load(tmp_path / "proj")
    placed = reloaded.transform(matrix[labels == 2].mean(axis=0)[None, :])
    assert placed.shape == (1, 3)
    assert np.isfinite(placed).all()


def test_transform_before_fit_raises(tmp_path):
    model = ProjectionModel(tmp_path / "proj", ProjectionParams())
    with pytest.raises(RuntimeError, match="not fitted"):
        model.transform(np.zeros((1, 16), dtype=np.float32))


def test_fit_is_deterministic(tmp_path):
    """Layout stability starts here: same input, same map."""
    matrix, _ = clustered(n_per=40)
    a = ProjectionModel(tmp_path / "a", ProjectionParams(min_umap_rows=50)).fit(matrix)
    b = ProjectionModel(tmp_path / "b", ProjectionParams(min_umap_rows=50)).fit(matrix)
    np.testing.assert_allclose(a, b, atol=1e-4)


# --- drift ---------------------------------------------------------------


def test_off_manifold_score_is_low_for_familiar_points(tmp_path):
    matrix, _ = clustered(n_per=40)
    model = ProjectionModel(tmp_path / "proj", ProjectionParams(min_umap_rows=50))
    model.fit(matrix)

    score = model.off_manifold_score(matrix[0][None, :])[0]
    assert score < 1.0


def test_off_manifold_score_is_high_for_a_new_field(tmp_path):
    """A paper from a genuinely unseen area is placed provisionally."""
    matrix, _ = clustered(n_per=40)
    model = ProjectionModel(tmp_path / "proj", ProjectionParams(min_umap_rows=50))
    model.fit(matrix)

    familiar = model.off_manifold_score(matrix[:5])
    alien = model.off_manifold_score(
        (np.ones((1, matrix.shape[1])) * 500).astype(np.float32)
    )
    assert alien[0] > familiar.max() * 2
