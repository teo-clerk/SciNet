"""Adjudicating the extractor's uncertain hits, in batches, honestly split.

The division of labour is the point: the LLM does *recognition* — "RJ here
means Jupiter radii", "Gy is the gray" — and pint does the *arithmetic*. A
model asked to convert units in its head gets to be silently wrong; a model
asked only to name the unit hands the conversion to code that cannot be.
A named unit pint does not know goes to human review, not into the data.

Schema order per the house rule: ``quantity_kind`` — the verdict — is
declared first, ``confident`` last (a self-assessment of an answer that
must exist before it can be assessed).

Batches of 25 with a commit per batch: hundreds of LLM calls in one
transaction means one failure re-adjudicates everything, and pending rows
are durable — a crash resumes exactly where it stopped.
"""

from __future__ import annotations

import json
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models import Job, Quantity, QuantityStatus
from app.services.quantities.extract import CANONICAL, _registry

logger = logging.getLogger(__name__)

BATCH_SIZE = 25
REQUEST_TIMEOUT = 120.0
LLM_CONFIDENCE = 0.75

ADJUDICATION_SCHEMA = {
    "type": "object",
    "properties": {
        # The verdict first — constrained decoding fills declaration order.
        "quantity_kind": {
            "type": "string",
            "enum": [*CANONICAL.keys(), "fraction", "level", "not_a_quantity"],
        },
        #: The unit's name in words pint can parse ("gray", "jupiter_radius",
        #: "mmHg") — recognition only; the conversion happens in code.
        "pint_unit": {"type": "string"},
        "confident": {"type": "boolean"},
    },
    "required": ["quantity_kind", "confident"],
}


def build_prompt(row: Quantity) -> str:
    return "\n".join(
        [
            "A sentence from a scientific paper contains a number followed by "
            "a token that may be a unit of measurement.",
            "",
            f"Sentence: {row.context_sentence}",
            f"Number: {row.value_original}",
            f"Token: {row.unit_original}",
            "",
            "First decide quantity_kind: the physical dimension the token "
            "denotes, or not_a_quantity if the token is not a unit here "
            "(a name, an abbreviation, a count of things).",
            "Then, if it is a unit, give pint_unit: the unit's full name in "
            "a form the Python pint library parses, e.g. 'gray', 'mmHg', "
            "'jupiter_radius', 'electron_volt'.",
            "Set confident to false if you are unsure of either.",
        ]
    )


def apply_verdict(row: Quantity, parsed: dict | None, model: str) -> None:
    row.model_id = model
    row.extraction_source = "llm"

    if parsed is None:
        row.status = QuantityStatus.PENDING_REVIEW
        return

    kind = parsed.get("quantity_kind")
    if kind == "not_a_quantity":
        row.status = QuantityStatus.REJECTED
        return
    if not parsed.get("confident") or not kind:
        row.status = QuantityStatus.PENDING_REVIEW
        return

    pint_unit = (parsed.get("pint_unit") or "").strip()
    canonical = CANONICAL.get(kind)
    if not pint_unit or canonical is None:
        # fraction/level have no pint conversion; without a target the
        # recognition cannot be checked, so a human checks it instead.
        row.status = QuantityStatus.PENDING_REVIEW
        row.quantity_kind = kind or row.quantity_kind
        return

    try:
        value = float(row.value_original.split("±")[0].split("-")[0].strip())
        converted = _registry().Quantity(value, pint_unit).to(canonical)
    except Exception:  # noqa: BLE001 - an unparseable unit is review, not data
        row.status = QuantityStatus.PENDING_REVIEW
        row.quantity_kind = kind
        return

    row.quantity_kind = kind
    row.value_si = float(converted.magnitude)
    row.unit_si = canonical
    row.confidence = LLM_CONFIDENCE
    row.status = QuantityStatus.AUTO


def _ask(
    model: str, prompt: str, settings: Settings, client: httpx.Client | None
) -> dict | None:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": ADJUDICATION_SCHEMA,
        "keep_alive": settings.ollama_keep_alive,
        "options": {"temperature": 0.1, "num_ctx": settings.llm_num_ctx},
    }
    owned = client is None
    http = client or httpx.Client(timeout=REQUEST_TIMEOUT)
    try:
        response = http.post(f"{settings.ollama_url}/api/generate", json=payload)
        response.raise_for_status()
        return json.loads(response.json().get("response", "{}"))
    except Exception as exc:  # noqa: BLE001 - one bad call is one pending row
        logger.warning("adjudication call failed: %s", exc)
        return None
    finally:
        if owned:
            http.close()


def handle_adjudicate(
    session: Session,
    job: Job,
    settings: Settings,
    client: httpx.Client | None = None,
) -> None:
    settings = settings or get_settings()
    from app.core.model_router import TaskKind, resolve

    model = resolve(TaskKind.EXTRACT_ADJUDICATE, settings, session)

    processed = 0
    while True:
        batch = session.scalars(
            select(Quantity)
            .where(Quantity.status == QuantityStatus.PENDING_LLM)
            .limit(BATCH_SIZE)
        ).all()
        if not batch:
            break
        for row in batch:
            apply_verdict(row, _ask(model, build_prompt(row), settings, client), model)
            processed += 1
        # A commit per batch: a crash re-adjudicates at most 25 calls.
        session.commit()

    logger.info("adjudicated %d quantity row(s)", processed)
