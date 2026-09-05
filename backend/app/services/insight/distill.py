"""The plain-English reading of one work, from the local LLM.

An abstract answers a specialist's question — what was done, with what, and
how well. A reader from another field has three different questions: what is
this asking, what does it claim, and why should anyone care. One constrained
call answers them, and in the same pass names the claims the text makes and
the people, works and ideas it turns on, so a philosophical essay with no
measured values still has something to show in the inspector.

Three things keep this honest:

**Genre first.** Whether "the central question" is a hypothesis, a thesis or a
narrative arc depends on what kind of work this is, so the schema makes the
model decide that before it writes a word. Property order is decision order
under constrained decoding (the ``BRIDGE_SCHEMA`` lesson), and ``grounded``
is last for the mirror-image reason: it is a verdict on prose that has to
exist before it can be assessed.

**Only what the material supports.** The model is given the title, the
abstract or synopsis, the opening of the text and the section headings, and
told to say so when that is too thin — a confident core idea for a work the
model has not really seen is worse than none, because the reader has no way
to check it.

**No science assumed.** The prompt names essays, chapters, poems and novels
before it names papers. Told it is reading science, a model summarises Mill
as if he reported an experiment.
"""

from __future__ import annotations

import json
import logging

import httpx

from app.core.config import Settings, get_settings
from app.core.model_store import PRIVATE_OLLAMA
from app.services.llm_text import collapse, trim_to_sentence

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 300.0
MAX_INPUT_CHARS = 6000
MAX_ABSTRACT_CHARS = 2500
MAX_EXCERPT_CHARS = 1800
MAX_HEADINGS = 20
MAX_AUTHORS = 4
#: Two sentences of plain prose. Longer answers start explaining the field.
MAX_ANSWER_CHARS = 320
MAX_CLAIMS = 4
MAX_ENTITIES = 8
#: A "claim" of three words is a keyword, not a statement the text makes.
MIN_CLAIM_WORDS = 4
TEMPERATURE = 0.15

GENRES: tuple[str, ...] = (
    "empirical-study",
    "theoretical-argument",
    "survey-or-review",
    "essay-or-commentary",
    "historical-narrative",
    "technical-report",
    "literary-or-fiction",
    "reference-or-textbook",
    "other",
)
ENTITY_KINDS: tuple[str, ...] = (
    "person",
    "work",
    "concept",
    "event",
    "place",
    "organisation",
)

# Property order is load-bearing: Ollama fills the fields in the order they are
# declared. ``genre`` is the one decision that governs the prose, so it comes
# first; ``grounded`` assesses the prose, so it comes last.
INSIGHT_SCHEMA = {
    "type": "object",
    "properties": {
        "genre": {"type": "string", "enum": list(GENRES)},
        "question": {"type": "string"},
        "argument": {"type": "string"},
        "significance": {"type": "string"},
        "claims": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": MAX_CLAIMS,
        },
        "entities": {
            "type": "array",
            "maxItems": MAX_ENTITIES,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "kind": {"type": "string", "enum": list(ENTITY_KINDS)},
                },
                "required": ["name", "kind"],
            },
        },
        "grounded": {"type": "boolean"},
    },
    "required": [
        "genre",
        "question",
        "argument",
        "significance",
        "claims",
        "entities",
        "grounded",
    ],
}


#: Fragments of the instructions themselves. On the first live run a third of
#: the readings came back with the instruction copied into the field — "What is
#: the central question or problem the work takes up?" as the question — while
#: the claims beneath were real. An answer that quotes the instruction has not
#: said anything about the work, whatever the model's own verdict.
ECHO_FRAGMENTS: tuple[str, ...] = (
    "central question or problem",
    "this particular work takes up",
    "the work takes up",
    "core argument, finding",
    "argues, finds or discovers",
    "what changes if the reader believes",
    "copy these instructions",
    # The prompt's own example, which a model that echoes will echo too.
    "flood damage be mapped",
)


def is_echo(answer: str) -> bool:
    lowered = answer.lower()
    return any(fragment in lowered for fragment in ECHO_FRAGMENTS)


class InsightUnavailable(RuntimeError):
    """The model is not reachable in the project's model store.

    A property of the moment, not of the document: the job keeps its retries
    and ``revive_jobs.py`` knows how to bring it back.
    """


