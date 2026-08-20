"""The persistent 3-D projection: fit once, insert incrementally.

Compute is not the reason this is incremental. A full UMAP refit at 4,000 x
1024 takes about a minute, which is affordable. The reasons are:

1. **Latency.** Dropping five papers into the library should place them in
   seconds, not trigger a minute of CPU.
2. **Stability.** Every refit re-randomises the layout's orientation. Refitting
   on every insert would mean the map never looks the same twice.

So the reducer and the exact matrix it was fitted on are persisted together.
New papers go through ``transform``, which is approximate but instant, and are
flagged so the UI can show they are provisionally placed. A full refit happens
only when enough has drifted to warrant it — and is then Procrustes-aligned
onto the previous layout before anyone sees it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

import joblib
import numpy as np

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProjectionParams:
    n_components: int = 3
    n_neighbors: int = 15
    min_dist: float = 0.05
    metric: str = "cosine"
    #: Fixed so the same corpus always produces the same map. This makes UMAP
    #: single-threaded, which at a few thousand rows is a fair trade for a
    #: layout the user can rely on.
    random_state: int = 42
    #: Below this many rows there is barely a map to make, and PCA is
    #: deterministic and instant. The threshold used to be 200 on the
    #: assumption that UMAP is unstable on small corpora — measured against
    #: libraries whose true grouping is known, that was wrong in the direction
    #: that matters: at 63 papers PCA scored a silhouette of 0.08 on the
    #: rendered coordinates (fields overlapping into mush) against UMAP's 0.38,
    #: and UMAP won at every size down to twenty papers.
    min_umap_rows: int = 20
    #: Neighbours consulted when scoring how far a new point sits from the
    #: fitted manifold.
    drift_neighbors: int = 15


class ProjectionModel:
    """A fitted reducer plus everything needed to reason about it later."""

    def __init__(self, base_path: Path | str, params: ProjectionParams | None = None):
        self.base = Path(base_path)
        self.params = params or ProjectionParams()
        self.method: str | None = None
        self._reducer = None
        self.fit_matrix: np.ndarray | None = None
        self.coords: np.ndarray | None = None

    # --- paths ------------------------------------------------------------

    @property
    def reducer_path(self) -> Path:
        return self.base.with_suffix(".reducer.joblib")

    @property
    def matrix_path(self) -> Path:
        return self.base.with_suffix(".fit.npy")

    @property
    def coords_path(self) -> Path:
        return self.base.with_suffix(".coords.npy")

    @property
    def meta_path(self) -> Path:
        return self.base.with_suffix(".meta.json")

    @property
    def is_fitted(self) -> bool:
        return self._reducer is not None or self.method == "pca"

    # --- fitting ----------------------------------------------------------

    def fit(self, matrix: np.ndarray) -> np.ndarray:
        matrix = np.asarray(matrix, dtype=np.float32)
        rows = matrix.shape[0]

        if rows < self.params.min_umap_rows:
            logger.info("projecting %d rows with PCA (below UMAP threshold)", rows)
            self.method = "pca"
            from sklearn.decomposition import PCA

            n_components = min(self.params.n_components, rows, matrix.shape[1])
            reducer = PCA(
                n_components=n_components, random_state=self.params.random_state
            )
            coords = reducer.fit_transform(matrix)
            # Pad if the corpus is smaller than three dimensions allow.
            if coords.shape[1] < self.params.n_components:
                pad = self.params.n_components - coords.shape[1]
                coords = np.hstack([coords, np.zeros((rows, pad))])
        else:
            logger.info("fitting UMAP on %d rows", rows)
            self.method = "umap"
            import umap

            # Scaled to the corpus, then clamped below the row count. A fixed
            # 15 is a quarter of a sixty-paper library, and smoothing over a
            # quarter of the collection averages away the local structure the
            # map is made of.
            scaled = max(5, min(self.params.n_neighbors, round(rows / 8)))
            neighbors = min(scaled, max(2, rows - 1))
            reducer = umap.UMAP(
                n_components=self.params.n_components,
                n_neighbors=neighbors,
                min_dist=self.params.min_dist,
                metric=self.params.metric,
                random_state=self.params.random_state,
            )
            coords = reducer.fit_transform(matrix)

        self._reducer = reducer
        self.fit_matrix = matrix
        self.coords = np.asarray(coords, dtype=np.float32)
        return self.coords

    def transform(self, matrix: np.ndarray) -> np.ndarray:
        """Place new points using the existing fit. Approximate, but instant."""
        if not self.is_fitted or self._reducer is None:
            raise RuntimeError("projection is not fitted; call fit() first")

        matrix = np.asarray(matrix, dtype=np.float32)
        coords = self._reducer.transform(matrix)
        coords = np.asarray(coords, dtype=np.float32)

        if coords.shape[1] < self.params.n_components:
            pad = self.params.n_components - coords.shape[1]
            coords = np.hstack([coords, np.zeros((coords.shape[0], pad), np.float32)])
        return coords

    # --- drift ------------------------------------------------------------

    def off_manifold_score(self, matrix: np.ndarray) -> np.ndarray:
        """How far each point sits from the manifold the reducer learned.

        Mean cosine distance to its nearest neighbours in the fit matrix,
        normalised by the fit set's own median neighbour distance. Around 1.0
        means "as typical as the training data"; much higher means the paper
        belongs to a field the map has never seen, and ``transform`` is
        guessing.
        """
        if self.fit_matrix is None:
            raise RuntimeError("projection is not fitted; call fit() first")

        matrix = np.asarray(matrix, dtype=np.float32)
        reference = self.fit_matrix
        k = min(self.params.drift_neighbors, reference.shape[0])

        distances = _cosine_distance(matrix, reference)
        nearest = np.sort(distances, axis=1)[:, :k].mean(axis=1)

        baseline = getattr(self, "_drift_baseline", None)
        if baseline is None:
            self_distances = _cosine_distance(reference, reference)
            np.fill_diagonal(self_distances, np.inf)
            baseline = float(
                np.median(np.sort(self_distances, axis=1)[:, :k].mean(axis=1))
            )
            baseline = max(baseline, 1e-6)
            self._drift_baseline = baseline

        return (nearest / baseline).astype(np.float32)

    # --- persistence ------------------------------------------------------

    def save(self) -> None:
        self.base.parent.mkdir(parents=True, exist_ok=True)
        if self._reducer is not None:
            joblib.dump(self._reducer, self.reducer_path)
        if self.fit_matrix is not None:
            np.save(self.matrix_path, self.fit_matrix)
        if self.coords is not None:
            np.save(self.coords_path, self.coords)
        self.meta_path.write_text(
            json.dumps({"method": self.method, "params": asdict(self.params)})
        )

    @classmethod
    def load(cls, base_path: Path | str) -> ProjectionModel:
        base = Path(base_path)
        meta = json.loads(base.with_suffix(".meta.json").read_text())
        model = cls(base, ProjectionParams(**meta["params"]))
        model.method = meta["method"]

        if model.reducer_path.exists():
            model._reducer = joblib.load(model.reducer_path)
        if model.matrix_path.exists():
            model.fit_matrix = np.load(model.matrix_path)
        if model.coords_path.exists():
            model.coords = np.load(model.coords_path)
        return model


def _cosine_distance(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a_norm = a / np.clip(np.linalg.norm(a, axis=1, keepdims=True), 1e-12, None)
    b_norm = b / np.clip(np.linalg.norm(b, axis=1, keepdims=True), 1e-12, None)
    return 1.0 - (a_norm @ b_norm.T)
