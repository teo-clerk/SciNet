"""The SQLite-backed job queue.

This replaces Celery/Redis, so the properties Celery would have given us for
free — atomic claim, retry, crash recovery — have to be demonstrated here.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.models import Job, JobKind, JobState
from app.workers.queue import (
    claim_next,
    complete,
    enqueue,
    fail,
    pending_counts,
    requeue_stale,
)


def test_enqueue_assigns_default_priority(sf):
    with sf() as s:
        job = enqueue(s, JobKind.PARSE, paper_id=1)
        s.commit()
        assert job.priority == 10
        assert job.state == JobState.QUEUED


def test_tagging_is_deprioritised_below_parsing(sf):
    """The map must be navigable before tags exist, so tagging queues last."""
    with sf() as s:
        tag = enqueue(s, JobKind.TAG, paper_id=1)
        parse = enqueue(s, JobKind.PARSE, paper_id=2)
        s.commit()
        assert parse.priority < tag.priority


def test_claim_returns_highest_priority_first(sf):
    with sf() as s:
        enqueue(s, JobKind.TAG, paper_id=1)
        enqueue(s, JobKind.PARSE, paper_id=2)
        s.commit()

    with sf() as s:
        job = claim_next(s)
        assert job is not None
        assert job.kind == JobKind.PARSE


def test_claim_can_be_restricted_to_one_kind(sf):
    """The worker drains a whole kind before switching, to avoid GPU thrash."""
    with sf() as s:
        enqueue(s, JobKind.PARSE, paper_id=1)
        enqueue(s, JobKind.TAG, paper_id=2)
        s.commit()

    with sf() as s:
        job = claim_next(s, kinds=[JobKind.TAG])
        assert job is not None
        assert job.kind == JobKind.TAG


def test_claim_marks_running_and_counts_the_attempt(sf):
    with sf() as s:
        enqueue(s, JobKind.PARSE, paper_id=1)
        s.commit()

    with sf() as s:
        job = claim_next(s)
        assert job.state == JobState.RUNNING
        assert job.attempts == 1
        assert job.started_at is not None


def test_claim_returns_none_when_empty(sf):
    with sf() as s:
        assert claim_next(s) is None


def test_a_job_is_never_claimed_twice(sf):
    """The core guarantee: one row, one worker, even under contention."""
    with sf() as s:
        for i in range(20):
            enqueue(s, JobKind.PARSE, paper_id=i)
        s.commit()

    def grab() -> list[int]:
        got = []
        with sf() as s:
            while (job := claim_next(s)) is not None:
                got.append(job.id)
        return got

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = [f.result() for f in [pool.submit(grab) for _ in range(6)]]

    claimed = [jid for r in results for jid in r]
    assert len(claimed) == 20
    assert len(set(claimed)) == 20, "a job was handed to more than one worker"


def test_complete_marks_done(sf):
    with sf() as s:
        enqueue(s, JobKind.PARSE, paper_id=1)
        s.commit()
    with sf() as s:
        job = claim_next(s)
        complete(s, job)
        s.commit()
        assert job.state == JobState.DONE
        assert job.finished_at is not None


def test_fail_requeues_until_max_attempts_then_dies(sf):
    with sf() as s:
        enqueue(s, JobKind.PARSE, paper_id=1, max_attempts=2)
        s.commit()

    with sf() as s:
        job = claim_next(s)
        fail(s, job, "boom")
        s.commit()
        assert job.state == JobState.QUEUED, "first failure should retry"
        assert job.last_error == "boom"

    with sf() as s:
        job = claim_next(s)
        assert job.attempts == 2
        fail(s, job, "boom again")
        s.commit()
        assert job.state == JobState.DEAD, "must stop retrying at max_attempts"


def test_dead_jobs_are_not_reclaimed(sf):
    with sf() as s:
        enqueue(s, JobKind.PARSE, paper_id=1, max_attempts=1)
        s.commit()
    with sf() as s:
        fail(s, claim_next(s), "x")
        s.commit()
    with sf() as s:
        assert claim_next(s) is None


def test_requeue_stale_recovers_jobs_from_a_killed_worker(sf):
    """Kill -9 during a batch must not strand rows in 'running' forever."""
    with sf() as s:
        enqueue(s, JobKind.PARSE, paper_id=1)
        s.commit()
    with sf() as s:
        claim_next(s)
        s.commit()  # worker dies here, never completes

    with sf() as s:
        n = requeue_stale(s)
        s.commit()
        assert n == 1

    with sf() as s:
        assert claim_next(s) is not None


def test_startup_sweep_reclaims_even_a_seconds_old_claim(sf):
    """A fast supervisor restart must not strand a fresh claim.

    The sweeping worker holds nothing at its own startup, so a running row's
    age is irrelevant — the previous version waited 30 minutes and left a
    just-claimed batch unclaimable across a quick restart.
    """
    with sf() as s:
        enqueue(s, JobKind.PARSE, paper_id=1)
        s.commit()
    with sf() as s:
        claim_next(s)
        s.commit()  # supervisor restarts the worker immediately

    with sf() as s:
        assert requeue_stale(s) == 1
        s.commit()
    with sf() as s:
        assert claim_next(s) is not None


def test_startup_sweep_sends_an_exhausted_job_to_dead(sf):
    """Dying on the final attempt must not buy a fourth one."""
    with sf() as s:
        enqueue(s, JobKind.PARSE, paper_id=1, max_attempts=1)
        s.commit()
    with sf() as s:
        claim_next(s)
        s.commit()  # the only attempt, and the worker dies in it

    with sf() as s:
        assert requeue_stale(s) == 1
        s.commit()
    with sf() as s:
        assert claim_next(s) is None
        job = s.query(Job).one()
        assert job.state == JobState.DEAD


def test_enqueue_is_idempotent_per_paper_and_kind(sf):
    """Re-scanning the library must not pile up duplicate work."""
    with sf() as s:
        enqueue(s, JobKind.PARSE, paper_id=7)
        enqueue(s, JobKind.PARSE, paper_id=7)
        s.commit()
        assert s.query(Job).count() == 1


def test_pending_counts_groups_by_kind_and_state(sf):
    with sf() as s:
        enqueue(s, JobKind.PARSE, paper_id=1)
        enqueue(s, JobKind.TAG, paper_id=1)
        s.commit()
    with sf() as s:
        claim_next(s)
        s.commit()

    with sf() as s:
        counts = pending_counts(s)
        assert counts[(JobKind.PARSE, JobState.RUNNING)] == 1
        assert counts[(JobKind.TAG, JobState.QUEUED)] == 1


@pytest.mark.parametrize("kind", list(JobKind))
def test_every_kind_has_a_declared_priority(sf, kind):
    with sf() as s:
        job = enqueue(s, kind, paper_id=1)
        s.commit()
        assert job.priority > 0


def test_corpus_wide_jobs_collapse_onto_one(sf):
    """A projection pass computes the same thing regardless of who asked.

    Without this, a 500-paper backfill enqueues 500 full-corpus projections —
    one after every embedding — instead of one.
    """
    with sf() as s:
        for _ in range(50):
            enqueue(s, JobKind.PROJECT, paper_id=None)
        s.commit()
        assert s.query(Job).filter(Job.kind == JobKind.PROJECT).count() == 1


def test_a_corpus_job_can_be_requeued_once_the_previous_one_finished(sf):
    """Collapsing must not prevent the *next* pass from being scheduled."""
    with sf() as s:
        enqueue(s, JobKind.PROJECT, paper_id=None)
        s.commit()
    with sf() as s:
        complete(s, claim_next(s, kinds=[JobKind.PROJECT]))
        s.commit()
    with sf() as s:
        enqueue(s, JobKind.PROJECT, paper_id=None)
        s.commit()
        assert s.query(Job).filter(Job.kind == JobKind.PROJECT).count() == 2


def test_per_paper_jobs_still_dedupe_per_paper(sf):
    with sf() as s:
        enqueue(s, JobKind.EMBED, paper_id=1)
        enqueue(s, JobKind.EMBED, paper_id=1)
        enqueue(s, JobKind.EMBED, paper_id=2)
        s.commit()
        assert s.query(Job).filter(Job.kind == JobKind.EMBED).count() == 2
