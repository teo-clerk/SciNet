"""The event broker, and the collision that stopped the worker.

An ``.azw3`` that could not be opened raised ``UnreadableDocument``, which is
ordinary and expected. What was not ordinary is what the failure *handler* then
did: it called ``publish("job.failed", ..., kind=...)``, binding the publisher's
own first parameter twice. The TypeError escaped into the surrounding
transaction, rolled back the rows recording the failure, and killed the process
— so one unreadable book stopped an entire backlog.
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.events import BROKER, EventBroker


def test_a_payload_key_called_kind_does_not_collide():
    """The exact call that stopped the worker."""
    broker = EventBroker()

    broker.publish("job.failed", job_id=7, kind="parse", error="boom")


def test_the_event_name_and_a_kind_payload_stay_separate():
    broker = EventBroker()
    received: list = []

    async def drive():
        async with broker.subscribe() as queue:
            broker.publish("job.failed", job_id=7, kind="parse", error="boom")
            received.append(await asyncio.wait_for(queue.get(), timeout=1))

    asyncio.run(drive())

    event = received[0]
    assert event.kind == "job.failed", "the event's own name"
    assert event.payload["kind"] == "parse", "and the payload's, untouched"
    assert event.payload["job_id"] == 7


def test_every_reserved_name_is_safe_as_a_payload_key():
    """Positional-only means no parameter name can ever be shadowed."""
    broker = EventBroker()

    broker.publish("x", kind="a", payload="b", at="c", self="d")


def test_the_module_broker_takes_the_same_call():
    BROKER.publish("job.failed", job_id=1, job_kind="parse", error="e")


@pytest.mark.parametrize("name", ["parse.done", "job.failed", "paper.added"])
def test_publishing_without_subscribers_is_harmless(name):
    EventBroker().publish(name, paper_id=1)
