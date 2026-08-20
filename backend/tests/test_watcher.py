"""The library watcher's admission logic.

The interesting behaviour is not "notices files" — it is "waits until the file
has finished being written". Hashing a PDF mid-copy stores a digest that will
never match anything again, which permanently breaks dedup for that paper.
"""

from __future__ import annotations

import time

from app.services.ingest.watcher import DEBOUNCE_SECONDS, PdfHandler


class Event:
    def __init__(self, path: str, is_directory: bool = False) -> None:
        self.src_path = path
        self.dest_path = path
        self.is_directory = is_directory


def make_handler(admitted: list) -> PdfHandler:
    return PdfHandler(on_ready=admitted.append)


def test_non_pdf_files_are_ignored(tmp_path):
    admitted: list = []
    handler = make_handler(admitted)
    handler.on_created(Event(str(tmp_path / "notes.txt")))
    assert handler._pending == {}


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