def build_prompt(
    *,
    title: str | None,
    authors: list[str] | None,
    year: int | None,
    abstract: str | None,
    headings: list[str],
    excerpt: str | None,
) -> str:
    byline = ", ".join((authors or [])[:MAX_AUTHORS]) or "(unknown)"
    parts = [
        "You are helping a curious reader understand one work in a personal "
        "library. The work may be a scientific paper, a philosophical essay, "
        "a chapter of a history book, a technical report, a poem, a lecture, "
        "or a novel. Do not assume it is science.",
        "",
        f"Title: {title or '(unknown)'}",
        f"Author(s): {byline}",
        f"Year: {year or '(unknown)'}",
        "",
        "Synopsis or abstract:",
        (abstract or "").strip()[:MAX_ABSTRACT_CHARS] or "(none)",
        "",
        "Opening of the text:",
        (excerpt or "").strip()[:MAX_EXCERPT_CHARS] or "(none)",
        "",
        "Section headings: " + ("; ".join(headings[:MAX_HEADINGS]) or "(none)"),
        "",
        "First decide genre: what kind of work this is.",
        "Then write three short answers about THIS work in plain English, one "
        "or two sentences each, for someone with no training in the field. "
        "Avoid jargon; if a technical term is unavoidable, say what it means "
        "in the same sentence.",
        "  - question: the problem or question this particular work takes up, "
        "in your own words (for example: how can flood damage be mapped from "
        "space within a day?).",
        "  - argument: what this work argues, finds or discovers.",
        "  - significance: why that matters — what changes if the reader believes it.",
        "Do not copy these instructions into the answers; every answer must "
        "name something from the work itself.",
        f"Then list up to {MAX_CLAIMS} claims: specific statements the text "
        "itself makes, one sentence each, in your own words, as claims (not "
        "questions).",
        f"Then list up to {MAX_ENTITIES} entities the text names and depends "
        "on: people, works, concepts, events, places, organisations. Use the "
        "text's own names.",
        "Only describe what the material above supports. Do not invent "
        "findings, numbers, names or conclusions that are not there. If the "
        "material is too thin to answer a question, answer with what can be "
        "said and set grounded to false.",
    ]
    return "\n".join(parts)[:MAX_INPUT_CHARS]


def available(settings: Settings | None = None, reference: str | None = None) -> bool:
    """Is the model — the routed one, or the default — actually installed?"""
    settings = settings or get_settings()
    try:
        PRIVATE_OLLAMA.start()
    except Exception:  # noqa: BLE001 - no binary, or it would not start
        return False
    return (reference or settings.llm_model) in PRIVATE_OLLAMA.installed()


def distill_paper(
    *,
    title: str | None,
    authors: list[str] | None,
    year: int | None,
    abstract: str | None,
    headings: list[str],
    excerpt: str | None,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
    model: str | None = None,
) -> dict:
    """One constrained call; returns the normalised reading.

    ``model`` is the router's resolved reference; None keeps the settings
    default. ``client`` is injectable so tests never touch a socket.
    """
    settings = settings or get_settings()
    reference = model or settings.llm_model
    if not available(settings, reference):
        raise InsightUnavailable(
            f"{reference} is not in the project's model store; "
            "run scripts/download_models.py"
        )

    payload = {
        "model": reference,
        "prompt": build_prompt(
            title=title,
            authors=authors,
            year=year,
            abstract=abstract,
            headings=headings,
            excerpt=excerpt,
        ),
        "stream": False,
        "format": INSIGHT_SCHEMA,
        "keep_alive": settings.ollama_keep_alive,
        "options": {
            "temperature": TEMPERATURE,
            # Set explicitly: the default context would silently truncate the
            # prompt and produce a confident reading of whatever survived.
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
            f"insight model returned unparseable JSON: {raw[:200]}"
        ) from exc
    if not isinstance(parsed, dict):
        raise ValueError("insight model returned something other than an object")
    return normalise(parsed)


def normalise(parsed: dict) -> dict:
    """Defend against a schema-constrained model still going slightly off-piste.

    Answers are trimmed to whole sentences and blanked when they merely echo
    the instructions; claims that are keywords rather than statements are
    dropped; entities are deduplicated by name and kinds outside the list are
    dropped rather than guessed at. ``grounded`` is forced to False when any
    of the three answers is empty: a reading with a hole in it is by
    definition not one the material supported.
    """
    genre = collapse(parsed.get("genre"))
    if genre not in GENRES:
        genre = "other"

    answers = {}
    for key in ("question", "argument", "significance"):
        answer = trim_to_sentence(collapse(parsed.get(key)), MAX_ANSWER_CHARS)
        # An echo of the instruction is blanked rather than shown: a hole in
        # the reading is visible, a question about "the work" in general is
        # not, and blanking it is what turns ``grounded`` off below.
        answers[key] = "" if is_echo(answer) else answer

    raw_claims = parsed.get("claims")
    raw_entities = parsed.get("entities")

    claims: list[str] = []
    for raw in raw_claims if isinstance(raw_claims, list) else []:
        claim = trim_to_sentence(collapse(raw), MAX_ANSWER_CHARS)
        if len(claim.split()) < MIN_CLAIM_WORDS or claim in claims:
            continue
        claims.append(claim)
        if len(claims) >= MAX_CLAIMS:
            break

    entities: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in raw_entities if isinstance(raw_entities, list) else []:
        if not isinstance(raw, dict):
            continue
        name = collapse(raw.get("name"))
        kind = collapse(raw.get("kind")).lower()
        if not name or kind not in ENTITY_KINDS or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        entities.append({"name": name, "kind": kind})
        if len(entities) >= MAX_ENTITIES:
            break

    grounded = bool(parsed.get("grounded", True)) and all(answers.values())

    return {
        "genre": genre,
        **answers,
        "claims": claims,
        "entities": entities,
        "grounded": grounded,
    }
