"""The library watcher's admission logic.

The interesting behaviour is not "notices files" — it is "waits until the file
has finished being written". Hashing a PDF mid-copy stores a digest that will
never match anything again, which permanently breaks dedup for that paper.
"""

from __future__ import annotations

import time

from app.services.ingest.watcher import DEBOUNCE_SECONDS, DocumentHandler


class Event:
    def __init__(self, path: str, is_directory: bool = False) -> None:
        self.src_path = path
        self.dest_path = path
        self.is_directory = is_directory


def make_handler(admitted: list) -> DocumentHandler:
    return DocumentHandler(on_ready=admitted.append)


def test_unsupported_formats_are_ignored(tmp_path):
    """Everything a library accumulates that is not a document."""
    admitted: list = []
    handler = make_handler(admitted)
    for name in ("cover.png", "slides.pptx", "archive.zip", "data.csv"):
        handler.on_created(Event(str(tmp_path / name)))
    assert handler._pending == {}


def test_every_readable_format_is_queued(tmp_path):
    """Books and text alike go through the same pipeline as a PDF."""
    handler = make_handler([])
    for name in ("notes.txt", "review.md", "draft.docx", "kuhn.epub", "geb.azw3"):
        handler.on_created(Event(str(tmp_path / name)))
    assert {p.name for p in handler._pending} == {
        "notes.txt",
        "review.md",
        "draft.docx",
        "kuhn.epub",
        "geb.azw3",
    }


def test_directories_are_ignored(tmp_path):
    handler = make_handler([])
    handler.on_created(Event(str(tmp_path / "subdir"), is_directory=True))
    assert handler._pending == {}


def test_pdf_is_queued_with_a_deadline(tmp_path):
    handler = make_handler([])
    pdf = tmp_path / "paper.pdf"
    handler.on_created(Event(str(pdf)))

    assert pdf in handler._pending
    assert handler._pending[pdf] > time.monotonic()


def test_repeated_writes_push_the_deadline_out(tmp_path):
    """This is the debounce: the clock restarts while bytes keep arriving."""
    handler = make_handler([])
    pdf = tmp_path / "paper.pdf"

    handler.on_created(Event(str(pdf)))
    first = handler._pending[pdf]
    time.sleep(0.02)
    handler.on_modified(Event(str(pdf)))
    second = handler._pending[pdf]

    assert second > first


def test_a_moved_file_is_queued_at_its_destination(tmp_path):
    handler = make_handler([])
    dest = tmp_path / "arrived.pdf"
    handler.on_moved(Event(str(dest)))
    assert dest in handler._pending


def test_uppercase_extension_is_accepted(tmp_path):
    handler = make_handler([])
    pdf = tmp_path / "PAPER.PDF"
    handler.on_created(Event(str(pdf)))
    assert pdf in handler._pending


def test_a_still_growing_file_is_not_admitted(tmp_path, monkeypatch):
    admitted: list = []
    handler = make_handler(admitted)
    pdf = tmp_path / "copying.pdf"
    pdf.write_bytes(b"partial")

    # Never settles: every stability check sees a new size.
    sizes = iter(range(1, 10_000))
    monkeypatch.setattr(
        "app.services.ingest.watcher.is_stable",
        lambda p, interval=1.0: next(sizes) and False,
    )
    monkeypatch.setattr("app.services.ingest.watcher.MAX_SETTLE_ATTEMPTS", 3)

    handler._admit(pdf)
    assert admitted == [], "a file still being written must not be ingested"


def test_a_settled_file_is_admitted(tmp_path, monkeypatch):
    admitted: list = []
    handler = make_handler(admitted)
    pdf = tmp_path / "done.pdf"
    pdf.write_bytes(b"complete")

    monkeypatch.setattr(
        "app.services.ingest.watcher.is_stable", lambda p, interval=1.0: True
    )
    handler._admit(pdf)
    assert admitted == [pdf]


def test_a_file_deleted_before_admission_is_skipped(tmp_path):
    admitted: list = []
    handler = make_handler(admitted)
    handler._admit(tmp_path / "vanished.pdf")
    assert admitted == []


def test_debounce_window_is_long_enough_to_be_useful():
    assert DEBOUNCE_SECONDS >= 1.0, "a sub-second debounce defeats the purpose"


# --- the startup rescan ---


def _no_session(monkeypatch):
    """rescan opens a session only to list what is pending; stub both."""
    from contextlib import contextmanager

    from app.services.ingest import watcher

    @contextmanager
    def fake_scope():
        yield None

    monkeypatch.setattr(watcher, "session_scope", fake_scope)
    return watcher


def test_rescan_admits_what_the_database_does_not_know(tmp_path, monkeypatch):
    watcher = _no_session(monkeypatch)
    new = [tmp_path / "a.pdf", tmp_path / "nested" / "b.md"]
    monkeypatch.setattr(watcher, "pending_documents", lambda root, session: list(new))
    admitted: list = []

    count = watcher.rescan(tmp_path, on_ready=admitted.append)

    assert count == 2
    assert admitted == new


def test_rescan_with_nothing_new_admits_nothing(tmp_path, monkeypatch):
    watcher = _no_session(monkeypatch)
    monkeypatch.setattr(watcher, "pending_documents", lambda root, session: [])
    admitted: list = []

    assert watcher.rescan(tmp_path, on_ready=admitted.append) == 0
    assert admitted == []


def test_one_bad_file_does_not_stop_the_rescan(tmp_path, monkeypatch):
    watcher = _no_session(monkeypatch)
    paths = [tmp_path / "broken.pdf", tmp_path / "fine.pdf"]
    monkeypatch.setattr(watcher, "pending_documents", lambda root, session: paths)
    seen: list = []

    def admit(path):
        seen.append(path)
        if path.name == "broken.pdf":
            raise OSError("unreadable")

    assert watcher.rescan(tmp_path, on_ready=admit) == 2
    assert seen == paths


def test_rescan_creates_the_library_folder(tmp_path, monkeypatch):
    """A fresh install has no data/library yet; the rescan must not fail on it."""
    watcher = _no_session(monkeypatch)
    monkeypatch.setattr(watcher, "pending_documents", lambda root, session: [])
    root = tmp_path / "library"

    watcher.rescan(root, on_ready=lambda p: None)

    assert root.is_dir()
