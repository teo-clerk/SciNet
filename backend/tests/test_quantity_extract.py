"""The extractor's precision floor, held by a hand-labelled fixture.

The design rule under test: a wrong unit is worse than a missing one. Every
AUTO row the extractor emits over the fixture is judged against the labels;
precision must clear 0.90, the canonical positives must all be found, and
the negatives — participant counts, years, epochs, table numbers — must
produce nothing at all. Unknown-but-unit-shaped tokens (RJ, Gy) go to
pending, never to AUTO.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.models.enums import QuantityStatus
from app.services.quantities.extract import extract_from_text, sentences_of

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "quantities_labeled.json").read_text()
)

PRECISION_FLOOR = 0.90
REL_TOL = 1e-3


def matches(expected: dict, row) -> bool:
    if row.quantity_kind != expected["kind"]:
        return False
    if row.value_si is None:
        return False
    reference = expected["value_si"]
    scale = max(abs(reference), 1e-12)
    if abs(row.value_si - reference) / scale > REL_TOL:
        return False
    if "unit_si" in expected and row.unit_si != expected["unit_si"]:
        return False
    return True


def test_precision_over_the_labelled_corpus() -> None:
    emitted = 0
    correct = 0
    missed: list[str] = []

    for case in FIXTURE["cases"]:
        rows = [
            r
            for r in extract_from_text(case["sentence"])
            if r.status == QuantityStatus.AUTO
        ]
        emitted += len(rows)
        expected = list(case.get("expect", []))
        for exp in expected:
            hit = next((r for r in rows if matches(exp, r)), None)
            if hit is not None:
                correct += 1
            else:
                missed.append(f"{exp['kind']}={exp['value_si']}: {case['sentence']}")

    expected_total = sum(len(c.get("expect", [])) for c in FIXTURE["cases"])
    assert missed == [], f"canonical positives missed: {missed}"
    precision = correct / emitted if emitted else 1.0
    assert precision >= PRECISION_FLOOR, (
        f"precision {precision:.2f} over {emitted} emissions "
        f"({correct} correct of {expected_total} expected)"
    )


def test_negatives_emit_nothing_at_all() -> None:
    for case in FIXTURE["cases"]:
        if case.get("expect") or case.get("pending"):
            continue
        rows = extract_from_text(case["sentence"])
        auto = [r for r in rows if r.status == QuantityStatus.AUTO]
        assert auto == [], f"false positive in: {case['sentence']} -> {auto}"


def test_unit_shaped_strangers_go_to_pending_never_auto() -> None:
    for case in FIXTURE["cases"]:
        for token in case.get("pending", []):
            rows = extract_from_text(case["sentence"])
            pending = [r for r in rows if r.status == QuantityStatus.PENDING_LLM]
            assert any(r.unit_original == token for r in pending), (
                f"{token} should be pending in: {case['sentence']}"
            )
            assert not any(
                r.unit_original == token and r.status == QuantityStatus.AUTO
                for r in rows
            )


def test_provenance_is_the_sentence_and_the_verbatim_value() -> None:
    rows = extract_from_text(
        "The oscillator drifts by 3 ± 0.4 Hz over the operating range."
    )
    row = rows[0]
    assert row.value_original == "3 ± 0.4"
    assert row.context_sentence.startswith("The oscillator")


def test_sentence_splitting_keeps_prose_and_drops_fragments() -> None:
    pieces = sentences_of(
        "Short. This sentence is long enough to keep and speaks of 5 km spans. "
        "x\n\n" + "A" * 600
    )
    assert len(pieces) == 1 and "5 km" in pieces[0]
