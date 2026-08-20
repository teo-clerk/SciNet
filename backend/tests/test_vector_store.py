"""The document-vector memmap.

This is the persistence layer the map is rebuilt from. It has to survive a
process restart, grow without rewriting itself, and never hand back a vector
that belongs to a different embedding model.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.services.embed.store import VectorStore


@pytest.fixture
def store(tmp_path):
    return VectorStore(tmp_path / "vec", dim=8, model_id="test-model")


def vec(seed: int, dim: int = 8) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    return v / np.linalg.norm(v)


# --- basics ---------------------------------------------------------------


def test_new_store_is_empty(store):
    assert store.count == 0


def test_add_returns_a_row_index(store):
    assert store.add(1, vec(1)) == 0
    assert store.add(2, vec(2)) == 1


def test_vector_round_trips_exactly(store):
    v = vec(7)
    store.add(42, v)
    np.testing.assert_array_equal(store.get(42), v)


def test_unknown_paper_returns_none(store):
    assert store.get(999) is None


def test_readding_a_paper_overwrites_in_place(store):
    first = store.add(1, vec(1))
    second = store.add(1, vec(2))
    assert first == second, "re-embedding must not leak a row"
    assert store.count == 1
    np.testing.assert_array_equal(store.get(1), vec(2))


def test_rejects_wrong_dimension(store):
    with pytest.raises(ValueError, match="dimension"):
        store.add(1, np.zeros(3, dtype=np.float32))


# --- persistence ----------------------------------------------------------


def test_survives_reopening(tmp_path):
    a = VectorStore(tmp_path / "vec", dim=8, model_id="m")
    a.add(1, vec(1))
    a.add(2, vec(2))
    a.flush()

    b = VectorStore(tmp_path / "vec", dim=8, model_id="m")
    assert b.count == 2
    np.testing.assert_array_equal(b.get(2), vec(2))


def test_reopening_with_a_different_model_refuses(tmp_path):
    """Vectors from two models are not comparable; silently mixing them would
    corrupt the map in a way no test downstream would catch."""
    a = VectorStore(tmp_path / "vec", dim=8, model_id="model-a")
    a.add(1, vec(1))
    a.flush()

    with pytest.raises(ValueError, match="model"):
        VectorStore(tmp_path / "vec", dim=8, model_id="model-b")


def test_reopening_with_a_different_dimension_refuses(tmp_path):
    a = VectorStore(tmp_path / "vec", dim=8, model_id="m")
    a.add(1, vec(1))
    a.flush()

    with pytest.raises(ValueError, match="dimension"):
        VectorStore(tmp_path / "vec", dim=16, model_id="m")


def test_grows_past_initial_capacity(tmp_path):
    s = VectorStore(tmp_path / "vec", dim=8, model_id="m", initial_capacity=4)
    for i in range(50):
        s.add(i, vec(i))
    assert s.count == 50
    np.testing.assert_array_equal(s.get(49), vec(49))
    np.testing.assert_array_equal(s.get(0), vec(0))


def test_growth_survives_a_reopen(tmp_path):
    s = VectorStore(tmp_path / "vec", dim=8, model_id="m", initial_capacity=2)
    for i in range(20):
        s.add(i, vec(i))
    s.flush()

    reopened = VectorStore(tmp_path / "vec", dim=8, model_id="m")
    assert reopened.count == 20
    np.testing.assert_array_equal(reopened.get(17), vec(17))


# --- matrix access --------------------------------------------------------


def test_matrix_has_one_row_per_paper(store):
    for i in range(5):
        store.add(i, vec(i))
    matrix, ids = store.matrix()
    assert matrix.shape == (5, 8)
    assert ids == [0, 1, 2, 3, 4]


def test_matrix_rows_align_with_ids(store):
    for i in (10, 20, 30):
        store.add(i, vec(i))
    matrix, ids = store.matrix()
    for row, paper_id in enumerate(ids):
        np.testing.assert_array_equal(matrix[row], vec(paper_id))


def test_matrix_of_an_empty_store(store):
    matrix, ids = store.matrix()
    assert matrix.shape == (0, 8)
    assert ids == []


def test_matrix_can_be_restricted_to_a_subset(store):
    for i in range(5):
        store.add(i, vec(i))
    matrix, ids = store.matrix(paper_ids=[3, 1])
    assert ids == [3, 1]
    np.testing.assert_array_equal(matrix[0], vec(3))


# --- similarity -----------------------------------------------------------


def test_nearest_finds_the_query_itself_first(store):
    for i in range(20):
        store.add(i, vec(i))
    hits = store.nearest(vec(7), k=3)
    assert hits[0][0] == 7
    assert hits[0][1] == pytest.approx(1.0, abs=1e-5)


def test_nearest_is_ordered_by_descending_similarity(store):
    for i in range(20):
        store.add(i, vec(i))
    scores = [s for _, s in store.nearest(vec(3), k=5)]
    assert scores == sorted(scores, reverse=True)


def test_nearest_on_an_empty_store_is_empty(store):
    assert store.nearest(vec(1), k=5) == []


def test_nearest_can_exclude_a_paper(store):
    for i in range(10):
        store.add(i, vec(i))
    hits = store.nearest(vec(4), k=3, exclude={4})
    assert 4 not in [pid for pid, _ in hits]


# --- removal --------------------------------------------------------------


def test_removing_a_paper_hides_it(store):
    store.add(1, vec(1))
    store.add(2, vec(2))
    store.remove(1)
    assert store.get(1) is None
    assert store.count == 1
    _, ids = store.matrix()
    assert ids == [2]
