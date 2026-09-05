"""The worker owns the library watcher.

The watcher module was complete and tested long before anything started it;
these tests pin the wiring, not the watching — a fake ``watch`` records the
root it was given and whether it was stopped, and a fake ``rescan`` records
that the catch-up pass ran on its own thread.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager

import pytest

from app.core.config import Settings
from app.workers import runner


class FakeObserver:
    def __init__(self) -> None:
        self.stopped = False
        self.joined = False

    def stop(self) -> None:
        self.stopped = True

    def join(self, timeout: float | None = None) -> None:
        self.joined = True


@pytest.fixture
def quiet_worker(tmp_path, monkeypatch):
    """A Worker whose run_forever does one empty pass and returns."""

    @contextmanager
    def fake_scope():
        yield None

    monkeypatch.setattr(runner, "session_scope", fake_scope)
    monkeypatch.setattr(runner, "requeue_stale", lambda session: 0)
    monkeypatch.setattr(runner, "configure_environment", lambda s: {"HF_HOME": "x"})
    monkeypatch.setattr(runner, "free_all_models", lambda: None)
    monkeypatch.setattr(runner.signal, "signal", lambda *a, **k: None)

    def make(**overrides) -> runner.Worker:
        settings = Settings(
            data_dir=tmp_path,
            library_dir=tmp_path / "library",
            markdown_dir=tmp_path / "markdown",
            vectors_dir=tmp_path / "vectors",
            models_dir=tmp_path / "models",
            db_path=tmp_path / "worker.db",
            enrichment_enabled=False,
            **overrides,
        )
        worker = runner.Worker(settings)

        def one_pass() -> int:
            worker._stopping = True
            return 0

        monkeypatch.setattr(worker, "drain_one_pass", one_pass)
        return worker

    return make


def test_the_worker_watches_the_library_and_stops_with_it(quiet_worker, monkeypatch):
    observer = FakeObserver()
    watched: list = []
    rescanned = threading.Event()
    monkeypatch.setattr(
        runner, "watch", lambda root: (watched.append(root), observer)[1]
    )
    monkeypatch.setattr(runner, "rescan", lambda root: rescanned.set())
    worker = quiet_worker(watch_enabled=True)

    worker.run_forever()

    assert watched == [worker.settings.library_dir]
    assert observer.stopped and observer.joined
    assert rescanned.wait(2.0), "the startup rescan never ran"
    assert worker._observer is None


def test_watching_can_be_switched_off(quiet_worker, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(runner, "watch", lambda root: calls.append("watch"))
    monkeypatch.setattr(runner, "rescan", lambda root: calls.append("rescan"))
    worker = quiet_worker(watch_enabled=False)

    worker.run_forever()

    assert calls == []


def test_stopping_without_an_observer_is_harmless(quiet_worker):
    worker = quiet_worker(watch_enabled=False)
    worker._stop_watching()
    assert worker._observer is None
