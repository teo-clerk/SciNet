"""The librarian's loop: decide before speaking.

Planning is constrained decoding — ``action`` is declared FIRST in the
schema, because Ollama fills fields in declaration order and a model asked
for its reasoning before its choice has not made one yet (the BRIDGE_SCHEMA
lesson, applied). The loop is bounded at three tool rounds: a librarian is
answering a reader, not conducting a survey.

The final answer streams as *prose*, not a schema — streaming and strict
JSON do not compose (you cannot stream readable text out of a constrained
object as it is generated). The citation gate therefore works on markers:
the prompt teaches ``[#id]``, and ``validate_citations`` strips any marker
naming a paper the tools never returned. Hallucinated citations are not
"discouraged"; they are structurally unrenderable.

Both the model call and the tool executor are injected, so the whole loop
tests with scripted fakes and no server anywhere.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.services.librarian.tools import ToolResult

MAX_ROUNDS = 3

#: action FIRST — the decision precedes every argument. See module docstring.
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": [
                "search_semantic",
                "search_fulltext",
                "similar",
                "read",
                "answer",
            ],
        },
        "query": {"type": "string"},
        "paper_id": {"type": "integer"},
        "reason": {"type": "string"},
    },
    "required": ["action"],
}

CITATION_RE = re.compile(r"\[#(\d+)\]")

AskJson = Callable[[str, dict], Awaitable[dict | None]]
Execute = Callable[[str, dict], Awaitable[ToolResult]]


@dataclass
class Gathered:
    results: list[ToolResult] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)

    @property
    def evidence_ids(self) -> set[int]:
        return {pid for result in self.results for pid in result.paper_ids}


def build_plan_prompt(
    question: str, gathered: Gathered, rounds_left: int, semantic_ok: bool
) -> str:
    lines = [
        "You are the librarian of a private scientific library. Decide the "
        "next step toward answering the reader's question.",
        "",
        f"Question: {question}",
        "",
        "Tools: search_semantic (find papers by meaning; needs query), "
        "search_fulltext (find exact words; needs query), similar (papers "
        "near one paper; needs paper_id), read (a window of one paper's "
        "text; needs paper_id), answer (stop searching and answer now).",
    ]
    if not semantic_ok:
        lines.append(
            "search_semantic is still warming up — use search_fulltext instead."
        )
    if gathered.results:
        lines.append("")
        lines.append("What the tools have found so far:")
        for result in gathered.results:
            lines.append(f"--- {result.tool} ---")
            lines.append(result.summary)
    lines.extend(
        [
            "",
            f"You may use at most {rounds_left} more tool call(s). Choose "
            "'answer' as soon as the evidence above suffices — or if the "
            "library clearly holds nothing relevant.",
        ]
    )
    return "\n".join(lines)


async def gather(
    question: str,
    *,
    ask_json: AskJson,
    execute: Execute,
    semantic_ok: bool = True,
    max_rounds: int = MAX_ROUNDS,
) -> Gathered:
    gathered = Gathered()
    for round_index in range(max_rounds):
        plan = await ask_json(
            build_plan_prompt(
                question, gathered, max_rounds - round_index, semantic_ok
            ),
            PLAN_SCHEMA,
        )
        action = (plan or {}).get("action")
        if action in (None, "", "answer"):
            break
        if action == "search_semantic" and not semantic_ok:
            action = "search_fulltext"
        args = {
            "query": (plan or {}).get("query", question),
            "paper_id": (plan or {}).get("paper_id"),
        }
        gathered.calls.append({"action": action, **args})
        result = await execute(action, args)
        gathered.results.append(result)
    return gathered


def build_answer_prompt(question: str, gathered: Gathered) -> str:
    evidence = (
        "\n\n".join(f"--- {r.tool} ---\n{r.summary}" for r in gathered.results)
        or "(the tools found nothing)"
    )
    return "\n".join(
        [
            "You are the librarian of a private scientific library. Answer "
            "the reader's question from the evidence below and nothing else.",
            "",
            f"Question: {question}",
            "",
            "Evidence:",
            evidence,
            "",
            "Rules: cite papers inline as [#id] using only ids that appear "
            "in the evidence. If the evidence does not answer the question, "
            "say plainly that the library does not seem to hold it — and "
            "cite nothing. Two short paragraphs at most.",
        ]
    )


#: A citation marker is at most [#123456] — ten characters. Held-back text
#: beyond this cannot be an unfinished marker, so it flushes.
MAX_MARKER_CHARS = 10


def safe_split(pending: str) -> tuple[str, str]:
    """Split streamed text into (safe to emit, hold for the next token).

    A marker split across two tokens — "see [#" then "42]" — must not reach
    the validator in halves: the regex would miss it and the fragment would
    leak to the reader. Anything after a '[' that has not closed yet is held
    back; a bracket that stays open past a marker's maximum length was never
    a marker and flushes.
    """
    cut = pending.rfind("[")
    if cut == -1:
        return pending, ""
    tail = pending[cut:]
    if "]" in tail or len(tail) > MAX_MARKER_CHARS:
        return pending, ""
    return pending[:cut], tail


def validate_citations(chunk: str, allowed: set[int]) -> tuple[str, set[int], set[int]]:
    """Pass through a piece of answer text, stripping unearned citations.

    Returns (clean_text, cited_ids, dropped_ids). Applied per streamed
    segment by the endpoint; the ``done`` frame totals both sets so a
    stripped citation is visible in the record, not silently vanished.
    """
    cited: set[int] = set()
    dropped: set[int] = set()

    def check(match: re.Match[str]) -> str:
        pid = int(match.group(1))
        if pid in allowed:
            cited.add(pid)
            return match.group(0)
        dropped.add(pid)
        return ""

    return CITATION_RE.sub(check, chunk), cited, dropped
