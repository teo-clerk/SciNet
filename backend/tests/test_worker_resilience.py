"""One unreadable document must cost that document and nothing else.

The worker drains hundreds of jobs unattended. A DRM-locked ``.azw3`` or a
truncated PDF in the middle of that queue is ordinary — the pipeline is pointed
at somebody's real library, not a curated corpus — so the handling of a failed
job is not an edge case, it is the load-bearing path. It failed once, taking
435 queued documents down with it, which is what these tests are for.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core import db as db_module
from app.core.config import Settings
from app.models import Job, JobKind, JobState, Paper, PaperStatus
from app.services.parse.text_documents import UnreadableDocument
from app.workers import runner as runner_module
from app.workers.queue import enqueue
from app.workers.runner import Worker


@pytest.fixture
def worker(monkeypatch, sf, tmp_path) -> Worker:
    """A Worker whose sessions come from the test database."""
    monkeypatch.setattr(db_module, "get_session_factory", lambda: sf)
    monkeypatch.setattr(runner_module, "session_scope", db_module.session_scope)
    return Worker(Settings(data_dir=tmp_path))


def _paper(session: Session, name: str) -> Paper:
    paper = Paper(
        content_sha256=name.ljust(64, "0")[:64],
        work_key=name,
        pdf_path=f"/library/{name}",
        pdf_bytes=1024,
        status=PaperStatus.PENDING,
        pipeline_version=1,
    )
    session.add(paper)
    session.flush()
    return paper


@pytest.fixture
def three_jobs(sf) -> list[int]:
    """Two readable documents with an unreadable one between them."""
    with sf() as s:
        ids = []
        for name in ("good-one.pdf", "drm-locked.azw3", "good-two.pdf"):
            paper = _paper(s, name)
            ids.append(enqueue(s, JobKind.PARSE, paper_id=paper.id).id)
        s.commit()
        return ids


def _install_handler(monkeypatch, handler) -> None:
    """drain_stage resolves its own handler, so replace the registry entry."""
    monkeypatch.setitem(runner_module.HANDLERS, JobKind.PARSE, handler)


def _failing_on(bad_paper_id: int, seen: list[int]):
    def handler(session: Session, job: Job, settings: Settings) -> None:
        seen.append(job.paper_id)
        if job.paper_id == bad_paper_id:
            raise UnreadableDocument("drm-locked.azw3 is encrypted")

    return handler


def test_one_unreadable_document_does_not_stop_the_batch(
    worker, sf, three_jobs, monkeypatch
):
    seen: list[int] = []
    _install_handler(monkeypatch, _failing_on(2, seen))

    drained = worker.drain_stage(JobKind.PARSE)

    assert drained == 2, "both readable documents completed"

    # One attempt each, in queue order. An encrypted book is fatal, so it is
    # not retried — which matters more than it looks: fail() requeues, and
    # claim_next orders by id, so a retried document is re-claimed *ahead* of
    # the one behind it. Before fail-fast this read [1, 2, 2, 2, 3]: three
    # attempts at a file that could never be read, paid for by its neighbour.
    assert seen == [1, 2, 3]


def test_the_failed_job_is_recorded_not_left_running(
    worker, sf, three_jobs, monkeypatch
):
    """The bug rolled the failure back, stranding the job in `running`.

    A job left running is invisible to the queue and only recovers on a
    thirty-minute stale sweep — so the symptom was a worker that had exited and
    a backlog that would not move.
    """
    _install_handler(monkeypatch, _failing_on(2, []))
    worker.drain_stage(JobKind.PARSE)

    with sf() as s:
        job = s.get(Job, three_jobs[1])
        assert job.state != JobState.RUNNING, "the bug left it here"
        assert job.state == JobState.DEAD
        assert job.attempts == 1, "fatal, so it never came back for more"
        assert "encrypted" in job.last_error
        assert "UnreadableDocument" in job.last_error


def test_the_failure_survives_a_broken_notification(
    worker, sf, three_jobs, monkeypatch
):
    """Telemetry must never be able to undo the record it announces.

    This is the original failure in miniature: publishing raised, the exception
    reached session_scope, and the rollback discarded the rows written just
    above it.
    """

    def explode(*_args, **_kwargs):
        raise TypeError("publish() got multiple values for argument 'kind'")

    monkeypatch.setattr(runner_module.BROKER, "publish", explode)
    _install_handler(monkeypatch, _failing_on(2, []))

    drained = worker.drain_stage(JobKind.PARSE)

    assert drained == 2, "the batch still finished"
    with sf() as s:
        job = s.get(Job, three_jobs[1])
        assert job.state != JobState.RUNNING, "the record survived the bad publish"
        assert "encrypted" in job.last_error


def test_a_transient_failure_is_still_retried_to_the_ceiling(worker, sf, monkeypatch):
    """Fail-fast must not become fail-always.

    A model server that was restarting is exactly what the retries are for, and
    the classification is deliberately conservative: anything unrecognised is
    treated as transient, because retrying a broken file wastes minutes while
    giving up on a good one loses it from the library until somebody notices.
    """
    with sf() as s:
        paper = _paper(s, "fine.pdf")
        job_id = enqueue(s, JobKind.PARSE, paper_id=paper.id).id
        s.commit()

    attempts: list[int] = []

    def ollama_is_down(session, job, settings):
        attempts.append(job.id)
        raise ConnectionError("connection refused")

    _install_handler(monkeypatch, ollama_is_down)
    worker.drain_stage(JobKind.PARSE)

    assert len(attempts) == 3, "retried to the ceiling, not given up on"
    with sf() as s:
        job = s.get(Job, job_id)
        assert job.state == JobState.DEAD
        assert job.attempts == 3
