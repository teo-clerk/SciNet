"""The plain-English reading of a work.

What is guarded here is not an exception but a confident paragraph about a
work the model never really saw: the prompt gives it the opening of the text
as well as the abstract, tells it to say when that is too thin, and the
normaliser refuses to call a reading with a hole in it grounded.
"""

from __future__ import annotations

import json

import pytest

from app.services.insight.distill import (
    ENTITY_KINDS,
    GENRES,
    INSIGHT_SCHEMA,
    MAX_ANSWER_CHARS,
    InsightUnavailable,
    build_prompt,
    distill_paper,
    normalise,
)
from app.services.metadata.synopsis import opening_excerpt

# --- the schema -----------------------------------------------------------------


def test_the_schema_decides_genre_first_and_assesses_grounding_last():
    """Property order is decision order under constrained decoding."""
    order = list(INSIGHT_SCHEMA["properties"])
    assert order[0] == "genre"
    assert order[-1] == "grounded"
    assert order.index("question") < order.index("claims") < order.index("entities")


def test_genres_and_kinds_are_closed_lists():
    assert INSIGHT_SCHEMA["properties"]["genre"]["enum"] == list(GENRES)
    kind = INSIGHT_SCHEMA["properties"]["entities"]["items"]["properties"]["kind"]
    assert kind["enum"] == list(ENTITY_KINDS)
    assert "other" in GENRES


# --- the prompt -----------------------------------------------------------------


def test_prompt_does_not_assume_science_and_carries_the_evidence():
    prompt = build_prompt(
        title="On Liberty",
        authors=["John Stuart Mill"],
        year=1859,
        abstract="The subject of this essay is civil, or social liberty.",
        headings=["Introductory", "Of the Liberty of Thought"],
        excerpt="The struggle between Liberty and Authority is the most…",
    )
    lowered = prompt.lower()
    assert "do not assume it is science" in lowered
    assert "essay" in lowered and "novel" in lowered
    assert "On Liberty" in prompt
    assert "John Stuart Mill" in prompt
    assert "civil, or social liberty" in prompt
    assert "Of the Liberty of Thought" in prompt
    assert "Liberty and Authority" in prompt
    assert "grounded" in prompt


def test_prompt_survives_missing_everything_and_stays_bounded():
    prompt = build_prompt(
        title=None,
        authors=None,
        year=None,
        abstract="x" * 20_000,
        headings=[],
        excerpt="y" * 20_000,
    )
    assert "(unknown)" in prompt
    assert "(none)" in prompt
    assert len(prompt) <= 6000


# --- the opening excerpt ----------------------------------------------------------


COPYRIGHT = (
    "Copyright © 1974 by the publisher. All rights reserved. No part of this "
    "publication may be reproduced, stored in a retrieval system, or transmitted "
    "in any form or by any means without the prior written permission of the "
    "publisher. Printed in the United States of America. ISBN 0-000-00000-0."
)
FIRST = (
    "The subject of this essay is not the so-called liberty of the will, so "
    "unfortunately opposed to the misnamed doctrine of philosophical necessity; "
    "but civil, or social liberty: the nature and limits of the power which can "
    "be legitimately exercised by society over the individual. A question seldom "
    "stated, and hardly ever discussed, in general terms."
)
SECOND = (
    "The struggle between liberty and authority is the most conspicuous feature "
    "in the portions of history with which we are earliest familiar. It was "
    "between subjects, or some classes of subjects, and the government, and by "
    "liberty was meant protection against the tyranny of the political rulers."
)


def test_excerpt_skips_the_title_and_the_furniture():
    markdown = "\n\n".join(
        ["# On Liberty", COPYRIGHT, "Figure 1: a frontispiece.", FIRST]
    )
    excerpt = opening_excerpt(markdown)
    assert excerpt is not None
    assert excerpt.startswith("The subject of this essay")
    assert "Copyright" not in excerpt
    assert "Figure 1" not in excerpt
    assert "# On Liberty" not in excerpt


def test_excerpt_respects_its_limit_and_ends_at_a_sentence():
    markdown = "\n\n".join([FIRST, SECOND, SECOND, SECOND])
    excerpt = opening_excerpt(markdown, limit=500)
    assert excerpt is not None
    assert len(excerpt) <= 500
    assert excerpt.rstrip().endswith((".", "…"))


def test_excerpt_of_nothing_is_none():
    assert opening_excerpt(None) is None
    assert opening_excerpt("   \n\n  ") is None
    assert opening_excerpt("# Just a heading\n\n| a | b |\n| 1 | 2 |") is None


# --- normalisation ----------------------------------------------------------------


def _reading(**overrides) -> dict:
    base = {
        "genre": "essay-or-commentary",
        "question": "How far may society limit an individual's freedom?",
        "argument": "Society may only interfere to prevent harm to others.",
        "significance": "It draws the line most liberal societies still argue over.",
        "claims": [
            "The only purpose for which power can be exercised is self-protection.",
            "Over himself, the individual is sovereign.",
        ],
        "entities": [
            {"name": "John Stuart Mill", "kind": "person"},
            {"name": "harm principle", "kind": "concept"},
        ],
        "grounded": True,
    }
    return {**base, **overrides}


