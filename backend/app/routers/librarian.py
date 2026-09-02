"""Ask the library a question; the answer streams, and the map listens.

One POST, one SSE stream (the events.py framing, hand-rolled — a second
convention for one endpoint would be worse than none). Frames: ``status``
(what the librarian is doing, including what the GPU is doing), ``tool_call``
(each retrieval as it happens), ``map_directive`` (fly, highlight — derived
from the evidence, so the map can only show what was actually retrieved),
``answer_token`` (validated prose), ``done`` (cited and dropped ids).

The GPU choreography: take the pause lease with ``keep_warm`` naming the
resolved model, wait boundedly for the worker's nonce-echoing ack (5 s when
nothing is mid-job, 15 s when something is — a tier-2 page can hold the
worker for minutes and the reader deserves to know rather than wait), then
proceed regardless: ``OLLAMA_NUM_PARALLEL=1`` serializes the worst case
into slowness, never corruption. The lease refreshes from the token loop
and releases in ``finally``; a crash falls back to expiry.

Single-flight by a plain bool on the event loop: a second concurrent ask is
a 409, because two generations behind one serialized Ollama double both
latencies and interleave two sets of map directives.
"""

from __future__ import annotations

import asyncio
import json
import logging

import anyio
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.core.model_router import TaskKind, resolve
from app.models import Job, JobState
from app.services.librarian import agent, tools
from app.workers import lease

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/librarian", tags=["librarian"])

#: Single-flight. A bool, not a Lock: everything here runs on one event
#: loop, and "try to acquire, 409 otherwise" is not a Lock's native move.
_busy = False

ACK_POLL_SECONDS = 0.5
#: Nothing mid-job means the worker acks within its 2 s idle poll — or is
#: not running at all, which five seconds also answers.
ACK_WAIT_IDLE_SECONDS = 5.0
ACK_WAIT_BUSY_SECONDS = 15.0
LEASE_REFRESH_EVERY_TOKENS = 40
GENERATE_TIMEOUT = httpx.Timeout(180.0, connect=10.0)
ANSWER_TEMPERATURE = 0.4


class AskRequest(BaseModel):
    question: str


def _frame(kind: str, payload: dict) -> str:
    return f"event: {kind}\ndata: {json.dumps(payload)}\n\n"


async def _ollama_json(
    model: str, prompt: str, schema: dict, settings: Settings
) -> dict | None:
    """One constrained, non-streamed planning call. None on any failure —
    a librarian that cannot plan answers from what it has, not with a 500."""
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": schema,
        "keep_alive": settings.ollama_keep_alive,
        "options": {"temperature": 0.2, "num_ctx": settings.llm_num_ctx},
    }
    try:
        async with httpx.AsyncClient(timeout=GENERATE_TIMEOUT) as client:
            response = await client.post(
                f"{settings.ollama_url}/api/generate", json=payload
            )
            response.raise_for_status()
            return json.loads(response.json().get("response", "{}"))
    except Exception as exc:  # noqa: BLE001 - planning failure ends the loop
        logger.warning("librarian planning call failed: %s", exc)
        return None


