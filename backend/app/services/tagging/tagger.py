"""Summarising and tagging a paper with the local LLM.

Two things keep this from producing garbage:

**A closed vocabulary.** The model picks from a fixed list rather than
inventing labels, because free-form tagging fragments into near-duplicates
within a few dozen papers.

**A JSON schema.** Ollama constrains decoding to the schema, so the response
parses or the request fails — no regex salvage of half-formed JSON.

The model is also fed a *short* view of the paper. Ollama's default context is
small enough that a whole paper would be silently truncated, and a confident
summary of page one is worse than no summary.
"""

from __future__ import annotations

import json
import logging

import httpx

from app.core.config import Settings, get_settings
from app.core.model_store import PRIVATE_OLLAMA
from app.services.tagging.taxonomy import (
    MAX_PROPOSED_TAGS,
    MAX_TAGS_PER_PAPER,
)

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 300.0
MAX_INPUT_CHARS = 6000


class TaggingUnavailable(RuntimeError):
    """The tagging model is not reachable in the project's model store."""


def response_schema(vocabulary: list[str]) -> dict:
    """Constrain the reply: tags must come from the vocabulary, or be flagged."""
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "tags": {
                "type": "array",
                "items": {"type": "string", "enum": vocabulary},
                "maxItems": MAX_TAGS_PER_PAPER,
            },
            "proposed_tags": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": MAX_PROPOSED_TAGS,
            },
        },
        "required": ["summary", "tags"],
    }


def build_prompt(
    *, title: str | None, abstract: str | None, headings: list[str]
) -> str:
    parts = [
        "You are cataloguing a scientific paper for a personal library.",
        "",
        f"Title: {title or '(unknown)'}",
    ]
    if abstract:
        parts += ["", "Abstract:", abstract.strip()[:3000]]
    if headings:
        parts += ["", "Section headings: " + "; ".join(headings[:20])]
    parts += [
        "",
        "Write a two-sentence summary of what this paper does, in plain prose.",
        f"Then choose up to {MAX_TAGS_PER_PAPER} tags from the allowed list.",
        "Choose only tags the paper genuinely fits; fewer is better than wrong.",
        "If a central topic has no matching tag, put it in proposed_tags "
        "(lowercase, hyphenated). Otherwise leave proposed_tags empty.",
    ]
    return "\n".join(parts)[:MAX_INPUT_CHARS]


def available(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    try:
        PRIVATE_OLLAMA.start()
    except Exception:  # noqa: BLE001 - no binary, or it would not start
        return False
    return settings.llm_model in PRIVATE_OLLAMA.installed()


def unload(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    PRIVATE_OLLAMA.unload(settings.llm_model)


def tag_paper(
    *,
    title: str | None,
    abstract: str | None,
    headings: list[str],
    vocabulary: list[str],
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> dict:
    """Return ``{"summary": str, "tags": [...], "proposed_tags": [...]}``."""
    settings = settings or get_settings()
    if not available(settings):
        raise TaggingUnavailable(
            f"{settings.llm_model} is not in the project's model store; "
            "run scripts/download_models.py"
        )

    payload = {
        "model": settings.llm_model,
        "prompt": build_prompt(title=title, abstract=abstract, headings=headings),
        "stream": False,
        "format": response_schema(vocabulary),
        "keep_alive": settings.ollama_keep_alive,
        "options": {
            "temperature": 0.1,
            # Set explicitly: the default context would silently truncate the
            # prompt and produce a confident summary of whatever survived.
            "num_ctx": settings.llm_num_ctx,
        },
    }

    owned = client is None
    http = client or httpx.Client(timeout=REQUEST_TIMEOUT)
    try:
        response = http.post(f"{settings.ollama_url}/api/generate", json=payload)
        response.raise_for_status()
        raw = response.json().get("response", "")
    finally:
        if owned:
            http.close()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"tagging model returned unparseable JSON: {raw[:200]}"
        ) from exc

    return normalise(parsed, vocabulary)


def normalise(parsed: dict, vocabulary: list[str]) -> dict:
    """Defend against a schema-constrained model still going slightly off-piste."""
    allowed = set(vocabulary)

    tags: list[str] = []
    for tag in parsed.get("tags") or []:
        slug = str(tag).strip().lower()
        if slug in allowed and slug not in tags:
            tags.append(slug)

    proposed: list[str] = []
    for tag in parsed.get("proposed_tags") or []:
        slug = "-".join(str(tag).strip().lower().split())
        if slug and slug not in allowed and slug not in proposed:
            proposed.append(slug)

    return {
        "summary": str(parsed.get("summary") or "").strip(),
        "tags": tags[:MAX_TAGS_PER_PAPER],
        "proposed_tags": proposed[:MAX_PROPOSED_TAGS],
    }
