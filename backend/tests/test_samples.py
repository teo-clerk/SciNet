"""Sample libraries: installed from the repository, never fetched by the app.

Two things are pinned. The install path does exactly what the upload path
does — copy into the library, register, queue a parse — and nothing more; and
no sample action ever writes an egress_log row, because the one rule the
privacy story rests on is that the application does not open a socket.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, get_settings
from app.core.db import Base, build_engine, get_db
from app.main import create_app
from app.models import EgressLog, Job, JobKind, Paper
from app.routers import samples
from app.services.metadata.extract import extract_from_document

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from eval_clustering import domain_of  # noqa: E402

BUNDLE = Path(__file__).resolve().parents[2] / "demo" / "samples" / "history-of-thought"

PROSE = (
    "The subject of this essay is not the so-called liberty of the will, but "
    "civil, or social liberty: the nature and limits of the power which can be "
    "legitimately exercised by society over the individual. A question seldom "
    "stated, and hardly ever discussed, in general terms."
)


@pytest.fixture
def samples_dir(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "samples"
    bundled = root / "tiny-thought"
    bundled.mkdir(parents=True)
    (bundled / "sample.json").write_text(
        json.dumps(
            {
                "name": "tiny-thought",
                "title": "Tiny Thought",
                "blurb": "Two short texts.",
                "kind": "bundled",
            }
        )
    )
    (bundled / "SOURCES.md").write_text("# Sources\n")
    (bundled / "Ethics_01-mill.md").write_text(
        f"# On Liberty\n\nAuthor: John Stuart Mill\nYear: 1859\n\n{PROSE}\n"
    )
    (bundled / "Ethics_02-kant.md").write_text(
        f"# Groundwork\n\nAuthor: Immanuel Kant\nYear: 1785\n\n{PROSE} Again.\n"
    )
    (bundled / "cover.png").write_bytes(b"\x89PNG not a document")

    fetched = root / "big-corpus"
    fetched.mkdir()
    (fetched / "sample.json").write_text(
        json.dumps(
            {
                "name": "big-corpus",
                "title": "Big Corpus",
                "blurb": "Fetched by a script.",
                "kind": "fetch",
                "documents": 75,
                "command": "uv run python ../scripts/fetch_demo_corpus.py --recipe big",
            }
        )
    )
    monkeypatch.setattr(samples, "SAMPLES_DIR", root)
    return root


@pytest.fixture
def env(tmp_path, samples_dir):
    settings = Settings(
        data_dir=tmp_path / "data",
        library_dir=tmp_path / "data" / "library",
        markdown_dir=tmp_path / "data" / "markdown",
        vectors_dir=tmp_path / "data" / "vectors",
        models_dir=tmp_path / "data" / "models",
        db_path=tmp_path / "data" / "s.db",
    )
    settings.ensure_dirs()
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
    with TestClient(app) as client:
        yield client, factory, settings
    app.dependency_overrides.clear()
    engine.dispose()


def test_listing_names_each_sample_and_counts_only_documents(env):
    client, _, _ = env
    body = client.get("/api/samples").json()
    by_name = {s["name"]: s for s in body}

    tiny = by_name["tiny-thought"]
    assert tiny["kind"] == "bundled"
    assert tiny["documents"] == 2, "a cover image and SOURCES.md are not documents"
    assert tiny["installed"] is False
    assert tiny["command"] is None

    big = by_name["big-corpus"]
    assert big["kind"] == "fetch"
    assert big["documents"] == 75
    assert big["command"].endswith("--recipe big")


def test_install_copies_registers_and_queues_a_parse(env):
    client, factory, settings = env
    body = client.post("/api/samples/tiny-thought/install").json()
    assert body == {"name": "tiny-thought", "queued": 2, "duplicates": 0}

    copied = sorted(
        p.name for p in (settings.library_dir / "samples" / "tiny-thought").iterdir()
    )
    assert copied == ["Ethics_01-mill.md", "Ethics_02-kant.md"]

    with factory() as s:
        papers = s.scalars(select(Paper)).all()
        assert len(papers) == 2
        assert all(str(settings.library_dir) in p.pdf_path for p in papers)
        parses = s.scalars(select(Job).where(Job.kind == JobKind.PARSE)).all()
        assert len(parses) == 2

    assert {s["name"]: s["installed"] for s in client.get("/api/samples").json()}[
        "tiny-thought"
    ] is True


def test_installing_twice_creates_no_duplicate_rows(env):
    client, factory, _ = env
    client.post("/api/samples/tiny-thought/install")
    second = client.post("/api/samples/tiny-thought/install").json()
    assert second["queued"] == 0
    assert second["duplicates"] == 2
    with factory() as s:
        assert s.scalar(select(func.count(Paper.id))) == 2


def test_no_sample_action_leaves_the_machine(env):
    client, factory, _ = env
    client.get("/api/samples")
    client.post("/api/samples/tiny-thought/install")
    client.post("/api/samples/big-corpus/install")
    with factory() as s:
        assert s.scalar(select(func.count(EgressLog.id))) == 0


def test_a_fetched_sample_answers_with_its_command_not_a_download(env):
    client, _, _ = env
    response = client.post("/api/samples/big-corpus/install")
    assert response.status_code == 409
    assert response.json()["detail"]["command"].endswith("--recipe big")


@pytest.mark.parametrize("name", ["../etc", "..%2F..", "Tiny-Thought", "nope", "a b"])
def test_a_name_that_is_not_a_sample_folder_is_404(env, name):
    client, _, _ = env
    assert client.post(f"/api/samples/{name}/install").status_code == 404


# --- the bundle that actually ships -----------------------------------------------


@pytest.mark.skipif(not BUNDLE.is_dir(), reason="the bundle has not been built")
def test_the_bundled_library_is_a_benchmark_the_pipeline_can_read():
    """Every file names its theme the way the evaluator reads it back, and
    opens with front matter the metadata stage turns into a title, an author
    and a year — the clustering floor needs at least thirty of them."""
    files = sorted(p for p in BUNDLE.glob("*.md") if p.name != "SOURCES.md")
    assert len(files) >= 30

    themes = set()
    for path in files:
        domain = domain_of(str(path))
        assert domain, f"{path.name} carries no theme prefix"
        themes.add(domain)
        text = path.read_text(encoding="utf-8")
        meta = extract_from_document(path, parsed_text=text)
        assert meta.title, path.name
        assert meta.authors, path.name
        assert meta.abstract, path.name
        # Ancient works have no year the stage can file them under; every
        # dated one must come back with its own.
        if "Year: " in text[:400] and text[:400].split("Year: ")[1][:4].strip():
            assert meta.year is not None and meta.year < 1980, path.name
    assert len(themes) >= 4

    manifest = json.loads((BUNDLE / "sample.json").read_text())
    assert manifest["kind"] == "bundled"
    assert (BUNDLE / "SOURCES.md").is_file()