def test_a_clean_reading_passes_through():
    out = normalise(_reading())
    assert out["genre"] == "essay-or-commentary"
    assert out["question"].startswith("How far")
    assert len(out["claims"]) == 2
    assert out["entities"][1] == {"name": "harm principle", "kind": "concept"}
    assert out["grounded"] is True


def test_claims_are_capped_deduplicated_and_must_be_statements():
    claims = [
        f"Claim number {i} says something specific about the text." for i in range(6)
    ]
    out = normalise(_reading(claims=[*claims, claims[0], "liberty", "two words"]))
    assert len(out["claims"]) == 4
    assert "liberty" not in out["claims"]


def test_entities_are_capped_deduplicated_and_kinds_are_not_guessed():
    entities = [{"name": f"Person {i}", "kind": "person"} for i in range(10)]
    out = normalise(
        _reading(
            entities=[
                *entities,
                {"name": "person 0", "kind": "person"},
                {"name": "Zeus", "kind": "deity"},
                "not an object",
            ]
        )
    )
    assert len(out["entities"]) == 8
    assert all(e["kind"] in ENTITY_KINDS for e in out["entities"])
    assert not any(e["name"] == "Zeus" for e in out["entities"])


def test_an_unknown_genre_becomes_other():
    assert normalise(_reading(genre="manifesto"))["genre"] == "other"
    assert normalise(_reading(genre=None))["genre"] == "other"


def test_answers_are_trimmed_to_whole_sentences():
    long = " ".join(f"Sentence number {i} of a very long answer." for i in range(30))
    out = normalise(_reading(argument=long))
    assert len(out["argument"]) <= MAX_ANSWER_CHARS
    assert out["argument"].endswith(".")


def test_a_reading_with_a_hole_in_it_is_not_grounded():
    assert normalise(_reading(significance=""))["grounded"] is False
    assert normalise(_reading(question=None))["grounded"] is False
    assert normalise(_reading(grounded=False))["grounded"] is False


@pytest.mark.parametrize("junk", [None, 42, [], "text"])
def test_normalise_tolerates_junk_fields(junk):
    out = normalise(_reading(claims=junk, entities=junk))
    assert out["claims"] == []
    assert out["entities"] == []


# --- the call ---------------------------------------------------------------------


class _Client:
    """Records the request and answers with a fixed object."""

    def __init__(self, answer: object):
        self.answer = answer
        self.payloads: list[dict] = []

    def post(self, url, json=None):
        self.payloads.append(json)
        answer = self.answer

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                body = (
                    answer
                    if isinstance(answer, str)
                    else __import__("json").dumps(answer)
                )
                return {"response": body}

        return Response()

    def close(self):
        pass


@pytest.fixture
def model_installed(monkeypatch):
    monkeypatch.setattr(
        "app.services.insight.distill.PRIVATE_OLLAMA.start", lambda: None
    )
    monkeypatch.setattr(
        "app.services.insight.distill.PRIVATE_OLLAMA.installed",
        lambda: ["qwen3:8b", "tiny:latest"],
    )


def _call(client, **kwargs):
    return distill_paper(
        title="On Liberty",
        authors=["John Stuart Mill"],
        year=1859,
        abstract="The subject of this essay is civil, or social liberty.",
        headings=["Introductory"],
        excerpt=FIRST,
        client=client,
        **kwargs,
    )


def test_a_good_answer_is_normalised(model_installed):
    client = _Client(_reading())
    out = _call(client)
    assert out["genre"] == "essay-or-commentary"
    assert out["grounded"] is True
    payload = client.payloads[0]
    assert payload["format"] is INSIGHT_SCHEMA
    assert payload["stream"] is False
    assert "On Liberty" in payload["prompt"]


def test_the_routed_model_is_what_the_request_carries(model_installed):
    client = _Client(_reading())
    _call(client, model="tiny:latest")
    assert client.payloads[0]["model"] == "tiny:latest"


def test_unparseable_json_is_an_error_not_a_reading(model_installed):
    with pytest.raises(ValueError):
        _call(_Client("not json at all"))
    with pytest.raises(ValueError):
        _call(_Client(json.dumps([1, 2, 3])))


def test_an_absent_model_is_a_transient_failure(monkeypatch):
    """A property of the moment, not of the document: the job keeps its
    retries rather than being quarantined on its first bad minute."""
    from app.services.parse.errors import is_fatal

    monkeypatch.setattr(
        "app.services.insight.distill.PRIVATE_OLLAMA.start", lambda: None
    )
    monkeypatch.setattr(
        "app.services.insight.distill.PRIVATE_OLLAMA.installed", lambda: []
    )
    with pytest.raises(InsightUnavailable) as caught:
        _call(_Client(_reading()))
    assert not is_fatal(caught.value)
