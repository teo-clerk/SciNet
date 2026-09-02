"""A second projection never moves the first.

The M2 invariant, extended to alternates: building a run under another
embedding model must leave the active run's coordinates byte-identical —
the alt is aligned *onto* the active layout, stored inactive, and served
only when asked for by id.
"""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, get_settings
from app.core.db import Base, build_engine, get_db
from app.main import create_app
from app.models import Paper, PaperMeta, Projection, ProjectionRun
from app.models.enums import PaperStatus
from app.services.project.alt_run import build_alt_run

N_PAPERS = 10


@pytest.fixture
def env(tmp_path, monkeypatch):
    # TestClient runs the lifespan, and the lifespan dispatches the real
    # embedding warm-up thread — which would try to load "test-embed" and
    # poison the process-global WARMER to FAILED for every later test.
    from app.core import warmup

    monkeypatch.setattr(warmup.WARMER, "start", lambda: None)

    settings = Settings(
        data_dir=tmp_path,
        library_dir=tmp_path / "library",
        markdown_dir=tmp_path / "markdown",
        vectors_dir=tmp_path / "vectors",
        models_dir=tmp_path / "models",
        db_path=tmp_path / "a.db",
        embed_dim=8,
        embed_model="test-embed",
    )
    settings.vectors_dir.mkdir(parents=True, exist_ok=True)
    engine = build_engine(f"sqlite+pysqlite:///{settings.db_path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    rng = np.random.default_rng(7)
    with factory() as session:
        run = ProjectionRun(
            model_id="test-embed",
            params_json="{}",
            n_fit=N_PAPERS,
            method="pca",
            is_active=True,
        )
        session.add(run)
        session.flush()
        for i in range(1, N_PAPERS + 1):
            session.add(
                Paper(
                    id=i,
                    content_sha256=f"{i:064d}",
                    work_key=f"w-{i}",
                    pdf_path=str(tmp_path / f"{i}.pdf"),
                    status=PaperStatus.READY,
                )
            )
            session.add(PaperMeta(paper_id=i, title=f"Paper number {i}"))
            x, y, z = rng.normal(0, 10, size=3)
            session.add(
                Projection(
                    run_id=run.id, paper_id=i, x=float(x), y=float(y), z=float(z)
                )
            )
        session.commit()
        active_id = run.id

    yield settings, factory, active_id
    engine.dispose()


def fake_encode(texts, reference, settings):
    """Deterministic 8-dim vectors; one distinctive direction per paper."""
    rng = np.random.default_rng(11)
    return rng.normal(0, 1, size=(len(texts), 8)).astype(np.float32)


def snapshot(factory, run_id):
    with factory() as s:
        return {
            r.paper_id: (r.x, r.y, r.z)
            for r in s.scalars(select(Projection).where(Projection.run_id == run_id))
        }


def test_the_active_run_is_byte_identical_after_a_build(env) -> None:
    settings, factory, active_id = env
    before = snapshot(factory, active_id)
    with factory() as session:
        build_alt_run(session, settings, "other/embed", encode=fake_encode)
        session.commit()
    assert snapshot(factory, active_id) == before


def test_the_alt_run_is_inactive_aligned_and_complete(env) -> None:
    settings, factory, active_id = env
    with factory() as session:
        result = build_alt_run(session, settings, "other/embed", encode=fake_encode)
        session.commit()

    with factory() as session:
        run = session.get(ProjectionRun, result.run_id)
        assert run.is_active is False
        assert run.model_id == "other/embed"
        rows = session.scalars(
            select(Projection).where(Projection.run_id == run.id)
        ).all()
    assert len(rows) == N_PAPERS
    assert result.rows == N_PAPERS and result.dim == 8
    assert np.isfinite(result.mean_displacement)


def test_the_api_serves_a_retained_run_by_id(env) -> None:
    settings, factory, active_id = env
    with factory() as session:
        result = build_alt_run(session, settings, "other/embed", encode=fake_encode)
        session.commit()

    app = create_app()

    def override_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        runs = client.get("/api/graph/runs").json()
        assert [r["is_active"] for r in runs] == [False, True]
        assert runs[0]["model_id"] == "other/embed"

        alt = client.get("/api/graph", params={"run_id": result.run_id}).json()
        assert alt["run_id"] == result.run_id and alt["count"] == N_PAPERS

        active = client.get("/api/graph").json()
        assert active["run_id"] == active_id

        assert client.get("/api/graph", params={"run_id": 4242}).status_code == 404
    app.dependency_overrides.clear()
