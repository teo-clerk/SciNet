"""Server-sent events: live pipeline progress for the UI."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.core.events import BROKER

router = APIRouter(prefix="/api", tags=["events"])

HEARTBEAT_SECONDS = 15.0


@router.get("/events")
async def stream_events(request: Request) -> StreamingResponse:
    async def generator() -> AsyncIterator[str]:
        async with BROKER.subscribe() as queue:
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=HEARTBEAT_SECONDS
                    )
                except TimeoutError:
                    # Comment frame: keeps proxies and browsers from timing the
                    # connection out during a quiet stretch of the pipeline.
                    yield ": heartbeat\n\n"
                    continue
                yield event.to_sse()

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
