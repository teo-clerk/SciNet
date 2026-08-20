"""The tier-1 wall-clock budget.

An earlier version of this guard used `with ThreadPoolExecutor(...)`, whose
__exit__ calls shutdown(wait=True) — so raising the timeout inside the block
then blocked until the abandoned thread finished. It raised the right
exception, eventually, after the full runtime it existed to prevent.

These tests therefore assert *elapsed time*, not just the exception type. A
test that only checked `pytest.raises(Tier1Timeout)` passed against the broken
implementation.
"""

from __future__ import annotations

import threading
import time

import pytest

from app.services.parse.tier1_marker import Tier1Timeout


def run_with_budget(work, budget: float):
    """Mirror of tier1_marker.parse's guard, minus the Marker dependency."""
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as FuturesTimeout

    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tier1-test")
    try:
        future = pool.submit(work)
        try:
            return future.result(timeout=budget)
        except FuturesTimeout as exc:
            raise Tier1Timeout(f"exceeded {budget}s") from exc
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def test_a_stalled_conversion_returns_within_the_budget():
    """The property that matters: control comes back on time."""
    release = threading.Event()

    def never_finishes():
        release.wait(30)  # far longer than the budget
        return "too late"

    started = time.perf_counter()
    with pytest.raises(Tier1Timeout):
        run_with_budget(never_finishes, budget=0.3)
    elapsed = time.perf_counter() - started

    # Generous, but an order of magnitude below the 30s the thread runs for:
    # the broken implementation took the full 30s here.
    assert elapsed < 3.0, f"timeout blocked for {elapsed:.1f}s waiting on the thread"
    release.set()


def test_fast_work_returns_its_result():
    assert run_with_budget(lambda: "parsed", budget=5.0) == "parsed"


def test_an_error_inside_the_work_propagates():
    """A crash must surface as itself, not be masked as a timeout."""
    def boom():
        raise ValueError("corrupt xref")

    with pytest.raises(ValueError, match="corrupt xref"):
        run_with_budget(boom, budget=5.0)


def test_the_budget_is_derived_from_settings():
    from app.core.config import Settings

    s = Settings(tier1_timeout_seconds=90, tier1_calls_per_document=3)
    assert s.tier1_timeout_seconds * s.tier1_calls_per_document == 270


def test_repeated_timeouts_do_not_accumulate_blocking():
    """A corpus of stalling papers must not compound into a stall of its own."""
    release = threading.Event()

    def never_finishes():
        release.wait(30)

    started = time.perf_counter()
    for _ in range(3):
        with pytest.raises(Tier1Timeout):
            run_with_budget(never_finishes, budget=0.2)
    elapsed = time.perf_counter() - started

    assert elapsed < 4.0, f"three timeouts took {elapsed:.1f}s"
    release.set()
