"""Uploading PDFs through the interface.

Built for batches of a few thousand, so the properties that matter are that one
bad file cannot end a batch, that a client-supplied filename cannot steer where
anything lands, and that nothing is silently overwritten.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, get_settings
from app.core.db import Base, build_engine, get_db
from app.main import create_app
from app.models import Paper

PDF = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n%%EOF\n"


@pytest.fixture
def env(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        library_dir=tmp_path / "library",
        markdown_dir=tmp_path / "md",
        vectors_dir=tmp_path / "vec",
        models_dir=tmp_path / "models",
        db_path=tmp_path / "u.db",
    )
    settings.ensure_dirs()
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


def upload(client, files):
    return client.post(
        "/api/papers/upload",
        files=[
            ("files", (name, io.BytesIO(data), "application/pdf"))
            for name, data in files
        ],
    )


def test_a_pdf_lands_in_the_library_and_is_queued(env):
    client, factory, settings = env
    body = upload(client, [("paper.pdf", PDF)]).json()

    assert body["queued"] == 1
    assert body["accepted"][0]["outcome"] == "created"
    assert (settings.library_dir / "paper.pdf").exists()
    with factory() as s:
        assert s.query(Paper).count() == 1


def test_several_files_arrive_together(env):
    client, factory, _ = env
    body = upload(client, [(f"p{i}.pdf", PDF + bytes([i])) for i in range(5)]).json()

    assert body["total"] == 5
    assert body["queued"] == 5
    with factory() as s:
        assert s.query(Paper).count() == 5


def test_one_bad_file_does_not_end_the_batch(env):
    """A rejected file is reported beside the ones that worked."""
    client, factory, _ = env
    body = upload(
        client,
        [
            ("good.pdf", PDF + b"a"),
            ("nonsense.pdf", b"this is not a pdf at all"),
            ("also-good.pdf", PDF + b"b"),
        ],
    ).json()

    assert len(body["accepted"]) == 2
    assert len(body["rejected"]) == 1
    assert "%PDF-" in body["rejected"][0]["reason"]
    with factory() as s:
        assert s.query(Paper).count() == 2


def test_a_rejected_file_leaves_nothing_behind(env):
    client, _, settings = env
    upload(client, [("nonsense.pdf", b"not a pdf")])
    assert list(settings.library_dir.iterdir()) == []


def test_an_empty_file_is_rejected(env):
    client, _, _ = env
    body = upload(client, [("empty.pdf", b"")]).json()
    assert body["rejected"][0]["reason"] == "empty file"


# --- the filename is untrusted input -------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "../../../etc/cron.d/evil.pdf",
        "/etc/passwd.pdf",
        "..%2F..%2Fescape.pdf",
        "sub/dir/nested.pdf",
    ],
)
def test_a_filename_cannot_steer_where_a_file_lands(env, hostile):
    client, _, settings = env
    upload(client, [(hostile, PDF)])

    written = list(settings.library_dir.rglob("*"))
    assert written, "the upload should still have been accepted"
    for path in written:
        assert path.parent == settings.library_dir, f"{path} escaped the library"


def test_odd_characters_in_a_filename_are_neutralised(env):
    client, _, settings = env
    upload(client, [("pa per; rm -rf ~.pdf", PDF)])
    name = next(settings.library_dir.iterdir()).name
    assert ";" not in name and " " not in name
    assert name.endswith(".pdf")


def test_a_name_without_a_pdf_extension_gets_one(env):
    client, _, settings = env
    upload(client, [("noextension", PDF)])
    assert next(settings.library_dir.iterdir()).name.endswith(".pdf")


def test_two_files_with_one_name_do_not_overwrite(env):
    """Different papers legitimately share filenames; content dedup comes later."""
    client, _, settings = env
    upload(client, [("paper.pdf", PDF + b"first")])
    upload(client, [("paper.pdf", PDF + b"second")])

    names = sorted(p.name for p in settings.library_dir.iterdir())
    assert names == ["paper(1).pdf", "paper.pdf"]


def test_identical_content_is_accepted_but_not_queued_twice(env):
    """Byte-identical uploads are the same paper, however they are named."""
    client, factory, _ = env
    upload(client, [("first.pdf", PDF)])
    body = upload(client, [("second.pdf", PDF)]).json()

    assert body["accepted"][0]["outcome"] in {"duplicate_content", "moved"}
    assert body["queued"] == 0
    with factory() as s:
        assert s.query(Paper).count() == 1


def test_an_empty_upload_is_rejected_by_validation(env):
    client, _, _ = env
    assert client.post("/api/papers/upload", files=[]).status_code == 422
