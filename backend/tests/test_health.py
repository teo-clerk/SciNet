"""API smoke tests.

Deliberately bound to an isolated database. An earlier version of this file
used the application's real engine, which meant the assertions passed or failed
depending on whatever happened to be in the developer's library — a test that
reads live state is not a test.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, get_settings
from app.core.db import Base, build_engine, get_db
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        library_dir=tmp_path / "library",
        markdown_dir=tmp_path / "markdown",
        vectors_dir=tmp_path / "vectors",
        models_dir=tmp_path / "models",
        db_path=tmp_path / "api.db",
        enrichment_enabled=False,
    )
    engine = build_engine(f"sqlite+pysqlite:///{settings.db_path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app = create_app()
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings

    with TestClient(app) as c:
        c.scinet_engine = engine
        yield c

    app.dependency_overrides.clear()
    engine.dispose()


def test_health_ok(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_system_reports_local_only_by_default(client):
    res = client.get("/api/system")
    assert res.status_code == 200
    body = res.json()
    assert body["enrichment_enabled"] is False, "network egress must be off by default"
    assert body["paper_count"] == 0


def test_system_reports_model_health(client):
    """The UI needs to show that a model will run on CPU before a job stalls."""
    body = client.get("/api/system").json()
    roles = {m["role"] for m in body["models"]}
    assert {"ocr", "vision", "embed", "tag"} <= roles

    for model in body["models"]:
        assert "reference" in model
        assert "quantization" in model
        # fits_vram may be None: unmeasured is reported as unproven, not as ok.
        assert model["fits_vram"] in (True, False, None)


def test_papers_endpoint_is_empty_on_a_fresh_database(client):
    body = client.get("/api/papers").json()
    assert body["total"] == 0
    assert body["items"] == []


def test_jobs_endpoint_reports_zero_counts(client):
    body = client.get("/api/jobs").json()
    assert body["queued"] == 0
    assert body["running"] == 0


def test_unknown_paper_is_404(client):
    assert client.get("/api/papers/99999").status_code == 404


def test_sqlite_pragmas_applied(client):
    """WAL and foreign keys keep the API and worker out of each other's way."""
    with client.scinet_engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
