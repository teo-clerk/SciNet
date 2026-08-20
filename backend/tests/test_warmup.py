"""Background model warming.

The property under test is not "the model loads" but "nothing waits for it":
startup returns immediately, the map serves while the load runs, and a query
that arrives early is told the engine is starting rather than shown an empty
result.
"""

from __future__ import annotations

import threading
import time

import pytest

from app.core.warmup import ModelWarmer, WarmupState


class SlowLoad:
    """Stands in for the real 25-second load."""

    def __init__(self, duration: float = 0.4, fail: bool = False) -> None:
        self.duration = duration
        self.fail = fail
        self.calls = 0
        self.started = threading.Event()

    def __call__(self, *args, **kwargs):
        self.calls += 1
        self.started.set()
        time.sleep(self.duration)
        if self.fail:
            raise RuntimeError("model file is corrupt")
        return object()


@pytest.fixture
def warmer(monkeypatch):
    loader = SlowLoad()
    monkeypatch.setattr("app.services.embed.encoder.encode_query", loader)
    w = ModelWarmer()
    w.loader = loader  # type: ignore[attr-defined]
    return w


# --- the point: nothing blocks ------------------------------------------


def test_start_returns_immediately(warmer):
    began = time.perf_counter()
    warmer.start()
    elapsed = time.perf_counter() - began

    assert elapsed < 0.05, f"start() blocked for {elapsed:.2f}s"
    assert warmer.status().state is WarmupState.WARMING


def test_the_load_runs_on_a_background_thread(warmer):
    warmer.start()
    assert warmer.loader.started.wait(timeout=2), "loader never ran"
    # Still warming while the main thread is free to do other work.
    assert warmer.status().state is WarmupState.WARMING


def test_it_becomes_ready(warmer):
    warmer.start()
    for _ in range(60):
        if warmer.is_ready:
            break
        time.sleep(0.05)
    assert warmer.is_ready
    assert warmer.status().state is WarmupState.READY


def test_a_cold_warmer_is_not_ready(warmer):
    assert not warmer.is_ready
    assert warmer.status().state is WarmupState.COLD


# --- one load, never several --------------------------------------------


def test_repeated_starts_do_not_stack_loads(warmer):
    for _ in range(10):
        warmer.start()
    time.sleep(0.6)
    assert warmer.loader.calls == 1, "a second load would double the memory cost"


def test_concurrent_starts_are_safe(warmer):
    """Several early queries can call start() at once."""
    threads = [threading.Thread(target=warmer.start) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    time.sleep(0.6)
    assert warmer.loader.calls == 1


def test_starting_an_already_ready_warmer_does_nothing(warmer):
    warmer.start()
    while not warmer.is_ready:
        time.sleep(0.02)
    warmer.start()
    time.sleep(0.2)
    assert warmer.loader.calls == 1


# --- failure is a state, not a crash -------------------------------------


def test_a_failed_load_is_reported_not_raised(monkeypatch):
    loader = SlowLoad(duration=0.05, fail=True)
    monkeypatch.setattr("app.services.embed.encoder.encode_query", loader)
    w = ModelWarmer()
    w.start()

    for _ in range(60):
        if w.status().state is WarmupState.FAILED:
            break
        time.sleep(0.05)
    status = w.status()
    assert status.state is WarmupState.FAILED
    assert "corrupt" in (status.error or "")


def test_a_failure_can_be_retried(monkeypatch):
    loader = SlowLoad(duration=0.05, fail=True)
    monkeypatch.setattr("app.services.embed.encoder.encode_query", loader)
    w = ModelWarmer()
    w.start()
    while w.status().state is not WarmupState.FAILED:
        time.sleep(0.02)

    w.reset()
    assert w.status().state is WarmupState.COLD
    w.start()
    time.sleep(0.2)
    assert loader.calls == 2


def test_reset_does_nothing_to_a_ready_warmer(warmer):
    warmer.start()
    while not warmer.is_ready:
        time.sleep(0.02)
    warmer.reset()
    assert warmer.is_ready, "resetting a working model would throw away the load"


# --- progress reporting ---------------------------------------------------


def test_elapsed_time_advances_while_warming(warmer):
    warmer.start()
    time.sleep(0.15)
    first = warmer.status().elapsed_seconds
    time.sleep(0.15)
    assert warmer.status().elapsed_seconds > first


def test_a_remaining_estimate_is_offered_while_warming(warmer):
    warmer.start()
    time.sleep(0.05)
    status = warmer.status()
    assert status.estimated_remaining is not None
    assert status.estimated_remaining >= 0


def test_no_estimate_once_ready(warmer):
    warmer.start()
    while not warmer.is_ready:
        time.sleep(0.02)
    assert warmer.status().estimated_remaining is None


def test_the_thread_is_a_daemon(warmer):
    """A half-finished warmup must not keep the process alive on shutdown."""
    warmer.start()
    assert warmer._thread is not None
    assert warmer._thread.daemon
