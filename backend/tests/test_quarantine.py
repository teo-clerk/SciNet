"""Moving unreadable documents out of the library, and back.

Distinct from ``cleanup``, which judges files before ingestion on what their
bytes are. This acts on the evidence of a parse that actually failed — an
encrypted book looks like an ordinary one from the outside, and nothing says
otherwise until something tries to read it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.models import Job, JobKind, JobState, Paper, PaperStatus
from app.services.ingest.cleanup import MANIFEST_NAME, QUARANTINE_DIRNAME
from app.services.ingest.quarantine import (
    quarantine_document,
    quarantine_root,
    restore,
)
from app.services.parse.text_documents import UnreadableDocument
from app.workers import runner as runner_module
from app.workers.queue import enqueue
from app.workers.runner import Worker
from tests.test_worker_resilience import _install_handler, _paper

DRM = (
    "DRM-protected (Amazon encrypted container); no tool can read it. "
    "A DRM-free copy would ingest normally. File: antifragile.azw3"
)


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    return root


def test_the_file_leaves_the_library(library):
    book = library / "antifragile.azw3"
    book.write_bytes(b"CR!encrypted")

    moved = quarantine_document(book, library, DRM)

    assert moved is not None
    assert not book.exists(), "still in the library"
    assert moved.destination.exists()
    assert moved.destination.read_bytes() == b"CR!encrypted", "moved, not copied"


def test_the_diagnosis_is_filed_next_to_it(library):
    book = library / "antifragile.azw3"
    book.write_bytes(b"CR!encrypted")

    moved = quarantine_document(book, library, DRM)

    manifest = json.loads(
        (moved.destination.parent / MANIFEST_NAME).read_text(encoding="utf-8")
    )
    entry = manifest["files"][0]
    assert entry["reason"] == DRM
    assert entry["original"].endswith("antifragile.azw3")
    assert "at" in entry


def test_several_failures_on_one_day_share_a_manifest(library):
    for name in ("a.azw3", "b.pdf", "c.djvu"):
        (library / name).write_bytes(b"x")
        quarantine_document(library / name, library, f"broken: {name}")

    day = next(quarantine_root(library).iterdir())
    manifest = json.loads((day / MANIFEST_NAME).read_text(encoding="utf-8"))

    assert len(manifest["files"]) == 3
    assert {e["original"].rsplit("/", 1)[-1] for e in manifest["files"]} == {
        "a.azw3",
        "b.pdf",
        "c.djvu",
    }


def test_subfolders_are_preserved_and_names_never_collide(library):
    for folder in ("books", "papers"):
        (library / folder).mkdir()
        (library / folder / "scan.djvu").write_bytes(folder.encode())
        quarantine_document(library / folder / "scan.djvu", library, "no text layer")

    day = next(quarantine_root(library).iterdir())
    found = sorted(p.name for p in day.rglob("scan*.djvu"))

    assert len(found) == 2, "two different files must not overwrite each other"


def test_a_file_outside_the_library_is_left_alone(tmp_path, library):
    elsewhere = tmp_path / "elsewhere.pdf"
    elsewhere.write_bytes(b"%PDF-1.4")

    assert quarantine_document(elsewhere, library, "broken") is None
    assert elsewhere.exists()


def test_a_missing_file_is_not_an_error(library):
    assert quarantine_document(library / "gone.pdf", library, "broken") is None


def test_restore_puts_it_back(library):
    book = library / "antifragile.azw3"
    book.write_bytes(b"CR!encrypted")
    moved = quarantine_document(book, library, DRM)

    returned = restore(moved.destination, library)

    assert returned == library / "antifragile.azw3"
    assert returned.read_bytes() == b"CR!encrypted"
    assert not moved.destination.exists()


def test_restore_refuses_a_path_outside_quarantine(tmp_path, library):
    """The path arrives over HTTP; it must not be able to steer the move."""
    outsider = tmp_path / "secrets.txt"
    outsider.write_text("private", encoding="utf-8")

    assert restore(outsider, library) is None
    assert outsider.exists()


# --- through the worker ---------------------------------------------------


@pytest.fixture
def worker(monkeypatch, sf, library) -> Worker:
    from app.core import db as db_module

    monkeypatch.setattr(db_module, "get_session_factory", lambda: sf)
    monkeypatch.setattr(runner_module, "session_scope", db_module.session_scope)
    return Worker(Settings(data_dir=library.parent, library_dir=library))


def test_an_unreadable_document_is_quarantined_by_the_worker(
    worker, sf, library, monkeypatch
):
    book = library / "antifragile.azw3"
    book.write_bytes(b"CR!encrypted")
    with sf() as s:
        paper = _paper(s, "antifragile.azw3")
        paper.pdf_path = str(book)
        paper_id = paper.id
        enqueue(s, JobKind.PARSE, paper_id=paper_id)
        s.commit()

    def drm(session, job, settings):
        raise UnreadableDocument(DRM)

    _install_handler(monkeypatch, drm)
    worker.drain_stage(JobKind.PARSE)

    assert not book.exists(), "the library still holds a file nothing can read"
    with sf() as s:
        paper = s.get(Paper, paper_id)
        assert paper.status == PaperStatus.QUARANTINED
        assert "DRM-protected" in paper.last_error
        # The row follows the file, or every later "has this disappeared?"
        # check reports a paper that is sitting safely in quarantine.
        assert QUARANTINE_DIRNAME in paper.pdf_path
        assert Path(paper.pdf_path).exists()


def test_a_readable_paper_that_fails_later_is_not_quarantined(
    worker, sf, library, monkeypatch
):
    """Quarantine is about unreadable files, not about a busy model.

    A paper that parsed and then failed to embed is a perfectly good document;
    moving it out of the library would fix a problem it does not have.
    """
    paper_file = library / "good.pdf"
    paper_file.write_bytes(b"%PDF-1.4 fine")
    with sf() as s:
        paper = _paper(s, "good.pdf")
        paper.pdf_path = str(paper_file)
        paper_id = paper.id
        enqueue(s, JobKind.EMBED, paper_id=paper_id)
        s.commit()

    def gpu_busy(session, job, settings):
        raise RuntimeError("CUDA out of memory")

    _install_handler(monkeypatch, gpu_busy)
    monkeypatch.setitem(runner_module.HANDLERS, JobKind.EMBED, gpu_busy)
    worker.drain_stage(JobKind.EMBED)

    assert paper_file.exists(), "a good file was taken out of the library"
    with sf() as s:
        assert s.get(Paper, paper_id).status != PaperStatus.QUARANTINED


def test_a_transient_parse_failure_is_only_quarantined_once_it_gives_up(
    worker, sf, library, monkeypatch
):
    scan = library / "scan.pdf"
    scan.write_bytes(b"%PDF-1.4")
    with sf() as s:
        paper = _paper(s, "scan.pdf")
        paper.pdf_path = str(scan)
        job_id = enqueue(s, JobKind.PARSE, paper_id=paper.id).id
        s.commit()

    seen: list[int] = []

    def ollama_down(session, job, settings):
        seen.append(job.attempts)
        raise ConnectionError("connection refused")

    _install_handler(monkeypatch, ollama_down)
    worker.drain_stage(JobKind.PARSE)

    assert seen == [1, 2, 3], "retried before being written off"
    assert not scan.exists(), "quarantined once the queue gave up"
    with sf() as s:
        assert s.get(Job, job_id).state == JobState.DEAD
