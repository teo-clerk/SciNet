"""Measurement is a job: warm the model, read back the truth, persist it.

Why a queue job and not an API call: loading a model is a GPU operation, and
the GPU belongs to the worker — a measurement running inside the API would
race whatever stage the worker has resident. As a MEASURE job it waits its
turn like everything else, and the worker's between-kinds ``free_all_models``
hands it an empty card.

Why the dedupe lives here and not in ``queue.enqueue``: enqueue's corpus-wide
collapse is payload-blind *on purpose* — the reproject button leans on four
presses collapsing into one job regardless of payload. Teaching enqueue to
compare payloads for some kinds would quietly invert that guarantee, so
MEASURE deduplicates itself, by exact payload match, and inserts the Job row
directly.

The handler unloads after itself. ``drain_stage`` frees the card between
*kinds*, but every MEASURE job loads a different model by design — without
the self-unload, measuring three candidates stacks three models against one
card's worth of VRAM.
"""

from __future__ import annotations

import json
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.model_store import PRIVATE_OLLAMA
from app.core.types import utcnow
from app.models import Job, JobKind, JobState, ModelProfile, ModelVerdict
from app.models.enums import PRIORITY
from app.services.models.profiles import record_measurement
from app.workers.queue import payload_of

logger = logging.getLogger(__name__)

WARMUP_PROMPT = "Reply with the single word: ready."
#: A fitting model answers the warm-up in seconds; ten minutes is the 181
#: s/page class actually failing to fit. The timeout is evidence, not error.
WARMUP_TIMEOUT_SECONDS = 600.0
#: Two attempts, not three: re-measuring after a transient server hiccup is
#: worth one retry; a model that times out twice has told us what it is.
MEASURE_MAX_ATTEMPTS = 2


def _payload_key(reference: str) -> str:
    return json.dumps({"reference": reference}, sort_keys=True)


def enqueue_measure(session: Session, reference: str) -> tuple[Job, bool]:
    """One outstanding measurement per reference. Returns (job, created)."""
    existing = session.scalar(
        select(Job).where(
            Job.kind == JobKind.MEASURE,
            Job.state.in_([JobState.QUEUED, JobState.RUNNING]),
            Job.payload_json == _payload_key(reference),
        )
    )
    if existing is not None:
        return existing, False

    job = Job(
        kind=JobKind.MEASURE,
        paper_id=None,
        priority=PRIORITY[JobKind.MEASURE],
        payload_json=_payload_key(reference),
        max_attempts=MEASURE_MAX_ATTEMPTS,
    )
    session.add(job)
    session.flush()
    return job, True


def _record_timeout(session: Session, reference: str, seconds: float) -> None:
    """A warm-up that outlives the timeout is itself a measurement.

    Ten minutes without an answer is the signature of a model served from
    system RAM; a dead job would just say "timed out", which reads as our
    fault rather than the model's.
    """
    profile = session.get(ModelProfile, reference)
    if profile is None:
        profile = ModelProfile(reference=reference, runtime="ollama", role="tag")
        session.add(profile)
    profile.verdict = ModelVerdict.CPU_ONLY
    profile.notes = (
        f"warm-up generate exceeded {seconds:.0f}s — the signature of a model "
        "served from system RAM; treat as CPU-only until proven otherwise"
    )
    session.flush()


def _looks_huggingface(reference: str) -> bool:
    # HF repos are org/name; ollama tags are name:tag. The slash decides.
    return "/" in reference


def _measure_huggingface(session: Session, reference: str, settings) -> None:
    """Dimension and CPU encode speed for a sentence-transformers model.

    Presence-gated: a MEASURE job must never trigger a multi-gigabyte
    download as a side effect — provisioning is download_models.py's job and
    the user's decision. VRAM stays unproven here (the measurement runs on
    CPU, where a query encoder lives anyway); the dimension is the fact the
    morph view needs, because a store is keyed by model *and* dim.
    """
    from time import perf_counter

    from app.core.config import get_settings
    from app.core.preflight import _hf_present
    from app.services.embed import encoder

    profile = session.get(ModelProfile, reference)
    if profile is None:
        profile = ModelProfile(
            reference=reference,
            runtime="huggingface",
            role="embed",
            source="discovered",
        )
        session.add(profile)

    if not _hf_present(reference):
        profile.notes = (
            "not in the project's model store; provision it first: "
            "scripts/download_models.py (measuring must never download)"
        )
        session.flush()
        return

    model = encoder._prepared(reference, "cpu", settings or get_settings())
    dimension = int(model.get_sentence_embedding_dimension())
    started = perf_counter()
    model.encode(
        ["a short sentence to time the encoder"] * 8,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    elapsed = perf_counter() - started

    profile.embed_dim = dimension
    profile.tok_per_s = round(8 / elapsed, 1) if elapsed > 0 else None
    profile.notes = f"{dimension}-dim; {profile.tok_per_s} docs/s on CPU"
    profile.last_measured_at = utcnow()
    session.flush()
    logger.info(
        "measured %s: %d-dim, %s docs/s (cpu)", reference, dimension, profile.tok_per_s
    )


def handle_measure(session: Session, job: Job, settings: Settings) -> None:
    reference = payload_of(job).get("reference", "")
    if not reference:
        raise ValueError("measure job carries no model reference")

    if _looks_huggingface(reference):
        _measure_huggingface(session, reference, settings)
        return

    server = PRIVATE_OLLAMA
    server.start()

    try:
        response = httpx.post(
            f"{server.url}/api/generate",
            json={
                "model": reference,
                "prompt": WARMUP_PROMPT,
                "stream": False,
                "keep_alive": "60s",
            },
            timeout=WARMUP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        answer = response.json()
    except httpx.TimeoutException:
        _record_timeout(session, reference, WARMUP_TIMEOUT_SECONDS)
        return

    total_mib, vram_mib = server.resident_footprint_mib(reference)
    eval_count = answer.get("eval_count") or 0
    eval_ns = answer.get("eval_duration") or 0
    tok_per_s = (eval_count / (eval_ns / 1e9)) if eval_ns else None

    # Unload before recording: the next MEASURE job loads a different model,
    # and the between-kinds free only runs when the *kind* changes.
    server.unload(reference)

    profile = record_measurement(
        session,
        reference,
        total_mib=total_mib,
        vram_mib=vram_mib,
        tok_per_s=tok_per_s,
    )
    logger.info(
        "measured %s: %d MiB resident (%s), %.1f tok/s",
        reference,
        vram_mib,
        profile.verdict,
        tok_per_s or 0.0,
    )
