"""Persistence and incremental updates, end to end against the database.

This is the behaviour the whole design is arranged around: opening the app must
never refit, and dropping a handful of papers in must place them without
touching the rest of the corpus.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.core.config import Settings
from app.models import Cluster, Paper, PaperMeta, PaperStatus, Projection, ProjectionRun
from app.services.embed.store import VectorStore
from app.services.project.pipeline import (
    ProjectionOutcome,
    active_run,
    project_corpus,
    should_refit,
)

DIM = 16


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        library_dir=tmp_path / "library",
        markdown_dir=tmp_path / "markdown",
        vectors_dir=tmp_path / "vectors",
        models_dir=tmp_path / "models",
        db_path=tmp_path / "t.db",
        embed_dim=DIM,
        embed_model="test-embed",
        umap_min_papers=1000,  # keep the tests on PCA: fast and deterministic
    )


def make_corpus(db, store, n=60, seed=0, start=0):
    """n papers in three blobs, with vectors in the store."""
    rng = np.random.default_rng(seed)
    centres = rng.standard_normal((3, DIM)) * 6
    ids = []
    for i in range(n):
        paper = Paper(
            content_sha256=f"{start + i:064d}",
            work_key=f"w{start + i}",
            pdf_path=f"/tmp/{start + i}.pdf",
            status=PaperStatus.EMBEDDED,
            pipeline_version=1,
        )
        db.add(paper)
        db.flush()
        db.add(PaperMeta(paper_id=paper.id, title=f"Paper about topic {i % 3}"))
        vec = centres[i % 3] + rng.standard_normal(DIM) * 0.5
        vec = (vec / np.linalg.norm(vec)).astype(np.float32)
        store.add(paper.id, vec)
        ids.append(paper.id)
    db.flush()
    store.flush()
    return ids


@pytest.fixture
def store(settings):
    settings.ensure_dirs()
    return VectorStore(
        settings.vectors_dir / "doc_vectors", dim=DIM, model_id="test-embed"
    )


# --- first run ------------------------------------------------------------


def test_first_projection_fits_and_activates(db, settings, store):
    ids = make_corpus(db, store)
    outcome = project_corpus(db, settings, store)
    db.commit()

    assert outcome.refitted
    assert outcome.total == len(ids)
    run = active_run(db)
    assert run is not None and run.id == outcome.run_id
    assert db.query(Projection).filter(Projection.run_id == run.id).count() == len(ids)


def test_every_paper_gets_finite_coordinates(db, settings, store):
    make_corpus(db, store)
    project_corpus(db, settings, store)
    db.commit()

    for p in db.query(Projection).all():
        assert all(np.isfinite([p.x, p.y, p.z]))


def test_fitted_papers_are_not_marked_transformed(db, settings, store):
    make_corpus(db, store)
    project_corpus(db, settings, store)
    db.commit()
    assert db.query(Projection).filter(Projection.is_transformed.is_(True)).count() == 0


# --- the incremental path -------------------------------------------------


def test_new_papers_are_placed_without_refitting(db, settings, store):
    """The headline behaviour: five new papers, no refit."""
    make_corpus(db, store, n=60)
    first = project_corpus(db, settings, store)
    db.commit()

    make_corpus(db, store, n=5, seed=99, start=1000)
    second = project_corpus(db, settings, store)
    db.commit()

    assert not second.refitted, "adding 5 papers must not trigger a refit"
    assert second.run_id == first.run_id, "the map must stay the same run"
    assert second.placed == 5


def test_incrementally_placed_papers_are_flagged(db, settings, store):
    make_corpus(db, store, n=60)
    project_corpus(db, settings, store)
    db.commit()
    make_corpus(db, store, n=5, seed=99, start=1000)
    project_corpus(db, settings, store)
    db.commit()

    transformed = db.query(Projection).filter(Projection.is_transformed.is_(True))
    assert transformed.count() == 5
    for row in transformed:
        assert row.off_manifold is not None


def test_existing_coordinates_are_untouched_by_an_insert(db, settings, store):
    """Nothing already on the map may move when a paper is added."""
    ids = make_corpus(db, store, n=60)
    project_corpus(db, settings, store)
    db.commit()
    before = {
        p.paper_id: (p.x, p.y, p.z)
        for p in db.query(Projection).filter(Projection.paper_id.in_(ids))
    }

    make_corpus(db, store, n=5, seed=99, start=1000)
    project_corpus(db, settings, store)
    db.commit()

    after = {
        p.paper_id: (p.x, p.y, p.z)
        for p in db.query(Projection).filter(Projection.paper_id.in_(ids))
    }
    assert before == after


def test_reprojecting_with_nothing_new_is_a_noop(db, settings, store):
    make_corpus(db, store, n=60)
    project_corpus(db, settings, store)
    db.commit()

    outcome = project_corpus(db, settings, store)
    db.commit()
    assert outcome.placed == 0
    assert not outcome.refitted


# --- refit triggers -------------------------------------------------------


def test_refit_fires_once_too_much_was_placed_incrementally(db, settings, store):
    make_corpus(db, store, n=40)
    project_corpus(db, settings, store)
    db.commit()

    # 20 more against 40 fitted is well past the 20% threshold.
    make_corpus(db, store, n=20, seed=7, start=2000)
    project_corpus(db, settings, store)  # places them incrementally
    db.commit()

    outcome = project_corpus(db, settings, store)  # now the trigger fires
    db.commit()
    assert outcome.refitted
    assert "incrementally" in outcome.reason


def test_force_refit_is_honoured(db, settings, store):
    make_corpus(db, store, n=60)
    first = project_corpus(db, settings, store)
    db.commit()

    outcome = project_corpus(db, settings, store, force_refit=True)
    db.commit()
    assert outcome.refitted
    assert outcome.run_id != first.run_id


def test_should_refit_when_there_is_no_active_run(db):
    refit, reason = should_refit(db, None)
    assert refit and "no active projection" in reason


# --- atomic swap ----------------------------------------------------------


def test_exactly_one_run_is_active_after_a_refit(db, settings, store):
    make_corpus(db, store, n=60)
    project_corpus(db, settings, store)
    db.commit()
    project_corpus(db, settings, store, force_refit=True)
    db.commit()

    active = db.query(ProjectionRun).filter(ProjectionRun.is_active.is_(True)).all()
    assert len(active) == 1


def test_the_previous_run_survives_a_refit(db, settings, store):
    """Keeping the old layout is what makes a rollback or an A/B possible."""
    make_corpus(db, store, n=60)
    first = project_corpus(db, settings, store)
    db.commit()
    project_corpus(db, settings, store, force_refit=True)
    db.commit()

    old = db.get(ProjectionRun, first.run_id)
    assert old is not None and not old.is_active
    assert db.query(Projection).filter(Projection.run_id == first.run_id).count() > 0


def test_refit_records_procrustes_displacement(db, settings, store):
    make_corpus(db, store, n=60)
    project_corpus(db, settings, store)
    db.commit()

    outcome = project_corpus(db, settings, store, force_refit=True)
    db.commit()
    assert outcome.mean_displacement is not None
    assert outcome.mean_displacement >= 0.0


# --- clustering -----------------------------------------------------------


def test_clusters_are_written_and_linked(db, settings, store):
    make_corpus(db, store, n=90)
    outcome = project_corpus(db, settings, store)
    db.commit()

    if outcome.clusters:
        clusters = db.query(Cluster).filter(Cluster.run_id == outcome.run_id).all()
        assert len(clusters) == outcome.clusters
        assigned = (
            db.query(Projection)
            .filter(
                Projection.run_id == outcome.run_id,
                Projection.cluster_id.isnot(None),
            )
            .count()
        )
        assert assigned > 0


def test_projecting_an_empty_corpus_raises(db, settings, store):
    with pytest.raises(ValueError, match="no document vectors"):
        project_corpus(db, settings, store)


def test_outcome_shape(db, settings, store):
    make_corpus(db, store, n=60)
    outcome = project_corpus(db, settings, store)
    assert isinstance(outcome, ProjectionOutcome)
    assert outcome.method in {"pca", "umap"}
