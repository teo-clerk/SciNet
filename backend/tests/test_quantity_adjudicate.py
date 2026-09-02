"""Adjudication: the LLM recognises, pint converts, humans get the doubt.

Scripted fake clients walk every verdict path. The invariant that matters
most: a unit pint cannot parse never becomes data, however confident the
model sounded — it becomes a review row.
"""

from __future__ import annotations

import json

from sqlalchemy import select

from app.models import Chunk, JobKind, Paper, Quantity, QuantityStatus
from app.models.enums import PaperStatus
from app.services.quantities.adjudicate import BATCH_SIZE, handle_adjudicate
from app.workers.queue import claim_next, enqueue


class ScriptedClient:
    """Answers by matching the row's token inside the prompt."""

    def __init__(self, answers: dict[str, dict]):
        self.answers = answers
        self.calls = 0

    def post(self, url, json=None):
        self.calls += 1
        prompt = json["prompt"]
        for token, verdict in self.answers.items():
            if f"Token: {token}" in prompt:
                return FakeResponse(verdict)
        return FakeResponse({"quantity_kind": "not_a_quantity", "confident": True})

    def close(self):
        return None


class FakeResponse:
    def __init__(self, verdict: dict):
        self._verdict = verdict

    def raise_for_status(self):
        return None

    def json(self):
        return {"response": json.dumps(self._verdict)}


def pending_row(db, tmp_path, paper_id: int, value: str, token: str) -> Quantity:
    if db.get(Paper, paper_id) is None:
        db.add(
            Paper(
                id=paper_id,
                content_sha256=f"{paper_id:064d}",
                work_key=f"w{paper_id}",
                pdf_path=str(tmp_path / f"{paper_id}.pdf"),
                status=PaperStatus.EMBEDDED,
            )
        )
        db.add(Chunk(paper_id=paper_id, ord=0, section=None, text="body"))
    row = Quantity(
        paper_id=paper_id,
        chunk_ord=0,
        section=None,
        quantity_kind="unknown",
        value_si=None,
        unit_si=None,
        value_original=value,
        unit_original=token,
        context_sentence=f"It measured {value} {token} in the final trial run.",
        confidence=0.4,
        status=QuantityStatus.PENDING_LLM,
    )
    db.add(row)
    db.flush()
    return row


def drive(db, client) -> None:
    enqueue(db, JobKind.ADJUDICATE, paper_id=None)
    job = claim_next(db, kinds=[JobKind.ADJUDICATE])
    handle_adjudicate(db, job, settings=None, client=client)


def test_a_recognised_unit_is_converted_by_pint_not_the_model(db, tmp_path) -> None:
    row = pending_row(db, tmp_path, 1, "760", "mmHg")
    drive(
        db,
        ScriptedClient(
            {
                "mmHg": {
                    "quantity_kind": "pressure",
                    "pint_unit": "mmHg",
                    "confident": True,
                }
            }
        ),
    )
    db.refresh(row)
    assert row.status == QuantityStatus.AUTO
    assert row.quantity_kind == "pressure" and row.unit_si == "pascal"
    assert (
        abs(row.value_si - 101325.0) / 101325.0 < 0.01
    )  # pint's number, not the LLM's
    assert row.extraction_source == "llm" and row.confidence == 0.75


def test_a_unit_pint_cannot_parse_goes_to_review_not_data(db, tmp_path) -> None:
    row = pending_row(db, tmp_path, 2, "12", "RJ")
    drive(
        db,
        ScriptedClient(
            {
                "RJ": {
                    "quantity_kind": "length",
                    "pint_unit": "totally_made_up_radius",
                    "confident": True,
                }
            }
        ),
    )
    db.refresh(row)
    assert row.status == QuantityStatus.PENDING_REVIEW
    assert row.value_si is None, "confidence is not a substitute for arithmetic"


def test_not_a_quantity_is_rejected_and_doubt_is_reviewed(db, tmp_path) -> None:
    named = pending_row(db, tmp_path, 3, "10", "GPT")
    unsure = pending_row(db, tmp_path, 3, "5", "Gy")
    drive(
        db,
        ScriptedClient(
            {
                "GPT": {"quantity_kind": "not_a_quantity", "confident": True},
                "Gy": {
                    "quantity_kind": "energy",
                    "pint_unit": "gray",
                    "confident": False,
                },
            }
        ),
    )
    db.refresh(named)
    db.refresh(unsure)
    assert named.status == QuantityStatus.REJECTED
    assert unsure.status == QuantityStatus.PENDING_REVIEW


def test_batches_commit_as_they_go(db, tmp_path) -> None:
    for i in range(BATCH_SIZE + 3):
        pending_row(db, tmp_path, 10, str(i + 1), "mmHg")
    client = ScriptedClient(
        {"mmHg": {"quantity_kind": "pressure", "pint_unit": "mmHg", "confident": True}}
    )
    drive(db, client)
    assert client.calls == BATCH_SIZE + 3
    remaining = db.scalars(
        select(Quantity).where(Quantity.status == QuantityStatus.PENDING_LLM)
    ).all()
    assert remaining == []
