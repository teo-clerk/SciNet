"""Optional online metadata cleanup.

Off by default, and the only code in SciNet that opens a socket to anything
other than a local model server. Two rules make that claim checkable rather
than aspirational:

* Every request goes through ``_get``, which refuses to run when enrichment is
  disabled and writes an ``egress_log`` row for every call it does make.
* Only an identifier is ever sent — a DOI or arXiv ID. Titles, abstracts and
  file names stay on the machine.

Even so, this leaks *which papers you read* to the services queried, which is
why it is opt-in rather than a default.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import EgressLog, MetaSource, Paper

logger = logging.getLogger(__name__)

CROSSREF_URL = "https://api.crossref.org/works/{doi}"
OPENALEX_URL = "https://api.openalex.org/works/doi:{doi}"
ARXIV_URL = "http://export.arxiv.org/api/query"
TIMEOUT_SECONDS = 20


class EnrichmentDisabled(RuntimeError):
    """Raised if enrichment code is reached with the switch off."""


def _get(
    session: Session,
    settings: Settings,
    service: str,
    url: str,
    *,
    paper_id: int | None,
    params: dict[str, Any] | None = None,
) -> httpx.Response | None:
    """The single choke point for outbound traffic."""
    if not settings.enrichment_enabled:
        raise EnrichmentDisabled("enrichment is off; no request may leave the machine")

    headers = {"User-Agent": _user_agent(settings)}
    status: int | None = None
    try:
        response = httpx.get(
            url, params=params, headers=headers, timeout=TIMEOUT_SECONDS
        )
        status = response.status_code
        response.raise_for_status()
        return response
    except Exception as exc:  # noqa: BLE001 - the library works fine offline
        logger.warning("%s lookup failed: %s", service, exc)
        return None
    finally:
        # Logged whether or not it succeeded: the audit trail is about what left
        # the machine, not about what came back.
        session.add(
            EgressLog(
                service=service, url=str(url), paper_id=paper_id, status_code=status
            )
        )


def _user_agent(settings: Settings) -> str:
    """Crossref asks for contact details and gives politer rate limits for them."""
    base = "SciNet/0.1 (local research tool)"
    return (
        f"{base} mailto:{settings.enrichment_email}"
        if settings.enrichment_email
        else base
    )


def fetch_crossref(
    session: Session, settings: Settings, doi: str, paper_id: int | None = None
) -> dict[str, Any] | None:
    response = _get(
        session,
        settings,
        "crossref",
        CROSSREF_URL.format(doi=doi),
        paper_id=paper_id,
    )
    if response is None:
        return None

    message = response.json().get("message", {})
    authors = [
        " ".join(filter(None, [a.get("given"), a.get("family")])).strip()
        for a in message.get("author", [])
    ]
    issued = message.get("issued", {}).get("date-parts", [[None]])[0]

    return {
        "title": (message.get("title") or [None])[0],
        "authors": [a for a in authors if a],
        "venue": (message.get("container-title") or [None])[0],
        "year": issued[0] if issued else None,
        "abstract": message.get("abstract"),
    }


def enrich_paper(session: Session, paper: Paper, settings: Settings) -> bool:
    """Fill gaps from Crossref. Locally-derived values are never overwritten.

    Provenance decides: a field extracted by regex from the PDF itself is more
    trustworthy than a remote record, so enrichment only fills blanks and
    fields whose recorded source was a heuristic.
    """
    # Defence in depth: this is the friendly check that lets callers invoke
    # enrichment unconditionally, while ``_get`` keeps the hard guard that
    # cannot be bypassed by any code path.
    if not settings.enrichment_enabled:
        return False

    meta = paper.meta
    if meta is None or not meta.doi:
        return False

    record = fetch_crossref(session, settings, meta.doi, paper.id)
    if not record:
        return False

    sources = json.loads(meta.field_sources_json or "{}")
    overwritable = {str(MetaSource.HEURISTIC), str(MetaSource.LLM)}
    changed = False

    def take(field: str, value: Any) -> None:
        nonlocal changed
        if not value:
            return
        current = getattr(meta, field, None)
        if current and sources.get(field) not in overwritable:
            return
        setattr(meta, field, value)
        sources[field] = str(MetaSource.CROSSREF)
        changed = True

    take("title", record["title"])
    take("venue", record["venue"])
    take("year", record["year"])
    if record["authors"] and (
        not meta.authors_json
        or meta.authors_json == "[]"
        or sources.get("authors") in overwritable
    ):
        meta.authors_json = json.dumps(record["authors"])
        sources["authors"] = str(MetaSource.CROSSREF)
        changed = True

    if changed:
        meta.field_sources_json = json.dumps(sources)
        session.add(meta)
    return changed
