"""The quantities API serves only what earned trust, and review moves rows.

Seeded with one row per status; the invariant across every read endpoint:
rejected and unadjudicated guesses never reach a filter that looks
authoritative.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, get_settings
from app.core.db import Base, build_engine, get_db
from app.main import create_app
from app.models import Paper, PaperMeta, Quantity, QuantityStatus
from app.models.enums import PaperStatus


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        library_dir=tmp_path / "library",
        markdown_dir=tmp_path / "markdown",
        vectors_dir=tmp_path / "vectors",
        models_dir=tmp_path / "models",
        db_path=tmp_path / "q.db",
    )
    engine = build_engine(f"sqlite+pysqlite:///{settings.db_path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory() as session:
        for i in (1, 2):
            session.add(
                Paper(
                    id=i,
                    content_sha256=f"{i:064d}",
                    work_key=f"w{i}",
                    pdf_path=str(tmp_path / f"{i}.pdf"),
                    status=PaperStatus.READY,
                )
            )
            session.add(PaperMeta(paper_id=i, title=f"Paper {i}"))

        def q(paper_id, value, status, kind="length"):
            return Quantity(
                paper_id=paper_id,
                chunk_ord=0,
                section="Results",
                quantity_kind=kind,
                value_si=value,
                unit_si="meter",
                value_original=str(value),
                unit_original="m",
                context_sentence=f"It spans {value} m in the field of view.",
                confidence=0.95,
                status=status,
            )

        session.add_all(
            [
                q(1, 500.0, QuantityStatus.AUTO),
                q(1, 900.0, QuantityStatus.CONFIRMED),
                q(2, 100.0, QuantityStatus.REJECTED),
                q(2, 700.0, QuantityStatus.PENDING_REVIEW),
            ]
        )
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
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    engine.dispose()


def test_kinds_summarise_only_trusted_rows(client) -> None:
    kinds = client.get("/api/quantities/kinds").json()
    assert len(kinds) == 1
    summary = kinds[0]
    assert summary["kind"] == "length" and summary["count"] == 2
    assert summary["min_value"] == 500.0 and summary["max_value"] == 900.0


def test_range_search_returns_paper_ids_for_the_visible_set(client) -> None:
    body = client.get(
        "/api/quantities/search",
        params={"kind": "length", "min_value": 400, "max_value": 1000},
    ).json()
    assert body["paper_ids"] == [1]
    narrow = client.get(
        "/api/quantities/search", params={"kind": "length", "min_value": 950}
    ).json()
    assert narrow["paper_ids"] == []


def test_a_papers_rows_include_pending_but_never_rejected(client) -> None:
    rows = client.get("/api/quantities/paper/2").json()
    assert [r["status"] for r in rows] == ["pending_review"]


def test_review_confirms_into_the_filterable_set(client) -> None:
    queue = client.get("/api/quantities/review").json()
    assert len(queue) == 1 and queue[0]["paper_title"] == "Paper 2"

    row_id = queue[0]["id"]
    client.post(f"/api/quantities/{row_id}/review", json={"verdict": "confirm"})

    body = client.get(
        "/api/quantities/search", params={"kind": "length", "min_value": 600}
    ).json()
    assert body["paper_ids"] == [1, 2], "confirmed rows join the filter"
    assert client.get("/api/quantities/review").json() == []


def test_a_rejection_stays_out_and_bad_verdicts_are_400(client) -> None:
    queue_before = client.get(
        "/api/quantities/search", params={"kind": "length"}
    ).json()
    res = client.post("/api/quantities/999/review", json={"verdict": "confirm"})
    assert res.status_code == 404
    res = client.post(
        f"/api/quantities/{queue_before['paper_ids'][0]}/review",
        json={"verdict": "maybe"},
    )
    assert res.status_code == 400
