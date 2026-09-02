"""The EXTRACT stage: wholesale rows, correct ordering, one adjudication.

Driven the way the worker drives it — claim, handle, complete — against
seeded chunks. The wholesale rule is the one a re-parse depends on: a
second run must replace, never accumulate.
"""

from __future__ import annotations

from sqlalchemy import select

from app.models import (
    PRIORITY,
    Chunk,
    Job,
    JobKind,
    JobState,
    Paper,
    Quantity,
    QuantityStatus,
)
from app.models.enums import PaperStatus
from app.workers.quantity_handlers import handle_extract
from app.workers.queue import claim_next, complete, enqueue
from app.workers.runner import STAGE_ORDER


def seed_paper(db, tmp_path, paper_id: int, chunk_text: str) -> None:
    db.add(
        Paper(
            id=paper_id,
            content_sha256=f"{paper_id:064d}",
            work_key=f"w{paper_id}",
            pdf_path=str(tmp_path / f"{paper_id}.pdf"),
            status=PaperStatus.EMBEDDED,
        )
    )
    db.add(Chunk(paper_id=paper_id, ord=0, section="Results", text=chunk_text))
    db.flush()


def run_stage(db, kind: JobKind) -> int:
    done = 0
    while True:
        job = claim_next(db, kinds=[kind])
        if job is None:
            return done
        handle_extract(db, job, settings=None)
        complete(db, job)
        db.commit()
        done += 1


def test_extract_sits_between_embed_and_project() -> None:
    order = [k for k in STAGE_ORDER]
    assert order.index(JobKind.EMBED) < order.index(JobKind.EXTRACT)
    assert order.index(JobKind.EXTRACT) < order.index(JobKind.PROJECT)
    assert PRIORITY[JobKind.ADJUDICATE] < PRIORITY[JobKind.TAG]


def test_a_paper_yields_rows_with_their_sentences(db, tmp_path) -> None:
    seed_paper(
        db,
        tmp_path,
        1,
        "The satellite flies at an altitude of 705 km above the ground. "
        "The survey had 120 participants in its second wave of interviews.",
    )
    enqueue(db, JobKind.EXTRACT, paper_id=1)
    assert run_stage(db, JobKind.EXTRACT) == 1

    rows = db.scalars(select(Quantity)).all()
    assert len(rows) == 1, "the participant count must not become a quantity"
    assert rows[0].quantity_kind == "length" and rows[0].value_si == 705000.0
    assert "altitude of 705 km" in rows[0].context_sentence


def test_a_second_run_replaces_rather_than_accumulates(db, tmp_path) -> None:
    seed_paper(db, tmp_path, 2, "A swath of 290 km is imaged in every single pass.")
    for _ in range(2):
        enqueue(db, JobKind.EXTRACT, paper_id=2)
        run_stage(db, JobKind.EXTRACT)
    rows = db.scalars(select(Quantity).where(Quantity.paper_id == 2)).all()
    assert len(rows) == 1, "wholesale: delete-then-insert, like chunks"


def test_pending_rows_queue_exactly_one_adjudication(db, tmp_path) -> None:
    seed_paper(db, tmp_path, 3, "The orbit reaches 12 RJ before the boundary layer.")
    seed_paper(db, tmp_path, 4, "Doses of 5 Gy were applied to every single sample.")
    enqueue(db, JobKind.EXTRACT, paper_id=3)
    enqueue(db, JobKind.EXTRACT, paper_id=4)
    assert run_stage(db, JobKind.EXTRACT) == 2

    pending = db.scalars(
        select(Quantity).where(Quantity.status == QuantityStatus.PENDING_LLM)
    ).all()
    assert len(pending) == 2
    adjudications = db.scalars(select(Job).where(Job.kind == JobKind.ADJUDICATE)).all()
    assert len(adjudications) == 1, "corpus-wide collapse: one pass, not per paper"
    assert adjudications[0].state == JobState.QUEUED
