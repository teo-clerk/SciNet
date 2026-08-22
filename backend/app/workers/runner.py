"""The worker process.

Runs separately from the API on purpose. GPU work inside the API process would
contend with the event loop, die on every reload, and — most importantly — make
it impossible to serialise model residency across the 8 GiB card.

The loop drains one job *kind* at a time rather than taking whatever is next.
That is the whole point: switching kinds means switching models, and a model
load costs 5-15 s. Interleaving per paper would spend more time loading models
than parsing papers.
"""

from __future__ import annotations

import logging
import signal
import sys
import time

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.db import session_scope
from app.core.events import BROKER
from app.core.gpu import free_all_models
from app.core.model_store import configure_environment
from app.models import PRIORITY, Job, JobKind, PaperStatus
from app.workers.handlers import HANDLERS
from app.workers.queue import claim_next, complete, fail, requeue_stale

logger = logging.getLogger(__name__)

IDLE_SLEEP_SECONDS = 2.0

# Ordered by pipeline position, then by cost. Tagging is last so the map becomes
# navigable long before tags finish.
STAGE_ORDER: list[JobKind] = sorted(PRIORITY, key=lambda k: PRIORITY[k])


class Worker:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._stopping = False

    def request_stop(self, *_: object) -> None:
        if self._stopping:
            logger.warning("second stop signal; exiting immediately")
            sys.exit(1)
        logger.info("stop requested; finishing the current job first")
        self._stopping = True

    def run_forever(self) -> None:
        self.settings.ensure_dirs()
        # Before any model library is imported, so weights land in the
        # project's store instead of the user's home cache.
        applied = configure_environment(self.settings)
        logger.info("model cache: %s", applied["HF_HOME"])
        signal.signal(signal.SIGINT, self.request_stop)
        signal.signal(signal.SIGTERM, self.request_stop)

        with session_scope() as session:
            recovered = requeue_stale(session)
        if recovered:
            logger.info("requeued %d job(s) stranded by a previous worker", recovered)

        logger.info("worker ready; stages: %s", [k.value for k in STAGE_ORDER])
        while not self._stopping:
            if self.drain_one_pass() == 0:
                time.sleep(IDLE_SLEEP_SECONDS)

        free_all_models()
        logger.info("worker stopped")

    def drain_one_pass(self) -> int:
        """Work each stage to exhaustion, in order. Returns jobs completed."""
        done = 0
        for kind in STAGE_ORDER:
            if self._stopping:
                break
            drained = self.drain_stage(kind)
            if drained:
                # The stage's model is no longer needed; empty the card before
                # the next stage tries to load its own. Three different runtimes
                # may be holding memory, so this is not a no-op.
                free_all_models()
                done += drained
        return done

    def drain_stage(self, kind: JobKind) -> int:
        handler = HANDLERS.get(kind)
        if handler is None:
            return 0

        done = 0
        while not self._stopping:
            with session_scope() as session:
                job = claim_next(session, kinds=[kind])
                if job is None:
                    break

                started = time.perf_counter()
                try:
                    handler(session, job, self.settings)
                    complete(session, job)
                    done += 1
                    logger.info(
                        "%s job %d ok in %.1fs",
                        kind.value,
                        job.id,
                        time.perf_counter() - started,
                    )
                except Exception as exc:  # noqa: BLE001 - one bad paper must not
                    # stop the batch; the queue decides whether to retry.
                    logger.exception("%s job %d failed", kind.value, job.id)
                    self._record_failure(session, job, kind, exc)
        return done

    def _record_failure(
        self, session: Session, job: Job, kind: JobKind, exc: BaseException
    ) -> None:
        """Write down that a job failed, without ever failing to do so.

        This runs inside ``session_scope``, so anything raised here reaches its
        ``except`` clause and rolls the transaction back — including the rows
        that record the failure. That is not hypothetical. A payload key named
        ``kind`` collided with the publisher's own parameter and raised
        TypeError on the line *after* ``fail()``: the job was rolled back to
        ``running``, the worker exited, and several hundred queued documents
        waited on a thirty-minute stale timeout for a worker that was gone.

        One unreadable document must cost that document and nothing else, so
        each step here is contained. The identifiers are read before the
        rollback, which expires the ORM objects.
        """
        job_id, paper_id = job.id, job.paper_id
        detail = f"{type(exc).__name__}: {exc}"

        try:
            session.rollback()
            fail(session, job, detail)
            self._mark_paper_failed(session, paper_id, detail)
            session.commit()
        except Exception:  # noqa: BLE001 - nothing here may stop the batch
            logger.exception("could not record the failure of job %d", job_id)
            session.rollback()
            return

        # Committed first, then announced. The notification is disposable
        # telemetry for a progress drawer; it must never be able to undo the
        # record above, and it is the thing that broke last time.
        try:
            BROKER.publish(
                "job.failed", job_id=job_id, job_kind=kind.value, error=detail
            )
        except Exception:  # noqa: BLE001
            logger.exception("could not announce the failure of job %d", job_id)

    @staticmethod
    def _mark_paper_failed(session: Session, paper_id: int | None, error: str) -> None:
        if paper_id is None:
            return
        from app.models import Job, JobState, Paper

        # Only give up on the paper once the queue has given up on the job.
        still_queued = (
            session.query(Job)
            .filter(Job.paper_id == paper_id, Job.state == JobState.QUEUED)
            .count()
        )
        if still_queued:
            return
        paper = session.get(Paper, paper_id)
        if paper is not None:
            paper.status = PaperStatus.FAILED
            paper.last_error = error[:2000]
            session.add(paper)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    Worker().run_forever()


if __name__ == "__main__":
    main()
