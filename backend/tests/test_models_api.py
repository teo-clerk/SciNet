"""The Model Lab's API: one GET that tells the whole truth about models.

Discovery and residency are faked at the httpx seam (the canonical
duck-typed pattern); the catalog, pins, and resolution run for real against
the isolated database. The invariant most worth pinning: a pin set through
the API changes what resolve() answers for that task — and only that task.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, get_settings
from app.core.db import Base, build_engine, get_db
from app.core.model_store import PRIVATE_OLLAMA
from app.main import create_app
from app.services.models import discovery


@pytest.fixture
def client(tmp_path, monkeypatch):
    settings = Settings(
        data_dir=tmp_path,
        library_dir=tmp_path / "library",
        markdown_dir=tmp_path / "markdown",
        vectors_dir=tmp_path / "vectors",
        models_dir=tmp_path / "models",
        db_path=tmp_path / "m.db",
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

    # The two live seams, faked: what the Ollama stores hold, and what is
    # resident on the card right now. The router imported discover by value,
    # so the patch targets its copy.
    monkeypatch.setattr(
        "app.routers.models.discover",
        lambda settings=None: [
            discovery.DiscoveredModel("qwen3:8b", "configured", 5325, "Q4_K_M"),
            discovery.DiscoveredModel("llama3.2:3b", "system", 2048, "Q4_K_M"),
        ],
    )
    monkeypatch.setattr(PRIVATE_OLLAMA, "resident_models", lambda: ["qwen3:8b"])

    app = create_app()
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    engine.dispose()


def test_the_overview_carries_hardware_catalog_and_residency(client) -> None:
    body = client.get("/api/models").json()
    assert body["hardware"]["vram_budget_mib"] > 0
    # Seeded on first read: the registry's four plus the two rejected memos.
    references = {p["reference"] for p in body["profiles"]}
    assert {"qwen3:8b", "malteos/scincl", "qwen2.5vl:7b"} <= references
    assert body["resident"] == ["qwen3:8b"]
    assert body["tasks"]["tag_paper"] == "qwen3:8b"


def test_discovered_models_know_whether_the_catalog_has_them(client) -> None:
    discovered = {
        d["reference"]: d for d in client.get("/api/models").json()["discovered"]
    }
    assert discovered["qwen3:8b"]["in_catalog"] is True
    assert discovered["llama3.2:3b"]["in_catalog"] is False
    assert discovered["llama3.2:3b"]["server"] == "system"


def test_a_pin_set_through_the_api_changes_resolution(client) -> None:
    res = client.post("/api/models/pins/tag_paper", json={"reference": "llama3.2:3b"})
    assert res.status_code == 200

    body = client.get("/api/models").json()
    assert body["pins"] == {"tag_paper": "llama3.2:3b"}
    assert body["tasks"]["tag_paper"] == "llama3.2:3b"
    assert body["tasks"]["name_cluster"] != "llama3.2:3b", "no bleed"

    client.delete("/api/models/pins/tag_paper")
    assert client.get("/api/models").json()["pins"] == {}


def test_an_unpinnable_task_is_refused_with_a_409(client) -> None:
    res = client.post("/api/models/pins/embed_docs", json={"reference": "x/y"})
    assert res.status_code == 409
    assert "not pin-routable" in res.json()["detail"]


def test_an_unknown_task_is_a_404(client) -> None:
    assert (
        client.post("/api/models/pins/vibes", json={"reference": "x"}).status_code
        == 404
    )


def test_the_rejected_memo_is_visible_with_its_warning(client) -> None:
    profiles = {p["reference"]: p for p in client.get("/api/models").json()["profiles"]}
    rejected = profiles["qwen2.5vl:7b"]
    assert rejected["verdict"] == "cpu-only"
    assert rejected["fits_here"] is False
