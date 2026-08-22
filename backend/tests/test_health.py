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


def _add_paper(client, *, abstract: str, source: str) -> int:
    """A ready paper with a known abstract provenance."""
    import json

    from sqlalchemy.orm import sessionmaker

    from app.models import Paper, PaperMeta, PaperStatus

    factory = sessionmaker(bind=client.scinet_engine, expire_on_commit=False)
    with factory() as session:
        paper = Paper(
            content_sha256="a" * 64,
            work_key="w" * 16,
            pdf_path="/library/book.epub",
            pdf_bytes=1024,
            status=PaperStatus.READY,
            pipeline_version=1,
        )
        session.add(paper)
        session.flush()
        session.add(
            PaperMeta(
                paper_id=paper.id,
                title="A Book",
                abstract=abstract,
                authors_json="[]",
                field_sources_json=json.dumps({"abstract": source}),
            )
        )
        session.commit()
        return paper.id


def test_an_assembled_abstract_is_disclosed_as_one(client):
    """A book has no abstract, so one may have been built from its own text.

    Presented without saying so it reads as the author's summary of their own
    work, which is exactly what it is not.
    """
    paper_id = _add_paper(
        client, abstract="Assembled from the chapters.", source="extracted_digest"
    )

    body = client.get(f"/api/papers/{paper_id}").json()

    assert body["abstract"] == "Assembled from the chapters."
    assert body["abstract_source"] == "extracted_digest"


def test_an_authored_abstract_is_not_labelled_as_assembled(client):
    paper_id = _add_paper(
        client, abstract="We propose a new architecture.", source="heuristic"
    )

    body = client.get(f"/api/papers/{paper_id}").json()

    assert body["abstract_source"] == "heuristic"


def _quarantine(client, filename: str, error: str) -> int:

    from sqlalchemy.orm import sessionmaker

    from app.models import Paper, PaperStatus

    factory = sessionmaker(bind=client.scinet_engine, expire_on_commit=False)
    with factory() as session:
        paper = Paper(
            content_sha256=filename.ljust(64, "0")[:64],
            work_key=filename,
            pdf_path=f"/data/quarantine/20260822/{filename}",
            pdf_bytes=1024,
            status=PaperStatus.QUARANTINED,
            last_error=error,
            pipeline_version=1,
        )
        session.add(paper)
        session.commit()
        return paper.id


def test_quarantine_list_is_empty_on_a_fresh_database(client):
    body = client.get("/api/papers/quarantine/list").json()
    assert body == {"items": [], "total": 0}


def test_quarantine_list_reports_the_name_and_the_diagnosis(client):
    _quarantine(
        client,
        "antifragile.azw3",
        "UnreadableDocument: DRM-protected (Amazon encrypted container); "
        "no tool can read it.",
    )

    body = client.get("/api/papers/quarantine/list").json()

    assert body["total"] == 1
    entry = body["items"][0]
    assert entry["filename"] == "antifragile.azw3"
    # The exception type belongs in a log, not in a sentence shown to someone
    # being told why their book was rejected.
    assert entry["reason"].startswith("DRM-protected")
    assert "UnreadableDocument" not in entry["reason"]
    # The filename has its own column; repeating it inside the sentence is
    # noise the parsers add for the benefit of truncated log lines.
    assert "File: antifragile.azw3" not in entry["reason"]
    assert entry["fatal"] is True


def test_a_document_that_merely_ran_out_of_retries_is_not_marked_fatal(client):
    _quarantine(client, "scan.pdf", "ConnectionError: connection refused")

    entry = client.get("/api/papers/quarantine/list").json()["items"][0]

    assert entry["fatal"] is False
    assert entry["reason"] == "connection refused"


def test_quarantine_route_is_not_shadowed_by_the_paper_detail_route(client):
    """`/quarantine/list` sits under the same prefix as `/{paper_id}`."""
    assert client.get("/api/papers/quarantine/list").status_code == 200


def test_restoring_something_that_is_not_quarantined_is_refused(client):
    paper_id = _add_paper(client, abstract="x", source="heuristic")

    assert client.post(f"/api/papers/{paper_id}/restore").status_code == 409


def test_restoring_an_unknown_paper_is_404(client):
    assert client.post("/api/papers/99999/restore").status_code == 404


@pytest.mark.parametrize(
    ("stored", "shown"),
    [
        (
            "UnreadableDocument: DRM-protected; no tool can read it.",
            "DRM-protected; no tool can read it.",
        ),
        ("the file vanished", "the file vanished"),
        # Better a useless string than an empty cell, which reads as a UI bug.
        ("MemoryError: ", "MemoryError:"),
        # Only the first separator splits; the message keeps its own colons.
        (
            "UnreadableDocument: no text layer: never run through OCR",
            "no text layer: never run through OCR",
        ),
        (None, "no diagnosis was recorded"),
    ],
)
def test_the_reason_shown_to_a_reader(stored, shown):
    """Mirrored in frontend/src/__tests__/quarantine.test.ts; this is the source."""
    from app.routers.papers import _readable_reason

    assert _readable_reason(stored) == shown


@pytest.mark.parametrize(
    "degenerate",
    ["File: book.azw3", "book.azw3", "UnreadableBook: File: book.azw3", ": ", ""],
)
def test_the_reason_cell_is_never_blank(degenerate):
    """An empty cell reads as a broken UI rather than as a missing diagnosis.

    Both trimming steps can eat the whole string on a degenerate message, and
    the filename column carrying the same word twice is the lesser evil.
    """
    from app.routers.papers import _readable_reason

    assert _readable_reason(degenerate, "book.azw3").strip()
