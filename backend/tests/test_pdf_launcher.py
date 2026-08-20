"""The open-in-viewer endpoint.

This is the one place a stored value reaches a subprocess, so the guard is
tested through the HTTP layer rather than only at the helper: a correct
`resolve_within` is worth nothing if the endpoint forgets to call it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, get_settings
from app.core.db import Base, build_engine, get_db
from app.main import create_app
from app.models import Paper, PaperStatus


@pytest.fixture
def env(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    settings = Settings(
        data_dir=tmp_path,
        library_dir=library,
        markdown_dir=tmp_path / "md",
        vectors_dir=tmp_path / "vec",
        models_dir=tmp_path / "models",
        db_path=tmp_path / "p.db",
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
        yield client, factory, settings, tmp_path
    app.dependency_overrides.clear()
    engine.dispose()


def add_paper(factory, path: str) -> int:
    with factory() as s:
        paper = Paper(
            content_sha256=f"{abs(hash(path)):064x}"[:64],
            work_key=path,
            pdf_path=path,
            status=PaperStatus.READY,
            pipeline_version=1,
        )
        s.add(paper)
        s.commit()
        return paper.id


@pytest.fixture
def launched(monkeypatch):
    """Capture what would have been executed, without executing it."""
    calls: list[list[str]] = []

    class FakeProcess:
        pass

    def fake_popen(args, **kwargs):
        calls.append(args)
        return FakeProcess()

    monkeypatch.setattr("app.routers.papers.subprocess.Popen", fake_popen)
    return calls


def test_a_paper_inside_the_library_opens(env, launched):
    client, factory, settings, _ = env
    pdf = settings.library_dir / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.7")
    paper_id = add_paper(factory, str(pdf))

    res = client.post(f"/api/papers/{paper_id}/open")
    assert res.status_code == 200
    assert launched == [["xdg-open", str(pdf.resolve())]]


def test_the_command_is_an_argument_vector_never_a_shell_string(env, launched):
    """A filename containing shell metacharacters must be inert."""
    client, factory, settings, _ = env
    nasty = settings.library_dir / "paper; rm -rf ~.pdf"
    nasty.write_bytes(b"%PDF-1.7")
    paper_id = add_paper(factory, str(nasty))

    client.post(f"/api/papers/{paper_id}/open")
    assert launched[0] == ["xdg-open", str(nasty.resolve())]
    assert isinstance(launched[0], list), "a string would be parsed by a shell"


def test_a_path_outside_the_library_is_refused(env, launched):
    """The client only sends an id — but a stored row can still point anywhere."""
    client, factory, _, tmp_path = env
    outside = tmp_path / "secret.pdf"
    outside.write_bytes(b"%PDF-1.7")
    paper_id = add_paper(factory, str(outside))

    res = client.post(f"/api/papers/{paper_id}/open")
    assert res.status_code == 400
    assert launched == [], "nothing may be executed for a rejected path"


def test_a_traversal_path_is_refused(env, launched):
    client, factory, settings, tmp_path = env
    (tmp_path / "escape.pdf").write_bytes(b"%PDF-1.7")
    paper_id = add_paper(factory, str(settings.library_dir / ".." / "escape.pdf"))

    assert client.post(f"/api/papers/{paper_id}/open").status_code == 400
    assert launched == []


def test_a_symlink_escaping_the_library_is_refused(env, launched):
    """The classic bypass: the path is inside, the file is not."""
    client, factory, settings, tmp_path = env
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-1.7")
    link = settings.library_dir / "innocent.pdf"
    link.symlink_to(outside)
    paper_id = add_paper(factory, str(link))

    assert client.post(f"/api/papers/{paper_id}/open").status_code == 400
    assert launched == []


def test_a_sibling_directory_sharing_a_prefix_is_refused(env, launched):
    """`/data/library-backup` must not pass a check against `/data/library`."""
    client, factory, settings, tmp_path = env
    sibling = tmp_path / f"{settings.library_dir.name}-backup"
    sibling.mkdir()
    target = sibling / "paper.pdf"
    target.write_bytes(b"%PDF-1.7")
    paper_id = add_paper(factory, str(target))

    assert client.post(f"/api/papers/{paper_id}/open").status_code == 400
    assert launched == []


def test_a_missing_file_is_410_not_a_launch(env, launched):
    client, factory, settings, _ = env
    paper_id = add_paper(factory, str(settings.library_dir / "gone.pdf"))

    assert client.post(f"/api/papers/{paper_id}/open").status_code == 410
    assert launched == []


def test_an_unknown_paper_is_404(env, launched):
    client, *_ = env
    assert client.post("/api/papers/99999/open").status_code == 404
    assert launched == []


def test_the_endpoint_takes_no_path_from_the_client(env):
    """The route accepts an integer id and nothing else."""
    client, *_ = env
    for attempt in ("/api/papers/..%2F..%2Fetc%2Fpasswd/open", "/api/papers/abc/open"):
        assert client.post(attempt).status_code in (404, 422)


def test_streaming_a_pdf_enforces_the_same_guard(env):
    """The viewer endpoint reads the same stored path and needs the same check."""
    client, factory, _, tmp_path = env
    outside = tmp_path / "secret.pdf"
    outside.write_bytes(b"%PDF-1.7")
    paper_id = add_paper(factory, str(outside))

    assert client.get(f"/api/papers/{paper_id}/pdf").status_code == 400
