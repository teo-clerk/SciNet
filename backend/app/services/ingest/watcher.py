"""Watches the library folder for new documents.

The hard part is not noticing files, it is noticing them at the right moment. A
PDF being copied in fires modify events continuously, and hashing it mid-copy
records a digest that will never be seen again — which poisons dedup for that
paper permanently. So events are debounced and the file's size must settle
before it is registered.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from app.core.db import session_scope
from app.core.events import BROKER
from app.core.paths import classify_document
from app.services.ingest.hashing import is_stable
from app.services.ingest.registrar import Registration, register_document

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 2.0
SETTLE_POLL_SECONDS = 1.0
MAX_SETTLE_ATTEMPTS = 30


class DocumentHandler(FileSystemEventHandler):
    """Queues candidate paths; a background thread does the slow part."""

    def __init__(self, on_ready: Callable[[Path], None]) -> None:
        self._on_ready = on_ready
        self._pending: dict[Path, float] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._drain, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _note(self, raw_path: str) -> None:
        path = Path(raw_path)
        if classify_document(path) is None:
            return
        with self._lock:
            # Resetting the deadline on every event is the debounce: the clock
            # only starts once writes stop arriving.
            self._pending[path] = time.monotonic() + DEBOUNCE_SECONDS

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._note(str(event.src_path))

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._note(str(event.src_path))

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._note(str(event.dest_path))

    def _drain(self) -> None:
        while not self._stop.wait(0.5):
            now = time.monotonic()
            with self._lock:
                ready = [p for p, deadline in self._pending.items() if deadline <= now]
                for path in ready:
                    self._pending.pop(path, None)

            for path in ready:
                try:
                    self._admit(path)
                except Exception:  # noqa: BLE001 - the watcher must not die
                    logger.exception("failed to admit %s", path)

    def _admit(self, path: Path) -> None:
        if not path.exists():
            return
        for _ in range(MAX_SETTLE_ATTEMPTS):
            if is_stable(path, interval=SETTLE_POLL_SECONDS):
                self._on_ready(path)
                return
            logger.debug("%s is still being written", path.name)
        logger.warning("%s never stopped changing; giving up for now", path.name)


def admit_path(path: Path) -> None:
    """Register a settled document and enqueue its parse job."""
    with session_scope() as session:
        result = register_document(session, path)

    if result.outcome is Registration.CREATED and result.paper is not None:
        logger.info("ingested %s as paper %d", path.name, result.paper.id)
        BROKER.publish("paper.added", paper_id=result.paper.id, name=path.name)
    else:
        logger.debug("%s: %s", path.name, result.outcome.value)


def watch(root: Path, on_ready: Callable[[Path], None] = admit_path) -> Observer:
    """Start watching ``root``. Caller owns stopping the returned observer."""
    root.mkdir(parents=True, exist_ok=True)
    handler = DocumentHandler(on_ready)
    handler.start()

    observer = Observer()
    observer.schedule(handler, str(root), recursive=True)
    observer.start()
    logger.info("watching %s for new documents", root)
    return observer
