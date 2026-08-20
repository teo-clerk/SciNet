"""Controlled-vocabulary tagging.

The failure this guards against is not an exception, it is a vocabulary that
quietly fragments into "deep learning" / "Deep Learning" / "DL" until filtering
by a tag returns a third of the papers that belong to it.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.models import Tag, TagStatus
from app.services.tagging.canonicalize import (
    approved_slugs,
    register_proposal,
    resolve,
    seed_vocabulary,
)
from app.services.tagging.tagger import build_prompt, normalise, response_schema
from app.services.tagging.taxonomy import MAX_TAGS_PER_PAPER, SEED_TAGS

# --- vocabulary -----------------------------------------------------------


def test_seeding_populates_the_vocabulary(db):
    added = seed_vocabulary(db)
    assert added == len(SEED_TAGS)
    assert db.query(Tag).count() == len(SEED_TAGS)


def test_seeding_twice_adds_nothing(db):
    seed_vocabulary(db)
    assert seed_vocabulary(db) == 0
    assert db.query(Tag).count() == len(SEED_TAGS)


def test_approved_slugs_excludes_aliases(db):
    seed_vocabulary(db)
    canonical = db.query(Tag).filter(Tag.slug == "deep-learning").one()
    db.add(
        Tag(slug="dl", label="DL", canonical_id=canonical.id, status=TagStatus.MERGED)
    )
    db.flush()

    slugs = approved_slugs(db)
    assert "deep-learning" in slugs
    assert "dl" not in slugs


def test_resolve_follows_an_alias(db):
    seed_vocabulary(db)
    canonical = db.query(Tag).filter(Tag.slug == "deep-learning").one()
    db.add(
        Tag(slug="dl", label="DL", canonical_id=canonical.id, status=TagStatus.MERGED)
    )
    db.flush()

    assert resolve(db, "dl").slug == "deep-learning"
    assert resolve(db, "deep-learning").slug == "deep-learning"
    assert resolve(db, "nonexistent") is None


# --- proposals ------------------------------------------------------------


def fake_embedder(mapping: dict[str, np.ndarray]):
    def embed(texts: list[str]) -> np.ndarray:
        out = []
        for t in texts:
            v = mapping.get(t, np.zeros(4))
            n = np.linalg.norm(v)
            out.append(v / n if n else v)
        return np.array(out)

    return embed


def test_a_near_duplicate_proposal_is_merged(db):
    seed_vocabulary(db)
    same = np.array([1.0, 0.0, 0.0, 0.0])
    embed = fake_embedder(
        {
            "graph nets": same,
            "Graph Neural Networks": same,
        }
    )

    tag = register_proposal(db, "graph-nets", embedder=embed)
    assert tag.canonical_id is not None
    assert tag.status == TagStatus.MERGED
    assert resolve(db, "graph-nets").slug == "graph-neural-networks"


def test_a_distinct_proposal_waits_for_review(db):
    seed_vocabulary(db)
    embed = fake_embedder({"quantum error correction": np.array([0.0, 0.0, 0.0, 1.0])})

    tag = register_proposal(db, "quantum-error-correction", embedder=embed)
    assert tag.canonical_id is None
    assert tag.status == TagStatus.PENDING_REVIEW
    assert "quantum-error-correction" not in approved_slugs(db)


def test_a_proposal_without_an_embedder_waits_for_review(db):
    """Degrading to 'ask a human' is the right failure for vocabulary calls."""
    seed_vocabulary(db)
    tag = register_proposal(db, "some-new-field")
    assert tag.status == TagStatus.PENDING_REVIEW


def test_proposing_an_existing_tag_returns_it(db):
    seed_vocabulary(db)
    tag = register_proposal(db, "deep-learning")
    assert tag.status == TagStatus.APPROVED
    assert db.query(Tag).filter(Tag.slug == "deep-learning").count() == 1


# --- schema and prompt ----------------------------------------------------


def test_schema_restricts_tags_to_the_vocabulary():
    schema = response_schema(["a", "b"])
    assert schema["properties"]["tags"]["items"]["enum"] == ["a", "b"]
    assert schema["properties"]["tags"]["maxItems"] == MAX_TAGS_PER_PAPER
    assert "summary" in schema["required"]


def test_prompt_includes_the_paper_but_stays_bounded():
    prompt = build_prompt(
        title="A Paper", abstract="x" * 20_000, headings=["Intro", "Method"]
    )
    assert "A Paper" in prompt
    assert "Intro" in prompt
    assert len(prompt) <= 6000


def test_prompt_survives_missing_metadata():
    prompt = build_prompt(title=None, abstract=None, headings=[])
    assert "unknown" in prompt


# --- normalisation --------------------------------------------------------


def test_tags_outside_the_vocabulary_are_dropped():
    out = normalise({"summary": "s", "tags": ["nlp", "made-up"]}, ["nlp", "physics"])
    assert out["tags"] == ["nlp"]


def test_duplicate_tags_are_collapsed():
    out = normalise({"summary": "s", "tags": ["nlp", "nlp"]}, ["nlp"])
    assert out["tags"] == ["nlp"]


def test_tag_count_is_capped():
    vocab = [f"t{i}" for i in range(20)]
    out = normalise({"summary": "s", "tags": vocab}, vocab)
    assert len(out["tags"]) <= MAX_TAGS_PER_PAPER


def test_proposed_tags_are_slugified():
    out = normalise(
        {"summary": "s", "tags": [], "proposed_tags": ["Quantum Error Correction"]},
        ["nlp"],
    )
    assert out["proposed_tags"] == ["quantum-error-correction"]


def test_a_proposal_already_in_the_vocabulary_is_dropped():
    out = normalise({"summary": "s", "tags": [], "proposed_tags": ["nlp"]}, ["nlp"])
    assert out["proposed_tags"] == []


def test_missing_fields_normalise_to_empties():
    out = normalise({}, ["nlp"])
    assert out == {"summary": "", "tags": [], "proposed_tags": []}


@pytest.mark.parametrize("bad", [None, [], {}, "text"])
def test_normalise_tolerates_junk_tag_fields(bad):
    out = normalise({"summary": "s", "tags": bad}, ["nlp"])
    assert out["tags"] == []
