"""Measurement as a queue job: deduped per reference, self-unloading.

The dedupe deliberately does NOT live in queue.enqueue: enqueue's
corpus-wide collapse is payload-blind on purpose (the reproject button
depends on it), so MEASURE deduplicates itself by exact payload. And the
handler unloads after itself, because drain_stage only frees the card
between kinds — three measured candidates would otherwise stack three
models on one card.
"""

from __future__ import annotations

import httpx

from app.models import Job, JobKind, JobState, ModelProfile, ModelVerdict
from app.services.models import measure as measure_module
from app.services.models.measure import enqueue_measure, handle_measure
from app.workers.queue import claim_next, complete


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


# --- dedupe ------------------------------------------------------------------


def test_one_outstanding_measurement_per_reference(db) -> None:
    first, created_first = enqueue_measure(db, "candidate:4b")
    again, created_again = enqueue_measure(db, "candidate:4b")
    other, created_other = enqueue_measure(db, "different:2b")

    assert created_first and not created_again and created_other
    assert first.id == again.id and other.id != first.id
    assert first.max_attempts == 2


def test_a_finished_measurement_can_be_rerun(db) -> None:
    job, _ = enqueue_measure(db, "candidate:4b")
    job.state = JobState.DONE
    db.flush()
    rerun, created = enqueue_measure(db, "candidate:4b")
    assert created and rerun.id != job.id


# --- the handler -------------------------------------------------------------


def _fake_server(monkeypatch, *, footprint=(5673, 5673)) -> list[str]:
    unloaded: list[str] = []
    server = measure_module.PRIVATE_OLLAMA
    monkeypatch.setattr(server, "start", lambda: None)
    monkeypatch.setattr(server, "resident_footprint_mib", lambda ref: footprint)
    monkeypatch.setattr(server, "unload", unloaded.append)
    return unloaded


def test_a_measurement_lands_in_the_catalog_and_unloads(db, monkeypatch) -> None:
    unloaded = _fake_server(monkeypatch)
    monkeypatch.setattr(
        measure_module.httpx,
        "post",
        lambda url, json, timeout: FakeResponse(
            {"response": "ready", "eval_count": 100, "eval_duration": 2_000_000_000}
        ),
    )

    job, _ = enqueue_measure(db, "candidate:4b")
    claimed = claim_next(db, kinds=[JobKind.MEASURE])
    handle_measure(db, claimed, settings=None)
    complete(db, claimed)
    db.commit()

    profile = db.get(ModelProfile, "candidate:4b")
    assert profile.verdict == ModelVerdict.GPU
    assert profile.vram_mib == 5673
    assert profile.tok_per_s == 50.0  # 100 tokens over 2 s
    assert unloaded == ["candidate:4b"], "the handler must free its own model"
    assert db.get(Job, job.id).state == JobState.DONE


def test_a_timeout_is_recorded_as_evidence_not_a_dead_job(db, monkeypatch) -> None:
    """Ten minutes without a warm-up answer IS the measurement: the signature
    of a model served from system RAM. A dead job saying 'timed out' would
    read as our fault rather than the model's."""
    _fake_server(monkeypatch)

    def too_slow(url, json, timeout):
        raise httpx.ReadTimeout("still generating")

    monkeypatch.setattr(measure_module.httpx, "post", too_slow)

    enqueue_measure(db, "enormous:70b")
    claimed = claim_next(db, kinds=[JobKind.MEASURE])
    handle_measure(db, claimed, settings=None)  # must not raise
    complete(db, claimed)
    db.commit()

    profile = db.get(ModelProfile, "enormous:70b")
    assert profile.verdict == ModelVerdict.CPU_ONLY
    assert "system RAM" in profile.notes


def test_an_hf_embed_model_reports_its_dimension_and_speed(db, monkeypatch) -> None:
    class FakeModel:
        def get_sentence_embedding_dimension(self):
            return 384

        def encode(self, texts, **kwargs):
            return None

    monkeypatch.setattr("app.core.preflight._hf_present", lambda ref: True)
    monkeypatch.setattr(
        "app.services.embed.encoder._prepared",
        lambda ref, device, settings: FakeModel(),
    )

    enqueue_measure(db, "some-org/small-embed")
    claimed = claim_next(db, kinds=[JobKind.MEASURE])
    handle_measure(db, claimed, settings=None)
    db.commit()

    profile = db.get(ModelProfile, "some-org/small-embed")
    assert profile.embed_dim == 384
    assert profile.runtime == "huggingface"
    assert profile.tok_per_s is not None


def test_measuring_never_downloads(db, monkeypatch) -> None:
    """An absent HF model gets a note pointing at provisioning — a MEASURE
    job must never pull gigabytes as a side effect."""

    def forbidden(ref, device, settings):
        raise AssertionError("measure must not load an absent model")

    monkeypatch.setattr("app.core.preflight._hf_present", lambda ref: False)
    monkeypatch.setattr("app.services.embed.encoder._prepared", forbidden)

    enqueue_measure(db, "some-org/not-downloaded")
    claimed = claim_next(db, kinds=[JobKind.MEASURE])
    handle_measure(db, claimed, settings=None)
    db.commit()

    profile = db.get(ModelProfile, "some-org/not-downloaded")
    assert "provision" in profile.notes
    assert profile.embed_dim is None


def test_a_job_without_a_reference_is_a_real_failure(db) -> None:
    job = Job(kind=JobKind.MEASURE, priority=90, payload_json="{}")
    db.add(job)
    db.flush()
    claimed = claim_next(db, kinds=[JobKind.MEASURE])
    try:
        handle_measure(db, claimed, settings=None)
    except ValueError as exc:
        assert "no model reference" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")
