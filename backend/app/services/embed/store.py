"""Persistent document-vector store, backed by a growable memmap.

This is what makes the map survive a restart. Vectors live in a flat float32
file beside a small JSON header; the header records which embedding model wrote
them, because vectors from two different models are not comparable and mixing
them would corrupt the map in a way nothing downstream would notice.

At this scale a memmap really is the right structure: 4,000 x 1024 float32 is
16 MB, brute-force cosine over the whole corpus is a couple of milliseconds,
and an ANN index would add a moving part for no gain. The design assumes tens
of thousands of documents, not millions.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_CAPACITY = 1024
GROWTH_FACTOR = 2


class VectorStore:
    """Row-addressed float32 vectors with a paper_id -> row index.

    The index is kept in the header rather than derived from the database so
    the store is self-describing: it can be opened, inspected and rebuilt from
    without a SQLite connection.
    """

    def __init__(
        self,
        base_path: Path | str,
        *,
        dim: int,
        model_id: str,
        initial_capacity: int = DEFAULT_CAPACITY,
    ) -> None:
        self.base = Path(base_path)
        self.data_path = self.base.with_suffix(".f32")
        self.meta_path = self.base.with_suffix(".meta.json")
        self.dim = dim
        self.model_id = model_id

        self._index: dict[int, int] = {}
        self._free: list[int] = []
        self._capacity = initial_capacity
        self._memmap: np.memmap | None = None

        self.base.parent.mkdir(parents=True, exist_ok=True)
        if self.meta_path.exists():
            self._load_header()
        else:
            self._create(initial_capacity)

    # --- lifecycle --------------------------------------------------------

    def _load_header(self) -> None:
        meta = json.loads(self.meta_path.read_text())

        if meta["dim"] != self.dim:
            raise ValueError(
                f"store at {self.base} has dimension {meta['dim']}, expected {self.dim}"
            )
        if meta["model_id"] != self.model_id:
            raise ValueError(
                f"store at {self.base} was written by model {meta['model_id']!r}, "
                f"not {self.model_id!r}; re-embed or point at a different path"
            )

        self._capacity = meta["capacity"]
        self._index = {int(k): v for k, v in meta["index"].items()}
        self._free = list(meta.get("free", []))
        self._open_memmap()

    def _create(self, capacity: int) -> None:
        self._capacity = max(capacity, 1)
        # Allocate the backing file up front so the memmap has something to map.
        with open(self.data_path, "wb") as fh:
            fh.truncate(self._capacity * self.dim * 4)
        self._open_memmap()
        self._write_header()

    def _open_memmap(self) -> None:
        self._memmap = np.memmap(
            self.data_path,
            dtype=np.float32,
            mode="r+",
            shape=(self._capacity, self.dim),
        )

    def _write_header(self) -> None:
        payload = {
            "dim": self.dim,
            "model_id": self.model_id,
            "capacity": self._capacity,
            "count": len(self._index),
            "index": {str(k): v for k, v in self._index.items()},
            "free": self._free,
        }
        # Written via a temporary file: a half-written header would orphan every
        # vector in the store.
        tmp = self.meta_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload))
        os.replace(tmp, self.meta_path)

    def _grow(self) -> None:
        new_capacity = self._capacity * GROWTH_FACTOR
        logger.info("growing vector store %d -> %d rows", self._capacity, new_capacity)
        if self._memmap is not None:
            self._memmap.flush()
            del self._memmap
            self._memmap = None
        with open(self.data_path, "r+b") as fh:
            fh.truncate(new_capacity * self.dim * 4)
        self._capacity = new_capacity
        self._open_memmap()

    def flush(self) -> None:
        if self._memmap is not None:
            self._memmap.flush()
        self._write_header()

    # --- writing ----------------------------------------------------------

    @property
    def count(self) -> int:
        return len(self._index)

    def add(self, paper_id: int, vector: np.ndarray) -> int:
        """Store a vector, returning its row. Re-adding overwrites in place."""
        vector = np.asarray(vector, dtype=np.float32).ravel()
        if vector.shape[0] != self.dim:
            raise ValueError(
                f"vector has dimension {vector.shape[0]}, expected {self.dim}"
            )

        row = self._index.get(paper_id)
        if row is None:
            row = self._free.pop() if self._free else len(self._index)
            while row >= self._capacity:
                self._grow()
            self._index[paper_id] = row

        assert self._memmap is not None
        self._memmap[row] = vector
        self._write_header()
        return row

    def add_many(self, items: list[tuple[int, np.ndarray]]) -> list[int]:
        """Batch insert; the header is written once rather than per vector."""
        rows = []
        for paper_id, vector in items:
            vector = np.asarray(vector, dtype=np.float32).ravel()
            if vector.shape[0] != self.dim:
                raise ValueError(
                    f"vector has dimension {vector.shape[0]}, expected {self.dim}"
                )
            row = self._index.get(paper_id)
            if row is None:
                row = self._free.pop() if self._free else len(self._index)
                while row >= self._capacity:
                    self._grow()
                self._index[paper_id] = row
            assert self._memmap is not None
            self._memmap[row] = vector
            rows.append(row)
        self.flush()
        return rows

    def remove(self, paper_id: int) -> None:
        row = self._index.pop(paper_id, None)
        if row is None:
            return
        # The row is recycled rather than compacted: compaction would renumber
        # every row and invalidate the projection's fit matrix.
        self._free.append(row)
        self._write_header()

    # --- reading ----------------------------------------------------------

    def get(self, paper_id: int) -> np.ndarray | None:
        row = self._index.get(paper_id)
        if row is None:
            return None
        assert self._memmap is not None
        return np.array(self._memmap[row])

    def matrix(
        self, paper_ids: list[int] | None = None
    ) -> tuple[np.ndarray, list[int]]:
        """Dense matrix plus the paper ids its rows correspond to.

        Row order is the caller's, or insertion order. Never rely on row index
        equalling the store's internal row.
        """
        ids = (
            [pid for pid in paper_ids if pid in self._index]
            if paper_ids is not None
            else list(self._index.keys())
        )
        if not ids:
            return np.zeros((0, self.dim), dtype=np.float32), []

        assert self._memmap is not None
        rows = [self._index[pid] for pid in ids]
        return np.array(self._memmap[rows]), ids

    def nearest(
        self, query: np.ndarray, k: int = 10, exclude: set[int] | None = None
    ) -> list[tuple[int, float]]:
        """Cosine similarity over the whole store.

        Brute force on purpose. This runs in the original embedding space, which
        is the only space where distances mean anything — the 3-D projection
        distorts them deliberately.
        """
        matrix, ids = self.matrix()
        if not ids:
            return []

        query = np.asarray(query, dtype=np.float32).ravel()
        query_norm = np.linalg.norm(query)
        if query_norm == 0:
            return []

        norms = np.linalg.norm(matrix, axis=1)
        norms[norms == 0] = 1e-12
        scores = (matrix @ query) / (norms * query_norm)

        order = np.argsort(-scores)
        hits: list[tuple[int, float]] = []
        for position in order:
            paper_id = ids[position]
            if exclude and paper_id in exclude:
                continue
            hits.append((paper_id, float(scores[position])))
            if len(hits) >= k:
                break
        return hits
