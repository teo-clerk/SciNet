"""The pause lease: leased means no claims, expiry means no wedge.

Two failure modes were designed out and are pinned here. A stale ack from a
previous turn must not satisfy a new wait — the ack echoes the lease's
nonce. And no crash on either side may wedge the pipeline — expiry lives in
the value, so the worker resumes on its own when the holder stops
refreshing, and a malformed row reads as no lease at all.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.config import Settings
from app.core.types import utcnow
from app.models import Job, JobKind, JobState, Setting
from app.workers import lease
from app.workers import runner as runner_module
from app.workers.queue import enqueue
from app.workers.runner import Worker

# --- the lease itself --------------------------------------------------------


def test_a_lease_is_active_until_it_expires(db) -> None:
    held = lease.take(db, ttl_seconds=60)
    assert lease.active(db) is not None
    assert not held.expired


def test_an_expired_lease_is_no_lease(db) -> None:
    lease.take(db, ttl_seconds=-1)
    assert lease.active(db) is None


def test_refresh_extends_and_keeps_the_nonce(db) -> None:
    held = lease.take(db, ttl_seconds=1)
    extended = lease.refresh(db, held, ttl_seconds=300)
    assert extended.nonce == held.nonce
    assert extended.until > held.until


def test_release_only_ends_the_callers_own_lease(db) -> None:
    mine = lease.take(db, ttl_seconds=300, reason="librarian")
    theirs = lease.take(db, ttl_seconds=300, reason="user")  # newer lease wins
    lease.release(db, mine)  # stale holder tries to end it
    still = lease.active(db)
    assert still is not None and still.nonce == theirs.nonce

    lease.release(db, theirs)
    assert lease.active(db) is None


def test_a_stale_ack_does_not_satisfy_a_new_lease(db) -> None:
    first = lease.take(db, ttl_seconds=300)
    lease.ack(db, first.nonce)
    assert lease.acked(db, first)

    second = lease.refresh(db, lease.take(db, ttl_seconds=300))
    assert not lease.acked(db, second), "the ack must echo the current nonce"


def test_garbage_in_the_row_reads_as_no_lease(db) -> None:
    db.add(Setting(key=lease.PAUSE_KEY, value="{not json"))
    db.flush()
    assert lease.active(db) is None


# --- the worker honours it ---------------------------------------------------


@pytest.fixture
def worker(monkeypatch, sf, tmp_path) -> Worker:
    from app.core import db as db_module

    monkeypatch.setattr(db_module, "get_session_factory", lambda: sf)
    monkeypatch.setattr(runner_module, "session_scope", db_module.session_scope)
    return Worker(Settings(data_dir=tmp_path))


def test_a_leased_worker_claims_nothing(worker, sf, monkeypatch) -> None:
    freed: list[bool] = []
    monkeypatch.setattr(runner_module, "free_all_models", lambda: freed.append(True))

    with sf() as session:
        job = enqueue(session, JobKind.PARSE, paper_id=None)
        job_id = job.id
        lease.take(session, ttl_seconds=300, reason="test")
        session.commit()

    assert worker.drain_stage(JobKind.PARSE) == 0
    assert worker.drain_one_pass() == 0

    with sf() as session:
        untouched = session.get(Job, job_id)
        assert untouched.state == JobState.QUEUED
        assert untouched.attempts == 0
        held = lease.active(session)
        assert held is not None and lease.acked(session, held)
    # The card is yielded once per lease, not once per poll.
    assert freed == [True]


def test_the_worker_resumes_when_the_lease_expires(worker, sf, monkeypatch) -> None:
    monkeypatch.setattr(runner_module, "free_all_models", lambda: None)
    handled: list[int] = []
    monkeypatch.setitem(
        runner_module.HANDLERS,
        JobKind.PARSE,
        lambda session, job, settings: handled.append(job.id),
    )

    with sf() as session:
        enqueue(session, JobKind.PARSE, paper_id=None)
        held = lease.take(session, ttl_seconds=300, reason="test")
        session.commit()

    assert worker.drain_stage(JobKind.PARSE) == 0

    with sf() as session:
        # The holder stops refreshing; time passes. Simulated by backdating.
        expired = lease.Lease(
            until=utcnow() - timedelta(seconds=1),
            nonce=held.nonce,
            reason=held.reason,
        )
        lease.take(session, ttl_seconds=-1, nonce=held.nonce)
        session.commit()
        assert lease.active(session) is None
        _ = expired

    assert worker.drain_stage(JobKind.PARSE) == 1
    assert len(handled) == 1
