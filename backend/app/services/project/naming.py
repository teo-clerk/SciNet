"""Naming clusters with the local LLM.

A map of unlabelled blobs is a puzzle, not a tool. HDBSCAN says *which* works
belong together but nothing about what they have in common, so each cluster is
described to the tagging model and asked for a short region name.

The model is given evidence rather than asked to guess: the most distinctive
words across the cluster's titles, plus a sample of the titles themselves. It
is told to name only what it can see, because an invented label on a map is
worse than no label — the user cannot tell it is wrong.

The prompts never say "paper" or "scientific". A library may hold essays,
chapters, lectures and primary sources beside preprints, and a model told it
is looking at science reaches for a subfield name ("Longitudinal Cohort
Analysis") where a reader from outside the field needed the idea ("Memory and
Identity"). Every prompt asks for the version a curious newcomer would follow,
and the specialist's layer — the distinctive words themselves — is kept
alongside rather than replaced.
"""

from __future__ import annotations

import json
import logging

import httpx

from app.core.config import Settings, get_settings
from app.core.model_store import PRIVATE_OLLAMA
from app.services.llm_text import trim_to_sentence

__all__ = [
    "BRIDGE_SCHEMA",
    "NAME_SCHEMA",
    "OVERVIEW_SCHEMA",
    "available",
    "build_prompt",
    "clean_name",
    "describe_bridge",
    "describe_cluster",
    "name_cluster",
    "sample_titles",
    "trim_to_sentence",
]

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


def sample_titles(titles: list[str], limit: int = SAMPLE_TITLES) -> list[str]:
    """Take titles spread evenly across the cluster, not the first few.

    Papers are numbered in registration order, which in a library organised on
    disk means alphabetical — so the first N members of a cluster are very
    likely to share a filename prefix and therefore a topic. Naming an
    88-paper region from its first twelve titles produced
    "Astrophysics and Cosmology" for a cluster that was two thirds climate and
    earth science: the model described exactly what it was shown, and what it
    was shown was one corner.
    """
    usable = [t for t in titles if t and t.strip()]
    if len(usable) <= limit:
        return usable
    step = len(usable) / limit
    return [usable[int(i * step)] for i in range(limit)]


