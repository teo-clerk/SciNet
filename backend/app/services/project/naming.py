"""Naming clusters with the local LLM.

A map of unlabelled blobs is a puzzle, not a tool. HDBSCAN says *which* papers
belong together but nothing about what they have in common, so each cluster is
described to the tagging model and asked for a short region name.

The model is given evidence rather than asked to guess: the most distinctive
words across the cluster's titles, plus a sample of the titles themselves. It
is told to name only what it can see, because an invented label on a map is
worse than no label — the user cannot tell it is wrong.
"""

from __future__ import annotations

import json
import logging

import httpx

from app.core.config import Settings, get_settings
from app.core.model_store import PRIVATE_OLLAMA

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 180.0
SAMPLE_TITLES = 12
MAX_NAME_WORDS = 5

NAME_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "confident": {"type": "boolean"},
    },
    "required": ["name", "confident"],
}


def build_prompt(terms: list[str], titles: list[str]) -> str:
    return "\n".join(
        [
            "These scientific papers were grouped together by an automatic "
            "clustering of their content. Name the topic they share.",
            "",
            "Most distinctive words: " + (", ".join(terms[:12]) or "(none)"),
            "",
            "Sample titles:",
            *[f"- {t}" for t in titles[:SAMPLE_TITLES] if t],
            "",
            f"Reply with a topic name of at most {MAX_NAME_WORDS} words, in "
            "title case, that a researcher would recognise as a field or "
            "subfield.",
            "Do not invent specificity the titles do not support: if they have "
            "little in common, give a broad name and set confident to false.",
        ]
    )


def name_cluster(
    terms: list[str],
    titles: list[str],
    *,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> str | None:
    """Return a short label for a cluster, or None if it cannot be named."""
    settings = settings or get_settings()
    if not terms and not titles:
        return None

    payload = {
        "model": settings.llm_model,
        "prompt": build_prompt(terms, titles),
        "stream": False,
        "format": NAME_SCHEMA,
        "keep_alive": settings.ollama_keep_alive,
        "options": {"temperature": 0.2, "num_ctx": settings.llm_num_ctx},
    }

    owned = client is None
    http = client or httpx.Client(timeout=REQUEST_TIMEOUT)
    try:
        response = http.post(f"{settings.ollama_url}/api/generate", json=payload)
        response.raise_for_status()
        parsed = json.loads(response.json().get("response", "{}"))
    except Exception as exc:  # noqa: BLE001 - an unnamed cluster is not a failure
        logger.warning("could not name cluster: %s", exc)
        return None
    finally:
        if owned:
            http.close()

    return clean_name(parsed.get("name"))


def clean_name(raw: object) -> str | None:
    """Trim the model's answer to something that fits on a map."""
    if not isinstance(raw, str):
        return None
    name = " ".join(raw.strip().strip("\"'.").split())
    if not name:
        return None

    words = name.split()
    if len(words) > MAX_NAME_WORDS:
        name = " ".join(words[:MAX_NAME_WORDS])
    # A label longer than this is unreadable floating over a point cloud.
    return name[:60]


def available(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    try:
        PRIVATE_OLLAMA.start()
    except Exception:  # noqa: BLE001
        return False
    return settings.llm_model in PRIVATE_OLLAMA.installed()
