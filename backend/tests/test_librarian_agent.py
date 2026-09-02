"""The plan loop is bounded, and citations cannot outrun the evidence.

Everything here runs on scripted fakes — the loop's shape is what is under
test: it stops when the model says answer, it stops at the round ceiling
however eager the model is, a warming semantic search degrades to fulltext
instead of failing, and the citation gate strips exactly the ids the tools
never returned.
"""

from __future__ import annotations

from app.services.librarian.agent import (
    MAX_ROUNDS,
    PLAN_SCHEMA,
    build_answer_prompt,
    gather,
    validate_citations,
)
from app.services.librarian.tools import ToolResult


def scripted(plans: list[dict]):
    queue = list(plans)

    async def ask_json(prompt: str, schema: dict) -> dict | None:
        assert schema is PLAN_SCHEMA
        return queue.pop(0) if queue else {"action": "answer"}

    return ask_json


async def echo_execute(action: str, args: dict) -> ToolResult:
    return ToolResult(action, f"ran {action} with {args.get('query')}", (7, 8))


# --- the loop ----------------------------------------------------------------


async def test_the_loop_stops_when_the_model_answers() -> None:
    gathered = await gather(
        "what about kalman filters?",
        ask_json=scripted(
            [{"action": "search_fulltext", "query": "kalman"}, {"action": "answer"}]
        ),
        execute=echo_execute,
    )
    assert [c["action"] for c in gathered.calls] == ["search_fulltext"]
    assert gathered.evidence_ids == {7, 8}


async def test_the_round_ceiling_holds_however_eager_the_model() -> None:
    always_searching = scripted(
        [{"action": "search_fulltext", "query": f"q{i}"} for i in range(10)]
    )
    gathered = await gather("q", ask_json=always_searching, execute=echo_execute)
    assert len(gathered.calls) == MAX_ROUNDS


async def test_a_warming_semantic_search_degrades_to_fulltext() -> None:
    gathered = await gather(
        "q",
        ask_json=scripted(
            [{"action": "search_semantic", "query": "x"}, {"action": "answer"}]
        ),
        execute=echo_execute,
        semantic_ok=False,
    )
    assert gathered.calls[0]["action"] == "search_fulltext"


async def test_a_broken_plan_ends_the_loop_rather_than_crashing() -> None:
    async def confused(prompt: str, schema: dict) -> dict | None:
        return None

    gathered = await gather("q", ask_json=confused, execute=echo_execute)
    assert gathered.calls == [] and gathered.results == []


# --- the gate ----------------------------------------------------------------


def test_unearned_citations_are_stripped_and_recorded() -> None:
    clean, cited, dropped = validate_citations(
        "Kalman filtering [#7] is covered; so is fusion [#99].", allowed={7, 8}
    )
    assert "[#7]" in clean and "[#99]" not in clean
    assert cited == {7} and dropped == {99}


def test_citations_split_across_calls_still_pass_whole_markers() -> None:
    # The endpoint validates per streamed segment; a marker only counts once
    # it is complete, so segments are buffered until markers close. Here we
    # pin the pure function: a complete marker in a later segment validates.
    clean, cited, _ = validate_citations("see [#8].", allowed={8})
    assert clean == "see [#8]." and cited == {8}


def test_the_answer_prompt_carries_the_evidence_and_the_rules() -> None:
    from app.services.librarian.agent import Gathered

    gathered = Gathered(
        results=[ToolResult("search_fulltext", "[#3] A Title: snippet", (3,))]
    )
    prompt = build_answer_prompt("what?", gathered)
    assert "[#3] A Title" in prompt
    assert "cite nothing" in prompt
