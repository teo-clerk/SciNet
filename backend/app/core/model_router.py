"""Which model answers which task.

``Role`` in the registry is too coarse for routing: ``TAG`` covers paper
tagging (300 s budget, temperature 0.1, an enum-constrained schema), cluster
naming (0.2), and bridge verdicts (0.25) — three prompt shapes that may well
deserve three models on a card big enough to hold them. ``TaskKind`` is the
finer axis, and resolution is deliberately boring:

    per-task pin (a ``route.<task>`` row in the settings table)
    else the legacy settings field for the task's family.

With no pins the behaviour is byte-identical to reading ``settings.llm_model``
directly, which is what makes the migration of twenty call sites safe to do
three at a time. Recommendations from the profile catalog are *advisory* —
shown in the Model Lab, applied by pinning — never a silent reroute: the user
chose "recommend", and a pipeline that swaps models on its own judgment is
the autopilot they explicitly declined.

``session=None`` skips the pin lookup rather than opening a session of its
own — a router that reached for the real database by default would leak it
into every test that forgot to say otherwise.

v1 scope, stated plainly: pins are applied for the LLM tasks (tagging,
naming, overviews, bridges, the librarian, adjudication). ``PAGE_OCR`` and
the ``EMBED_*`` tasks resolve to their settings fields only — embed pins in
particular are "switch the whole store" operations (vectors and projections
are keyed by model), which deserve an explicit confirmation path, not a row
write.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.system import Setting


class TaskKind(StrEnum):
    TAG_PAPER = "tag_paper"
    NAME_CLUSTER = "name_cluster"
    DESCRIBE_CLUSTER = "describe_cluster"
    BRIDGE_VERDICT = "bridge_verdict"
    PAGE_OCR = "page_ocr"
    EMBED_DOCS = "embed_docs"
    EMBED_QUERY = "embed_query"
    LIBRARIAN_CHAT = "librarian_chat"
    EXTRACT_ADJUDICATE = "extract_adjudicate"
    INSIGHT = "insight"


PIN_PREFIX = "route."

#: The settings field that answers when nothing is pinned — full back-compat.
_LEGACY_FIELD: dict[TaskKind, str] = {
    TaskKind.TAG_PAPER: "llm_model",
    TaskKind.NAME_CLUSTER: "llm_model",
    TaskKind.DESCRIBE_CLUSTER: "llm_model",
    TaskKind.BRIDGE_VERDICT: "llm_model",
    TaskKind.LIBRARIAN_CHAT: "llm_model",
    TaskKind.EXTRACT_ADJUDICATE: "llm_model",
    TaskKind.INSIGHT: "llm_model",
    TaskKind.PAGE_OCR: "vlm_model",
    TaskKind.EMBED_DOCS: "embed_model",
    TaskKind.EMBED_QUERY: "embed_model",
}

#: The tasks a pin actually reroutes in v1. Everything else is listed so the
#: Model Lab can grey it out instead of offering a knob that does nothing.
PINNABLE: tuple[TaskKind, ...] = (
    TaskKind.TAG_PAPER,
    TaskKind.NAME_CLUSTER,
    TaskKind.DESCRIBE_CLUSTER,
    TaskKind.BRIDGE_VERDICT,
    TaskKind.LIBRARIAN_CHAT,
    TaskKind.EXTRACT_ADJUDICATE,
    TaskKind.INSIGHT,
)


def pin_key(task: TaskKind) -> str:
    return f"{PIN_PREFIX}{task.value}"


def resolve(
    task: TaskKind,
    settings: Settings | None = None,
    session: Session | None = None,
) -> str:
    """The model reference this task should use, pin first."""
    settings = settings or get_settings()
    if session is not None and task in PINNABLE:
        row = session.get(Setting, pin_key(task))
        if row is not None and row.value:
            return row.value
    return getattr(settings, _LEGACY_FIELD[task])


def set_pin(session: Session, task: TaskKind, reference: str) -> None:
    if task not in PINNABLE:
        raise ValueError(f"{task.value} is not pin-routable in v1")
    row = session.get(Setting, pin_key(task))
    if row is None:
        session.add(Setting(key=pin_key(task), value=reference))
    else:
        row.value = reference
    session.flush()


def clear_pin(session: Session, task: TaskKind) -> None:
    row = session.get(Setting, pin_key(task))
    if row is not None:
        session.delete(row)
        session.flush()


def pins(session: Session) -> dict[str, str]:
    """Every active pin, task value -> model reference."""
    rows = session.query(Setting).filter(Setting.key.like(f"{PIN_PREFIX}%")).all()
    return {row.key.removeprefix(PIN_PREFIX): row.value for row in rows}
