"""Where to start reading.

The ranking is a claim about which paper a newcomer should open first, made
from four signals of very different reliability. Each test isolates one signal
by holding the others neutral, so a change in a weight fails the test that
names the signal rather than three others.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.models import Paper, PaperMeta, PaperStatus
from app.services.embed.store import VectorStore
from app.services.project.curriculum import (
    MAX_READING_ORDER,
    Candidate,
    intro_cue,
    rank_entry_points,
    reading_order,
)
from app.workers.embed_handlers import store_slug
from tests.test_graph_api import env, seed_map  # noqa: F401 - fixtures

DIM = 8


def unit(vector) -> np.ndarray:
    vector = np.asarray(vector, dtype=np.float64)
    return vector / np.linalg.norm(vector)


def axis(index: int, *, along: int = 0, amount: float = 0.0) -> np.ndarray:
    """A vector along ``along`` nudged ``amount`` toward another axis."""
    vector = np.zeros(DIM)
    vector[along] = 1.0
    vector[index] += amount
    return unit(vector)


def blank(paper_id: int, **fields) -> Candidate:
    """A candidate with every signal but centrality switched off."""
    defaults = {"title": None, "abstract": None, "year": None, "pages": None}
    return Candidate(paper_id=paper_id, **{**defaults, **fields})


# --- the signals, one at a time ------------------------------------------


def test_the_most_central_paper_wins_all_else_equal():
    """Centrality is the only signal every set has; alone, it decides."""
    centre = axis(0)
    matrix = np.vstack([centre] + [axis(k, amount=0.8) for k in range(4, 8)])
    candidates = [blank(i) for i in range(5)]

    ranked = rank_entry_points(candidates, matrix)

    assert ranked[0].paper_id == 0
    assert ranked[0].centrality > ranked[1].centrality


def test_a_survey_title_beats_a_slightly_more_central_paper():
    """A paper that says what it is for outranks one that is merely typical."""
    matrix = np.vstack(
        [axis(0), axis(4, amount=0.3)] + [axis(k, amount=0.8) for k in range(5, 8)]
    )
    candidates = [
        blank(0),
        blank(1, title="A Survey of Thermodynamic Cycles"),
        blank(2),
        blank(3),
        blank(4),
    ]

    ranked = rank_entry_points(candidates, matrix)

    assert ranked[0].paper_id == 1
    assert any("survey" in reason for reason in ranked[0].reasons)
    # The winner is genuinely less central, so the cue did the work.
    by_id = {s.paper_id: s for s in ranked}
    assert by_id[1].centrality < by_id[0].centrality


def test_unknown_year_and_pages_are_neutral_not_penalised():
    """An undated paper of unknown length scores as the midpoint, exactly as a
    paper dated to the middle of the span with a middling page count does."""
    same = np.vstack([axis(0)] * 4)
    candidates = [
        blank(0, year=2000, pages=165),  # the midpoint of both ranges
        blank(1, year=None, pages=None),
        blank(2, year=1990, pages=10),
        blank(3, year=2010, pages=400),
    ]

    by_id = {s.paper_id: s for s in rank_entry_points(candidates, same)}

    assert by_id[1].score == pytest.approx(by_id[0].score, abs=1e-4)
    assert by_id[1].score > by_id[3].score


def test_a_book_loses_to_a_primer_at_equal_centrality():
    same = np.vstack([axis(0)] * 3)
    candidates = [blank(0, pages=400), blank(1, pages=20), blank(2, pages=120)]

    ranked = rank_entry_points(candidates, same)

    assert [s.paper_id for s in ranked] == [1, 2, 0]


def test_ties_are_broken_deterministically():
    """Identical vectors and identical metadata: the lower id comes first."""
    same = np.vstack([axis(0)] * 3)
    ranked = rank_entry_points([blank(7), blank(3), blank(5)], same)
    assert [s.paper_id for s in ranked] == [3, 5, 7]


# --- the intro cue --------------------------------------------------------


@pytest.mark.parametrize(
    "title, word",
    [
        ("An Introduction to Category Theory", "introduction"),
        ("Lectures on Quantum Mechanics", "lectures on"),
        ("Deep Learning: A Review", "review"),
        ("What is Life?", "what is"),
    ],
)
def test_title_cues_score_a_full_point(title, word):
    assert intro_cue(title, None) == (1.0, word)


def test_an_abstract_cue_is_worth_half_a_title_cue():
    score, word = intro_cue("Curious Results", "In this paper we review the field.")
    assert score == 0.5
    assert word == "we review"


def test_a_cue_deep_in_the_abstract_does_not_count():
    """A paper describing itself does so at the top."""
    filler = "Results are presented. " * 40  # well past the 600-character window
    assert intro_cue("Curious Results", filler + "We review nothing.") == (0.0, None)


def test_review_inside_another_word_is_not_a_cue():
    assert intro_cue("Previewing the Data", None) == (0.0, None)


# --- reading order --------------------------------------------------------


def two_blobs(n_per: int = 5, seed: int = 0):
    """Two tight sub-topics, nearly orthogonal to each other."""
    rng = np.random.default_rng(seed)
    rows, blob_of = [], {}
    for blob, along in enumerate((0, 1)):
        for i in range(n_per):
            noise = np.zeros(DIM)
            noise[2:] = rng.standard_normal(DIM - 2) * 0.08
            base = np.zeros(DIM)
            base[along] = 1.0
            rows.append(unit(base + noise))
            blob_of[blob * n_per + i] = blob
    return np.vstack(rows), blob_of


def test_reading_order_starts_at_the_entry_point_and_finishes_a_blob_first():
    matrix, blob_of = two_blobs()
    candidates = [blank(i) for i in range(len(blob_of))]

    order = reading_order(candidates, matrix)
    ranked = rank_entry_points(candidates, matrix)

    assert order[0] == ranked[0].paper_id
    assert len(order) == len(candidates)
    blobs = [blob_of[pid] for pid in order]
    crossings = sum(1 for a, b in zip(blobs, blobs[1:], strict=False) if a != b)
    assert crossings == 1, blobs


def test_reading_order_is_capped_and_deterministic():
    rng = np.random.default_rng(3)
    matrix = rng.standard_normal((20, DIM))
    candidates = [blank(i) for i in range(20)]

    first = reading_order(candidates, matrix)
    second = reading_order(candidates, matrix)

    assert len(first) == MAX_READING_ORDER
    assert first == second
    assert len(set(first)) == len(first)
    assert len(reading_order(candidates, matrix, limit=5)) == 5


# --- reasons --------------------------------------------------------------


def test_reasons_read_as_plain_english():
    matrix = np.vstack([axis(0), axis(4, amount=0.6), axis(5, amount=0.9)])
    candidates = [
        blank(0, title="A Primer on Tides", year=1859, pages=18),
        blank(1, year=1950, pages=200),
        blank(2, year=2001, pages=500),
    ]

    winner = rank_entry_points(candidates, matrix)[0]

    assert winner.paper_id == 0
    assert winner.reasons  # something was worth saying
    for reason in winner.reasons:
        assert "_" not in reason
        assert reason[0].islower()
    joined = " ".join(winner.reasons)
    assert "'primer'" in joined
    assert "1859" in joined
    assert "18 pages" in joined


def test_a_paper_with_nothing_to_recommend_it_gets_no_reasons():
    """A reason list padded with faint praise is worse than an empty one."""
    matrix = np.vstack([axis(0), axis(4, amount=0.6), axis(5, amount=0.9)])
    candidates = [blank(0), blank(1), blank(2)]
    last = rank_entry_points(candidates, matrix)[-1]
    assert last.reasons == ()


# --- degenerate sets ------------------------------------------------------


def test_sets_below_two_yield_nothing():
    single = np.vstack([axis(0)])
    assert rank_entry_points([blank(0)], single) == []
    assert reading_order([blank(0)], single) == []
    assert rank_entry_points([], np.zeros((0, DIM))) == []
    assert reading_order([], np.zeros((0, DIM))) == []


def test_misaligned_rows_are_an_error_not_a_guess():
    with pytest.raises(ValueError):
        rank_entry_points([blank(0), blank(1)], np.vstack([axis(0)]))


# --- through the API ------------------------------------------------------


def seed_papers(factory, settings, specs):
    """Papers with metadata in the database and vectors in the store.

    ``specs`` is a list of ``(title, year, pages, vector)``; returns ids in
    the same order. Rows without a vector pass ``None`` for it.
    """
    store = VectorStore(
        settings.vectors_dir / f"doc_vectors__{store_slug(settings.embed_model)}",
        dim=settings.embed_dim,
        model_id=settings.embed_model,
    )
    ids = []
    with factory() as s:
        for i, (title, year, pages, vector) in enumerate(specs):
            paper = Paper(
                content_sha256=f"{i:064d}",
                work_key=f"w{i}",
                pdf_path=f"/tmp/{i}.pdf",
                status=PaperStatus.READY,
                pipeline_version=1,
                page_count=pages,
            )
            s.add(paper)
            s.flush()
            s.add(PaperMeta(paper_id=paper.id, title=title, year=year))
            if vector is not None:
                store.add(paper.id, vector.astype(np.float32))
            ids.append(paper.id)
        s.commit()
    store.flush()
    return ids


def test_a_cluster_without_vectors_has_no_entry_point(env):  # noqa: F811
    client, factory, _settings = env
    seed_map(factory, n=6)

    body = client.get("/api/clusters/1").json()

    assert body["entry_point"] is None
    assert body["alternatives"] == []
    assert body["reading_order"] == []
    assert len(body["members"]) == 6  # the rest of the detail is untouched


def test_entry_point_endpoint_ranks_a_set(env):  # noqa: F811
    client, factory, settings = env
    ids = seed_papers(
        factory,
        settings,
        [
            ("Dense Results IV", 2015, 40, axis(0)),
            ("A Survey of the Field", 2010, 30, axis(4, amount=0.3)),
            ("Dense Results V", 2018, 45, axis(5, amount=0.8)),
            ("Dense Results VI", 2019, 50, axis(6, amount=0.8)),
            ("Never embedded", 2020, 10, None),
        ],
    )

    res = client.post("/api/graph/entry-point", json={"paper_ids": ids})

    assert res.status_code == 200
    body = res.json()
    assert body["considered"] == 4
    assert body["entry_point"]["paper_id"] == ids[1]
    assert body["entry_point"]["title"] == "A Survey of the Field"
    assert any("survey" in r for r in body["entry_point"]["reasons"])
    assert len(body["alternatives"]) == 2
    assert body["reading_order"][0] == ids[1]
    assert set(body["reading_order"]) == set(ids[:4])


def test_entry_point_endpoint_needs_two_vectors(env):  # noqa: F811
    client, factory, settings = env
    ids = seed_papers(factory, settings, [("Alone", 2000, 10, axis(0))])

    body = client.post("/api/graph/entry-point", json={"paper_ids": ids}).json()

    assert body["entry_point"] is None
    assert body["considered"] == 1


def test_entry_point_endpoint_rejects_an_empty_set(env):  # noqa: F811
    client, _factory, _settings = env
    assert (
        client.post("/api/graph/entry-point", json={"paper_ids": []}).status_code == 400
    )