async def _stream_answer_tokens(model: str, prompt: str, settings: Settings):
    """Raw answer tokens from Ollama's NDJSON stream — the first streaming
    generate in the codebase; every earlier call is stream: False."""
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "keep_alive": settings.ollama_keep_alive,
        "options": {"temperature": ANSWER_TEMPERATURE, "num_ctx": settings.llm_num_ctx},
    }
    async with httpx.AsyncClient(timeout=GENERATE_TIMEOUT) as client:
        async with client.stream(
            "POST", f"{settings.ollama_url}/api/generate", json=payload
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                try:
                    piece = json.loads(line)
                except json.JSONDecodeError:
                    continue
                token = piece.get("response", "")
                if token:
                    yield token
                if piece.get("done"):
                    return


def _running_jobs(db: Session) -> int:
    return len(db.scalars(select(Job).where(Job.state == JobState.RUNNING)).all())


@router.post("/ask")
async def ask(
    body: AskRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    global _busy
    if _busy:
        raise HTTPException(409, "the librarian is already answering")
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "ask a question")
    _busy = True

    events: asyncio.Queue[tuple[str, dict] | None] = asyncio.Queue()

    async def put(kind: str, payload: dict) -> None:
        await events.put((kind, payload))

    async def execute(action: str, args: dict) -> tools.ToolResult:
        await put(
            "tool_call",
            {
                "action": action,
                "query": args.get("query"),
                "paper_id": args.get("paper_id"),
            },
        )

        def call() -> tools.ToolResult:
            if action == "search_fulltext":
                return tools.search_fulltext(db, args.get("query") or question)
            if action == "search_semantic":
                return tools.search_semantic(
                    db, settings, args.get("query") or question
                )
            if action == "similar":
                return tools.similar(db, settings, int(args.get("paper_id") or 0))
            if action == "read":
                return tools.read_window(db, int(args.get("paper_id") or 0))
            return tools.ToolResult(action, f"unknown tool {action!r}")

        result = await anyio.to_thread.run_sync(call)
        await put(
            "status",
            {"text": f"{action}: {len(result.paper_ids)} paper(s)"},
        )
        return result

    async def run() -> None:
        held: lease.Lease | None = None
        try:
            model = resolve(TaskKind.LIBRARIAN_CHAT, settings, db)
            try:
                held = lease.take(db, keep_warm=model, reason="librarian")
                db.commit()
            except OperationalError:
                # The worker holds SQLite's write lock through a long
                # transaction — a projection can hold it for minutes — so
                # even WRITING the pause request can starve. The lease is
                # best-effort by design: proceed without it, and Ollama's
                # single-parallel setting turns the contention into
                # slowness, never corruption.
                db.rollback()
                held = None
                await put(
                    "status",
                    {
                        "text": "the worker is mid-write; answering without "
                        "a pause — this may be slow"
                    },
                )

            if held is not None:
                busy_worker = _running_jobs(db) > 0
                deadline = asyncio.get_event_loop().time() + (
                    ACK_WAIT_BUSY_SECONDS if busy_worker else ACK_WAIT_IDLE_SECONDS
                )
                if busy_worker:
                    await put(
                        "status",
                        {"text": "waiting for the worker to finish its current job…"},
                    )
                while asyncio.get_event_loop().time() < deadline:
                    db.expire_all()  # the lease rows change under another process
                    if lease.acked(db, held):
                        break
                    await asyncio.sleep(ACK_POLL_SECONDS)
                else:
                    await put(
                        "status",
                        {"text": "worker still busy; answers may be slow for a moment"},
                    )

            await put("status", {"text": f"thinking with {model}…"})
            gathered = await agent.gather(
                question,
                ask_json=lambda p, s: _ollama_json(model, p, s, settings),
                execute=execute,
                semantic_ok=tools.semantic_ready(),
            )

            evidence = sorted(gathered.evidence_ids)
            if evidence:
                await put(
                    "map_directive", {"type": "highlight", "paper_ids": evidence[:12]}
                )
                await put("map_directive", {"type": "fly_to", "paper_id": evidence[0]})
                await put("map_directive", {"type": "trail", "paper_ids": evidence[:6]})

            cited_total: set[int] = set()
            dropped_total: set[int] = set()
            pending = ""
            token_count = 0
            async for token in _stream_answer_tokens(
                model, agent.build_answer_prompt(question, gathered), settings
            ):
                pending += token
                emit, pending = agent.safe_split(pending)
                if emit:
                    clean, cited, dropped = agent.validate_citations(
                        emit, gathered.evidence_ids
                    )
                    cited_total |= cited
                    dropped_total |= dropped
                    if clean:
                        await put("answer_token", {"text": clean})
                token_count += 1
                if held is not None and token_count % LEASE_REFRESH_EVERY_TOKENS == 0:
                    try:
                        held = lease.refresh(db, held)
                        db.commit()
                    except OperationalError:
                        db.rollback()  # expiry covers a refresh that starves
            if pending:
                clean, cited, dropped = agent.validate_citations(
                    pending, gathered.evidence_ids
                )
                cited_total |= cited
                dropped_total |= dropped
                if clean:
                    await put("answer_token", {"text": clean})

            await put(
                "done",
                {
                    "cited": sorted(cited_total),
                    "dropped": sorted(dropped_total),
                    "evidence": evidence,
                },
            )
        except Exception as exc:  # noqa: BLE001 - the stream is the error channel
            logger.exception("librarian turn failed")
            await put("status", {"text": f"the librarian could not answer: {exc}"})
            await put("done", {"cited": [], "dropped": [], "error": str(exc)})
        finally:
            if held is not None:
                try:
                    lease.release(db, held)
                    db.commit()
                except Exception:  # noqa: BLE001 - expiry is the backstop
                    logger.debug("lease release failed; expiry will cover it")
            await events.put(None)

    task = asyncio.create_task(run())

    async def stream():
        global _busy
        try:
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    task.cancel()
                    break
                try:
                    item = await asyncio.wait_for(events.get(), timeout=15.0)
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                if item is None:
                    break
                kind, payload = item
                yield _frame(kind, payload)
        finally:
            _busy = False

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
