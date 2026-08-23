"""The graph payload.

Shape matters as much as correctness here: this endpoint is what makes opening
the app instant, so it must stay small and must 304 on an unchanged map.
"""

from __future__ import annotations

import base64

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, get_settings
from app.core.db import Base, build_engine, get_db
from app.main import create_app
from app.models import (
    Cluster,
    Paper,
    PaperMeta,
    PaperStatus,
    PaperTag,
    Projection,
    ProjectionRun,
    Tag,
)


@pytest.fixture
def env(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        library_dir=tmp_path / "lib",
        markdown_dir=tmp_path / "md",
        vectors_dir=tmp_path / "vec",
        models_dir=tmp_path / "models",
        db_path=tmp_path / "g.db",
        embed_dim=8,
        embed_model="test-embed",
    )
    engine = build_engine(f"sqlite+pysqlite:///{settings.db_path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_db():
        s = factory()
        try:
            yield s
        finally:
            s.close()

    app = create_app()
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client, factory, settings
    app.dependency_overrides.clear()
    engine.dispose()


def seed_map(factory, n=12):
    with factory() as s:
        run = ProjectionRun(
            model_id="test-embed",
            params_json="{}",
            n_fit=n,
            method="pca",
            is_active=True,
        )
        s.add(run)
        s.flush()
        cluster = Cluster(
            run_id=run.id,
            hdbscan_label=0,
            size=n,
            top_terms_json='["graphs","molecules"]',
        )
        s.add(cluster)
        tag = Tag(slug="machine-learning", label="Machine Learning")
        s.add(tag)
        s.flush()

        for i in range(n):
            paper = Paper(
                content_sha256=f"{i:064d}",
                work_key=f"w{i}",
                pdf_path=f"/tmp/{i}.pdf",
                status=PaperStatus.READY,
                pipeline_version=1,
            )
            s.add(paper)
            s.flush()
            s.add(PaperMeta(paper_id=paper.id, title=f"Paper {i}", year=2020 + i % 5))
            s.add(
                Projection(
                    run_id=run.id,
                    paper_id=paper.id,
                    x=float(i),
                    y=float(-i),
                    z=0.5 * i,
                    is_transformed=(i >= n - 2),
                    off_manifold=0.1,
                    cluster_id=cluster.id,
                )
            )
            if i % 2 == 0:
                s.add(PaperTag(paper_id=paper.id, tag_id=tag.id, source="llm"))
        s.commit()
        return run.id


def test_no_projection_yet_is_a_409(env):
    client, _, _ = env
    assert client.get("/api/graph").status_code == 409


def test_positions_round_trip_as_float32(env):
    client, factory, _ = env
    seed_map(factory, n=12)
    body = client.get("/api/graph").json()

    raw = base64.b64decode(body["positions_f32"])
    positions = np.frombuffer(raw, dtype=np.float32).reshape(-1, 3)
    assert positions.shape == (12, 3)
    np.testing.assert_allclose(positions[3], [3.0, -3.0, 1.5], atol=1e-6)


def test_positions_align_with_node_order(env):
    client, factory, _ = env
    seed_map(factory, n=12)
    body = client.get("/api/graph").json()
    positions = np.frombuffer(
        base64.b64decode(body["positions_f32"]), dtype=np.float32
    ).reshape(-1, 3)
    assert len(body["nodes"]) == positions.shape[0]


def test_payload_omits_heavy_fields(env):
    """Abstracts are fetched per node; sending them all is what makes it slow."""
    client, factory, _ = env
    seed_map(factory)
    node = client.get("/api/graph").json()["nodes"][0]
    assert set(node) == {
        "id",
        "title",
        "year",
        "cluster",
        "tags",
        "pages",
        "confidence",
        "provisional",
        "drift",
    }
    # The expensive fields stay out: abstracts and summaries are fetched per
    # node on click, because shipping them for the whole corpus is what turns
    # a 50 ms map load into a multi-second one.
    assert "abstract" not in node
    assert "summary" not in node


def test_tags_are_integer_ids_against_one_vocabulary(env):
    client, factory, _ = env
    seed_map(factory)
    body = client.get("/api/graph").json()
    assert body["tag_vocabulary"] == ["machine-learning"]
    tagged = [n for n in body["nodes"] if n["tags"]]
    assert tagged and all(n["tags"] == [0] for n in tagged)


def test_provisional_papers_are_marked(env):
    """The UI needs to show which nodes were placed without a refit."""
    client, factory, _ = env
    seed_map(factory, n=12)
    body = client.get("/api/graph").json()
    assert sum(1 for n in body["nodes"] if n["provisional"]) == 2


def test_clusters_carry_their_terms(env):
    client, factory, _ = env
    seed_map(factory)
    clusters = client.get("/api/graph").json()["clusters"]
    assert clusters and clusters[0]["terms"] == ["graphs", "molecules"]


def test_unchanged_map_returns_304(env):
    """Reopening the app must cost nothing."""
    client, factory, _ = env
    seed_map(factory)
    first = client.get("/api/graph")
    etag = first.headers["etag"]

    second = client.get("/api/graph", headers={"If-None-Match": etag})
    assert second.status_code == 304
    assert second.content == b""


def test_etag_changes_when_a_paper_is_added(env):
    client, factory, settings = env
    run_id = seed_map(factory, n=12)
    etag = client.get("/api/graph").headers["etag"]

    with factory() as s:
        paper = Paper(
            content_sha256="f" * 64,
            work_key="wnew",
            pdf_path="/tmp/new.pdf",
            status=PaperStatus.READY,
            pipeline_version=1,
        )
        s.add(paper)
        s.flush()
        s.add(
            Projection(
                run_id=run_id,
                paper_id=paper.id,
                x=1,
                y=1,
                z=1,
                is_transformed=True,
                off_manifold=0.2,
            )
        )
        s.commit()

    assert client.get("/api/graph").headers["etag"] != etag


def test_similar_requires_an_embedding(env):
    client, factory, _ = env
    seed_map(factory)
    assert client.get("/api/graph/similar/1").status_code == 404


def test_reproject_enqueues_one_job(env):
    client, _factory, _settings = env

    body = client.post("/api/graph/reproject").json()

    assert body["state"] == "queued"
    assert isinstance(body["job_id"], int)


def test_pressing_reproject_repeatedly_does_not_pile_up_refits(env):
    """A refit is tens of seconds of UMAP; four presses must not mean four."""
    client, _factory, _settings = env

    first = client.post("/api/graph/reproject").json()
    second = client.post("/api/graph/reproject").json()
    third = client.post("/api/graph/reproject").json()

    assert first["job_id"] == second["job_id"] == third["job_id"]


def test_the_queued_job_actually_asks_for_a_refit(env):
    """Without the payload it takes the cheap path and changes nothing."""
    import json

    from app.models import Job

    client, factory, _settings = env
    job_id = client.post("/api/graph/reproject").json()["job_id"]

    with factory() as session:
        assert json.loads(session.get(Job, job_id).payload_json) == {
            "force_refit": True
        }
