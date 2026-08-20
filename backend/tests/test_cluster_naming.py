"""Cluster naming.

A map of unlabelled blobs is a puzzle rather than a tool, but a *wrong* label is
worse than none — the user cannot tell it is wrong. So the output is trimmed
hard and an unreachable model yields no label rather than a guess.
"""

from __future__ import annotations

import pytest

from app.services.project.naming import build_prompt, clean_name, name_cluster


def test_prompt_carries_both_kinds_of_evidence():
    prompt = build_prompt(
        ["exoplanet", "transit", "spectroscopy"],
        ["Transit Spectroscopy of Hot Jupiters", "Atmospheres of Exoplanets"],
    )
    assert "exoplanet" in prompt
    assert "Transit Spectroscopy of Hot Jupiters" in prompt


def test_prompt_survives_empty_terms():
    assert "(none)" in build_prompt([], ["Some Title"])


def test_prompt_bounds_the_title_sample():
    prompt = build_prompt([], [f"Paper number {i}" for i in range(100)])
    assert prompt.count("- Paper number") <= 12


def test_prompt_discourages_invented_specificity():
    prompt = build_prompt(["a"], ["b"])
    assert "invent" in prompt.lower()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Exoplanet Atmospheres", "Exoplanet Atmospheres"),
        ('  "Galaxy Formation."  ', "Galaxy Formation"),
        ("'Stellar Dynamics'", "Stellar Dynamics"),
        ("multi   space   name", "multi space name"),
    ],
)
def test_names_are_tidied(raw, expected):
    assert clean_name(raw) == expected


def test_overlong_names_are_truncated_to_five_words():
    name = clean_name("One Two Three Four Five Six Seven Eight")
    assert len(name.split()) == 5


def test_a_very_long_name_is_length_capped():
    assert len(clean_name("x" * 500)) <= 60


@pytest.mark.parametrize("junk", [None, "", "   ", 42, [], {}])
def test_unusable_answers_yield_no_name(junk):
    assert clean_name(junk) is None


def test_an_empty_cluster_is_not_named():
    assert name_cluster([], []) is None


def test_an_unreachable_model_yields_no_name(monkeypatch):
    """An unnamed cluster is a missing label, not a failed projection."""

    class Boom:
        def post(self, *a, **k):
            raise OSError("connection refused")

        def close(self):
            pass

    monkeypatch.setattr(
        "app.services.project.naming.PRIVATE_OLLAMA.start", lambda: None
    )
    assert name_cluster(["term"], ["Title"], client=Boom()) is None


def test_malformed_json_yields_no_name(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"response": "not json at all"}

    class Client:
        def post(self, *a, **k):
            return Response()

        def close(self):
            pass

    assert name_cluster(["term"], ["Title"], client=Client()) is None


def test_a_good_answer_is_returned(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"response": '{"name": "Exoplanet Atmospheres", "confident": true}'}

    class Client:
        def post(self, *a, **k):
            return Response()

        def close(self):
            pass

    assert name_cluster(["exoplanet"], ["A Title"], client=Client()) == (
        "Exoplanet Atmospheres"
    )