def build_prompt(terms: list[str], titles: list[str]) -> str:
    return "\n".join(
        [
            "These works were grouped together automatically because their "
            "content is similar. They may be papers, essays, chapters or "
            "reports from any field. Name the theme they share.",
            "",
            "Most distinctive words: " + (", ".join(terms[:12]) or "(none)"),
            "",
            "Sample titles:",
            *[f"- {t}" for t in sample_titles(titles)],
            "",
            f"Reply with a name of at most {MAX_NAME_WORDS} words, in title "
            "case, that a curious newcomer would understand and a specialist "
            'would still find accurate ("Stoic Ethics", "Radar Imaging of the '
            'Earth", "Theories of Consciousness"). Prefer the idea over the '
            "method.",
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
    model: str | None = None,
) -> str | None:
    """Return a short label for a cluster, or None if it cannot be named."""
    settings = settings or get_settings()
    if not terms and not titles:
        return None

    payload = {
        "model": model or settings.llm_model,
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

    name = clean_name(parsed.get("name"))
    if name and parsed.get("confident") is False:
        # The model was asked to flag a region it could not characterise. Taking
        # it at its word beats printing a confident label over a mixed cluster.
        logger.info("cluster name %r reported as low confidence", name)
    return name


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


OVERVIEW_SCHEMA = {
    "type": "object",
    "properties": {"overview": {"type": "string"}},
    "required": ["overview"],
}

# Property order is load-bearing. Constrained decoding fills the fields in the
# order they are declared, so the verdict must come first: asked for the prose
# first, the model has not decided anything yet and emits filler — every pair
# on the real corpus came back with the literal string "connected".
BRIDGE_SCHEMA = {
    "type": "object",
    "properties": {
        # Asked for explicitly rather than inferred from the prose. On a real
        # corpus the model answered "loosely related" for most pairs — which
        # was correct — and detecting that by string matching would be brittle.
        "connected": {"type": "boolean"},
        "relationship": {"type": "string"},
    },
    "required": ["connected", "relationship"],
}

# A summary shorter than this is not a sentence; it is the model restating the
# field name or the verdict.
MIN_BRIDGE_WORDS = 6

MAX_OVERVIEW_CHARS = 420
#: Three plain sentences that tell a story need more room than the two
#: technical ones this used to hold; 300 cut the third sentence off mid-way.
MAX_BRIDGE_CHARS = 420


def _ask_json(
    prompt: str, schema: dict, settings, client, model: str | None = None
) -> dict | None:
    """One constrained call, returning the whole object."""
    payload = {
        "model": model or settings.llm_model,
        "prompt": prompt,
        "stream": False,
        "format": schema,
        "keep_alive": settings.ollama_keep_alive,
        "options": {"temperature": 0.25, "num_ctx": settings.llm_num_ctx},
    }
    owned = client is None
    http = client or httpx.Client(timeout=REQUEST_TIMEOUT)
    try:
        response = http.post(f"{settings.ollama_url}/api/generate", json=payload)
        response.raise_for_status()
        return json.loads(response.json().get("response", "{}"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("llm call failed: %s", exc)
        return None
    finally:
        if owned:
            http.close()


def _ask(
    prompt: str, schema: dict, key: str, settings, client, model: str | None = None
) -> str | None:
    """One constrained call. Returns None rather than raising: an unnamed
    region is a missing label, not a failed projection."""
    payload = {
        "model": model or settings.llm_model,
        "prompt": prompt,
        "stream": False,
        "format": schema,
        "keep_alive": settings.ollama_keep_alive,
        "options": {"temperature": 0.25, "num_ctx": settings.llm_num_ctx},
    }
    owned = client is None
    http = client or httpx.Client(timeout=REQUEST_TIMEOUT)
    try:
        response = http.post(f"{settings.ollama_url}/api/generate", json=payload)
        response.raise_for_status()
        value = json.loads(response.json().get("response", "{}")).get(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("llm call failed: %s", exc)
        return None
    finally:
        if owned:
            http.close()
    return " ".join(str(value).split()) if value else None


def describe_cluster(
    name: str,
    terms: list[str],
    titles: list[str],
    *,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
    model: str | None = None,
) -> str | None:
    """A few sentences on what a region contains and what holds it together.

    The label names the region; this explains it. Given the same evidence the
    label was drawn from, and told to describe rather than speculate — a
    confident description of papers the model cannot see is worse than none,
    because the reader has no way to check it.
    """
    settings = settings or get_settings()
    if not titles:
        return None

    prompt = "\n".join(
        [
            f'These works were grouped together and the group is called "{name}".',
            "",
            "Most distinctive words: " + (", ".join(terms[:12]) or "(none)"),
            "",
            "Works in the group:",
            *[f"- {t}" for t in sample_titles(titles, limit=14)],
            "",
            "In two or three sentences, tell a curious newcomer what this "
            "group of works is about: what question they share, and what "
            "holds them together. Write it as you would explain it to a "
            "friend from another field, without jargon; if a technical term "
            "is unavoidable, say in a few words what it means. Describe only "
            "what these titles show; do not speculate about work that is not "
            "listed, and do not restate the group's name.",
        ]
    )
    overview = _ask(prompt, OVERVIEW_SCHEMA, "overview", settings, client, model)
    return trim_to_sentence(overview, MAX_OVERVIEW_CHARS) if overview else None


def describe_bridge(
    left_name: str,
    right_name: str,
    shared: list[str],
    bridge_titles: list[str],
    *,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
    model: str | None = None,
) -> str | None:
    """How two regions relate, grounded in the papers that span them.

    The papers are the evidence and are given to the model explicitly, because
    the interesting failure here is a fluent, plausible connection between two
    fields that this particular library does not actually contain.
    """
    settings = settings or get_settings()
    if not bridge_titles:
        return None

    prompt = "\n".join(
        [
            f'Two groups of works in a library are called "{left_name}" and '
            f'"{right_name}".',
            "",
            "These works sit between the two groups:",
            *[f"- {t}" for t in bridge_titles[:8] if t],
            "",
            "Words both groups use: " + (", ".join(shared[:10]) or "(none)"),
            "",
            "First, set connected to true only if the listed works show a "
            "real, specific path of ideas from one group to the other. "
            "Sharing a broad field, or a word like 'review', is not a path. "
            "If in doubt, set it to false.",
            "Then write relationship: two or three full sentences a newcomer "
            "could follow, telling the story of how someone starting in "
            f'"{left_name}" would arrive at "{right_name}" — name the idea, '
            "question or method that carries them across, say what one side "
            "supplies and what the other does with it, and refer to the works "
            "above. Plain language; if a technical term is unavoidable, say "
            "in a few words what it means. Do not answer with a single word.",
        ]
    )
    answer = _ask_json(prompt, BRIDGE_SCHEMA, settings, client, model)
    if not answer or not answer.get("connected"):
        # The model was asked directly and said no. Drawing the line anyway
        # would assert a relationship it just declined to find.
        return None
    summary = " ".join(str(answer.get("relationship") or "").split())
    if len(summary.split()) < MIN_BRIDGE_WORDS:
        # A one-word answer is not an explanation, and an unexplained line on
        # the map is an assertion the reader cannot check.
        logger.warning(
            "discarding degenerate bridge summary %r for %s <-> %s",
            summary,
            left_name,
            right_name,
        )
        return None
    return trim_to_sentence(summary, MAX_BRIDGE_CHARS)
