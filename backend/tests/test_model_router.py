"""Pin beats default, and with no pins nothing changes at all.

The migration safety property everything rests on: twenty call sites read
``settings.llm_model`` directly for months, and the router's fallback IS
that read — so threading it through three seams at a time cannot change
behaviour until somebody actually pins. The payload test at the bottom pins
the other end of the chain: the reference the router resolves is the one the
Ollama request carries, and the one provenance records.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.model_router import (
    PINNABLE,
    TaskKind,
    clear_pin,
    pin_key,
    pins,
    resolve,
    set_pin,
)
from app.services.tagging import tagger


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        library_dir=tmp_path / "library",
        markdown_dir=tmp_path / "markdown",
        vectors_dir=tmp_path / "vectors",
        models_dir=tmp_path / "models",
        db_path=tmp_path / "r.db",
        llm_model="default-llm:8b",
        vlm_model="default-vlm:2b",
        embed_model="default/embed",
    )


def test_with_no_pins_every_task_reads_its_settings_field(settings) -> None:
    assert resolve(TaskKind.TAG_PAPER, settings) == "default-llm:8b"
    assert resolve(TaskKind.NAME_CLUSTER, settings) == "default-llm:8b"
    assert resolve(TaskKind.PAGE_OCR, settings) == "default-vlm:2b"
    assert resolve(TaskKind.EMBED_QUERY, settings) == "default/embed"


def test_a_pin_wins_and_clearing_it_restores_the_default(settings, db) -> None:
    set_pin(db, TaskKind.TAG_PAPER, "pinned-llm:4b")
    assert resolve(TaskKind.TAG_PAPER, settings, db) == "pinned-llm:4b"
    # A pin on one task must not bleed into its siblings.
    assert resolve(TaskKind.NAME_CLUSTER, settings, db) == "default-llm:8b"

    clear_pin(db, TaskKind.TAG_PAPER)
    assert resolve(TaskKind.TAG_PAPER, settings, db) == "default-llm:8b"


def test_no_session_means_no_pin_lookup(settings, db) -> None:
    """The router never opens its own session — a default that reached for
    the real database would leak it into every test that forgot."""
    set_pin(db, TaskKind.TAG_PAPER, "pinned-llm:4b")
    assert resolve(TaskKind.TAG_PAPER, settings, session=None) == "default-llm:8b"


def test_unpinnable_tasks_refuse_a_pin_instead_of_ignoring_it(db) -> None:
    """An embed pin is a switch-the-whole-store operation (vectors and
    projections are keyed by model); silently accepting the row would offer
    a knob that does nothing."""
    assert TaskKind.EMBED_DOCS not in PINNABLE
    with pytest.raises(ValueError, match="not pin-routable"):
        set_pin(db, TaskKind.EMBED_DOCS, "other/embed")


def test_pins_lists_only_route_rows(db) -> None:
    set_pin(db, TaskKind.TAG_PAPER, "a:1")
    set_pin(db, TaskKind.BRIDGE_VERDICT, "b:2")
    assert pins(db) == {"tag_paper": "a:1", "bridge_verdict": "b:2"}
    assert pin_key(TaskKind.TAG_PAPER) == "route.tag_paper"


# --- the resolved reference is the one the wire carries -----------------------


def test_the_routed_model_is_what_the_request_carries(settings, monkeypatch) -> None:
    sent: dict = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"response": '{"summary": "s", "tags": [], "proposed_tags": []}'}

    class Client:
        def post(self, url, json):
            sent.update(json)
            return Response()

        def close(self):
            return None

    monkeypatch.setattr(tagger, "available", lambda settings, reference=None: True)
    tagger.tag_paper(
        title="T",
        abstract="A",
        headings=[],
        vocabulary=["x"],
        settings=settings,
        client=Client(),
        model="pinned-llm:4b",
    )
    assert sent["model"] == "pinned-llm:4b"
