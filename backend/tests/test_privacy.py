"""The privacy guarantee.

SciNet claims nothing leaves the machine unless enrichment is explicitly
enabled. That is only worth stating if it is enforced somewhere and tested
here.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.models import EgressLog, Paper, PaperMeta, PaperStatus
from app.services.metadata.enrich import (
    EnrichmentDisabled,
    _get,
    enrich_paper,
    fetch_crossref,
)


@pytest.fixture
def offline_settings() -> Settings:
    return Settings(enrichment_enabled=False)


@pytest.fixture
def online_settings() -> Settings:
    return Settings(enrichment_enabled=True, enrichment_email="me@example.com")


@pytest.fixture
def paper_with_doi(db) -> Paper:
    paper = Paper(
        content_sha256="a" * 64,
        work_key="doi:10.1/x",
        pdf_path="/tmp/x.pdf",
        status=PaperStatus.PARSED,
        pipeline_version=1,
    )
    db.add(paper)
    db.flush()
    meta = PaperMeta(paper_id=paper.id, doi="10.1/x", title="Local Title")
    db.add(meta)
    db.flush()
    paper.meta = meta
    return paper


def test_disabled_enrichment_refuses_to_make_a_request(db, offline_settings):
    with pytest.raises(EnrichmentDisabled):
        _get(
            db,
            offline_settings,
            "crossref",
            "https://api.crossref.org/works/10.1/x",
            paper_id=None,
        )


def test_enrich_paper_is_a_noop_when_disabled(db, paper_with_doi, offline_settings):
    """The switch is checked before any network code path is entered."""
    assert enrich_paper(db, paper_with_doi, offline_settings) is False
    assert db.query(EgressLog).count() == 0


def test_default_settings_have_enrichment_off():
    """The safe value must be the default, not something to remember to set."""
    assert Settings().enrichment_enabled is False


def test_every_outbound_call_is_logged(db, online_settings, monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"title": ["Remote Title"], "author": []}}

    def fake_get(url, **kwargs):
        calls.append(url)
        return FakeResponse()

    monkeypatch.setattr("app.services.metadata.enrich.httpx.get", fake_get)

    fetch_crossref(db, online_settings, "10.1/x", paper_id=7)
    db.flush()

    logged = db.query(EgressLog).all()
    assert len(logged) == 1
    assert logged[0].service == "crossref"
    assert logged[0].paper_id == 7
    assert logged[0].status_code == 200


def test_failed_calls_are_logged_too(db, online_settings, monkeypatch):
    """The audit trail records what left, not what came back."""

    def boom(url, **kwargs):
        raise OSError("network unreachable")

    monkeypatch.setattr("app.services.metadata.enrich.httpx.get", boom)

    assert fetch_crossref(db, online_settings, "10.1/x", paper_id=7) is None
    db.flush()
    assert db.query(EgressLog).count() == 1


def test_only_the_identifier_is_sent(db, online_settings, monkeypatch):
    """Titles, abstracts and filenames must never appear in an outbound URL."""
    seen = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {}}

    def fake_get(url, params=None, headers=None, **kwargs):
        seen["url"] = url
        seen["params"] = params
        return FakeResponse()

    monkeypatch.setattr("app.services.metadata.enrich.httpx.get", fake_get)
    fetch_crossref(db, online_settings, "10.1/x", paper_id=7)

    assert "10.1/x" in seen["url"]
    assert not seen["params"]


def test_remote_values_do_not_overwrite_locally_extracted_ones(
    db, paper_with_doi, online_settings, monkeypatch
):
    """A DOI read from the PDF outranks anything a remote service says."""
    import json

    paper_with_doi.meta.field_sources_json = json.dumps({"title": "pdf_embedded"})
    db.add(paper_with_doi.meta)
    db.flush()

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "message": {
                    "title": ["Remote Title"],
                    "author": [{"given": "A", "family": "B"}],
                    "container-title": ["Some Venue"],
                    "issued": {"date-parts": [[2021]]},
                }
            }

    monkeypatch.setattr(
        "app.services.metadata.enrich.httpx.get", lambda *a, **k: FakeResponse()
    )

    enrich_paper(db, paper_with_doi, online_settings)
    assert paper_with_doi.meta.title == "Local Title", "PDF-derived title was clobbered"
    # A field with no local value is fair game.
    assert paper_with_doi.meta.venue == "Some Venue"


def test_heuristic_values_may_be_replaced(
    db, paper_with_doi, online_settings, monkeypatch
):
    import json

    paper_with_doi.meta.field_sources_json = json.dumps({"title": "heuristic"})
    db.add(paper_with_doi.meta)
    db.flush()

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"title": ["Remote Title"], "author": []}}

    monkeypatch.setattr(
        "app.services.metadata.enrich.httpx.get", lambda *a, **k: FakeResponse()
    )

    enrich_paper(db, paper_with_doi, online_settings)
    assert paper_with_doi.meta.title == "Remote Title"
