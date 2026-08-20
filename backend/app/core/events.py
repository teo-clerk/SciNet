"""In-process pub/sub feeding the UI's progress stream.

The worker publishes; the API's SSE endpoint subscribes. Deliberately
fire-and-forget: a slow or absent browser must never apply backpressure to the
ingestion pipeline, so a full subscriber queue drops its oldest event rather
than blocking the publisher.

Cross-process delivery is handled by the worker writing to SQLite and the API
polling job counts. This broker only carries the low-latency chatter.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from app.core.types import utcnow

logger = logging.getLogger(__name__)

MAX_QUEUE = 256


@dataclass(frozen=True)
class Event:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    at: datetime = field(default_factory=utcnow)

    def to_sse(self) -> str:
        body = asdict(self)
        body["at"] = self.at.isoformat()
        return f"event: {self.kind}\ndata: {json.dumps(body['payload'])}\n\n"


class EventBroker:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()

    def publish(self, kind: str, **payload: Any) -> None:
        event = Event(kind=kind, payload=payload)
        for queue in list(self._subscribers):
            if queue.full():
                # Drop the oldest rather than block: progress events are
                # disposable, and the pipeline must not wait on a browser.
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[Event]]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=MAX_QUEUE)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


BROKER = EventBroker()
