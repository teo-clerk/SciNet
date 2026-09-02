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
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.db import session_scope
from app.core.events import BROKER
from app.core.gpu import GPU, free_all_models
from app.core.model_store import configure_environment
from app.core.models_registry import Runtime
from app.models import PRIORITY, Job, JobKind, JobState, Paper, PaperStatus
from app.services.ingest.quarantine import quarantine_document
from app.services.parse.errors import is_fatal
from app.workers import lease
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
        # The nonce of the pause lease already yielded to, so the card is
        # freed and the ack written once per lease, not once per 2 s poll.
        self._pause_seen: str | None = None

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
            if self._honour_pause():
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
            if self._honour_pause():
                break
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

    def _honour_pause(self) -> bool:
        """True while an unexpired pause lease exists.

        First sight of a lease yields the card (unless its keep_warm names
        exactly what the Ollama slot holds — a pauser about to use the same
        model should inherit it warm) and writes the ack echoing the nonce.
        After that, honouring it costs one SELECT per poll; run_forever's
        idle sleep paces the polling, no new loop needed.
        """
        with session_scope() as session:
            held = lease.active(session)
            if held is None:
                self._pause_seen = None
                return False
            if self._pause_seen != held.nonce:
                resident = GPU.resident
                keep = (
                    held.keep_warm is not None
                    and resident is not None
                    and resident.runtime is Runtime.OLLAMA
                    and resident.reference == held.keep_warm
                )
                if not keep:
                    free_all_models()
                lease.ack(session, held.nonce)
                self._pause_seen = held.nonce
                logger.info(
                    "paused for %s until %s%s",
                    held.reason,
                    held.until.isoformat(timespec="seconds"),
                    " (model kept warm)" if keep else "",
                )
        return True

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
        fatal = is_fatal(exc)

        try:
            session.rollback()
            given_up = fail(session, job, detail, fatal=fatal).state == JobState.DEAD
            self._mark_paper_failed(session, paper_id, detail)
            if given_up and kind is JobKind.PARSE:
                self._quarantine(session, paper_id, detail)
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
                "job.failed",
                job_id=job_id,
                job_kind=kind.value,
                error=detail,
                fatal=fatal,
            )
        except Exception:  # noqa: BLE001
            logger.exception("could not announce the failure of job %d", job_id)

    def _quarantine(self, session: Session, paper_id: int | None, reason: str) -> None:
        """Move an unreadable document out of the library and say where it went.

        Only for the parse stage, and only once the queue has given up. A paper
        that parsed and then failed to embed is not an unreadable file — the
        model was busy — and moving it would take a perfectly good document out
        of the library to fix a problem it does not have.
        """
        if paper_id is None:
            return
        paper = session.get(Paper, paper_id)
        if paper is None or paper.status == PaperStatus.QUARANTINED:
            return

        moved = quarantine_document(
            Path(paper.pdf_path), self.settings.library_dir, reason
        )
        if moved is None:
            # The file could not be moved; the row keeps FAILED, which is the
            # truthful description of what happened.
            return

        # The row follows the file. Leaving pdf_path pointing into the library
        # would make every later "has this disappeared?" check say yes.
        paper.pdf_path = str(moved.destination)
        paper.status = PaperStatus.QUARANTINED
        paper.last_error = reason[:2000]
        session.add(paper)
        BROKER.publish(
            "paper.quarantined",
            paper_id=paper.id,
            name=moved.original.name,
            reason=reason,
        )

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
