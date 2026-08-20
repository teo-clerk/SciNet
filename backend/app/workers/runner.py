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
from app.core.gpu import GPU
from app.core.model_store import configure_environment
from app.models import PRIORITY, JobKind, PaperStatus
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

        GPU.release()
        logger.info("worker stopped")

    def drain_one_pass(self) -> int:
        """Work each stage to exhaustion, in order. Returns jobs completed."""
        done = 0
        for kind in STAGE_ORDER:
            if self._stopping:
                break
            drained = self.drain_stage(kind)
            if drained:
                # The stage's model is no longer needed; free the card before
                # the next stage tries to load its own.
                GPU.release()
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
                    session.rollback()
                    fail(session, job, f"{type(exc).__name__}: {exc}")
                    self._mark_paper_failed(session, job.paper_id, str(exc))
                    BROKER.publish(
                        "job.failed", job_id=job.id, kind=kind.value, error=str(exc)
                    )
        return done

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
