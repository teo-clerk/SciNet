"""The pause lease: the API asks, the worker yields at a job boundary.

API and worker are separate processes sharing only SQLite, so the lease is a
row in the settings table — ``worker.pause`` holding ``{until, nonce,
reason, keep_warm}``. The worker checks it between jobs and between kinds
(the seams where the card is naturally free), stops claiming while it is
live, and acks by echoing the *nonce* — an ack without the nonce would let a
stale acknowledgement from a previous turn satisfy a new wait, which was the
first hole poked in this design.

Expiry lives in the value, so no crash on either side can wedge the
pipeline: a dead API stops refreshing and the worker resumes on its own; a
dead worker never acks and the caller proceeds after its bounded wait
(``OLLAMA_NUM_PARALLEL=1`` serializes the worst case into slowness, never
corruption). ``release`` checks the nonce before ending the lease — the
holder may end its own lease, not whoever took one after it.

``keep_warm`` names the model the pauser is about to use. If that exact
reference is what the worker's Ollama slot holds, the eviction is skipped —
a librarian answering with the tagger's own model should inherit it warm,
not reload it.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.types import utcnow
from app.models.system import Setting

logger = logging.getLogger(__name__)

PAUSE_KEY = "worker.pause"
ACK_KEY = "worker.paused_ack"

#: A librarian turn's lease: long enough for a slow generation, short enough
#: that a crashed API costs the pipeline three minutes, not an evening.
DEFAULT_TTL_SECONDS = 180.0
#: The UI pause button: a human pressed it, a human will unpress it — but an
#: hour bounds the cost of a forgotten tab.
USER_TTL_SECONDS = 3600.0


@dataclass(frozen=True)
class Lease:
    until: datetime
    nonce: str
    reason: str
    keep_warm: str | None = None

    @property
    def expired(self) -> bool:
        return utcnow() >= self.until


def _write(session: Session, key: str, value: dict) -> None:
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=json.dumps(value)))
    else:
        row.value = json.dumps(value)
    session.flush()


def _read(session: Session, key: str) -> dict | None:
    row = session.get(Setting, key)
    if row is None or not row.value:
        return None
    try:
        return json.loads(row.value)
    except json.JSONDecodeError:
        return None  # a malformed lease is no lease; never wedge on garbage


def take(
    session: Session,
    *,
    ttl_seconds: float = DEFAULT_TTL_SECONDS,
    reason: str = "librarian",
    keep_warm: str | None = None,
    nonce: str | None = None,
) -> Lease:
    lease = Lease(
        until=utcnow() + timedelta(seconds=ttl_seconds),
        nonce=nonce or uuid.uuid4().hex,
        reason=reason,
        keep_warm=keep_warm,
    )
    _write(
        session,
        PAUSE_KEY,
        {
            "until": lease.until.isoformat(),
            "nonce": lease.nonce,
            "reason": lease.reason,
            "keep_warm": lease.keep_warm,
        },
    )
    return lease


def refresh(
    session: Session, lease: Lease, ttl_seconds: float = DEFAULT_TTL_SECONDS
) -> Lease:
    return take(
        session,
        ttl_seconds=ttl_seconds,
        reason=lease.reason,
        keep_warm=lease.keep_warm,
        nonce=lease.nonce,
    )


def current(session: Session) -> Lease | None:
    payload = _read(session, PAUSE_KEY)
    if payload is None:
        return None
    try:
        until = datetime.fromisoformat(payload["until"])
    except (KeyError, TypeError, ValueError):
        return None
    return Lease(
        until=until,
        nonce=str(payload.get("nonce", "")),
        reason=str(payload.get("reason", "")),
        keep_warm=payload.get("keep_warm"),
    )


def active(session: Session) -> Lease | None:
    lease = current(session)
    if lease is None or lease.expired:
        return None
    return lease


def release(session: Session, lease: Lease) -> None:
    """End the caller's own lease. A mismatched nonce means somebody took a
    newer lease meanwhile, and ending theirs is not this caller's to do."""
    held = current(session)
    if held is not None and held.nonce == lease.nonce:
        _write(session, PAUSE_KEY, {"until": utcnow().isoformat(), "nonce": ""})


def release_any(session: Session) -> None:
    """The resume button: end whatever lease exists, expressly."""
    _write(session, PAUSE_KEY, {"until": utcnow().isoformat(), "nonce": ""})


def ack(session: Session, nonce: str) -> None:
    _write(session, ACK_KEY, {"nonce": nonce, "at": utcnow().isoformat()})


def acked(session: Session, lease: Lease) -> bool:
    payload = _read(session, ACK_KEY)
    return bool(payload) and payload.get("nonce") == lease.nonce
