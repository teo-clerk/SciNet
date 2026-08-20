"""Datetime storage invariants.

SQLite stores datetimes as text. If some columns are written timezone-aware and
others naive, they round-trip to different strings — which breaks Python
comparison (aware vs naive raises TypeError) and SQL ordering (a trailing
"+00:00" changes the lexicographic result). These tests pin the one format.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.core.types import SQLITE_DATETIME_FORMAT, to_storage, utcnow
from app.models import JobKind
from app.workers.queue import claim_next, enqueue, requeue_stale


def test_utcnow_is_timezone_aware():
    assert utcnow().tzinfo is not None


def test_to_storage_emits_naive_utc(tmp_path):
    aware = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone(timedelta(hours=5)))
    rendered = to_storage(aware)
    assert "+" not in rendered, "offset must not reach storage"
    # 12:00 at +05:00 is 07:00 UTC.
    assert rendered.startswith("2026-03-01 07:00:00")
    datetime.strptime(rendered, SQLITE_DATETIME_FORMAT)  # parses back


def test_all_datetime_columns_share_one_storage_format(sf, engine):
    """created_at (ORM default) and started_at (raw SQL) must agree."""
    with sf() as s:
        enqueue(s, JobKind.PARSE, paper_id=1)
        s.commit()
    with sf() as s:
        claim_next(s)
        s.commit()

    raw = sqlite3.connect(engine.url.database)
    created, started = raw.execute("SELECT created_at, started_at FROM jobs").fetchone()
    raw.close()

    for column, value in (("created_at", created), ("started_at", started)):
        assert "+" not in value, f"{column} leaked a UTC offset into storage"
        datetime.strptime(value, SQLITE_DATETIME_FORMAT)


def test_values_read_back_timezone_aware(sf):
    with sf() as s:
        enqueue(s, JobKind.PARSE, paper_id=1)
        s.commit()
    with sf() as s:
        job = claim_next(s)
        assert job.created_at.tzinfo is not None
        assert job.started_at.tzinfo is not None


def test_columns_are_mutually_comparable(sf):
    """The bug this guards: naive minus aware raises TypeError."""
    with sf() as s:
        enqueue(s, JobKind.PARSE, paper_id=1)
        s.commit()
    with sf() as s:
        job = claim_next(s)
        assert job.started_at >= job.created_at
        assert (utcnow() - job.started_at) < timedelta(seconds=60)


def test_sql_comparison_against_a_cutoff_is_correct(sf):
    """requeue_stale relies on SQL comparing stored text to a bound datetime."""
    with sf() as s:
        enqueue(s, JobKind.PARSE, paper_id=1)
        s.commit()
    with sf() as s:
        claim_next(s)
        s.commit()

    with sf() as s:
        assert requeue_stale(s, older_than_seconds=3600) == 0, "too new to be stale"
    with sf() as s:
        assert requeue_stale(s, older_than_seconds=0) == 1, "cutoff in the past"
        s.commit()


@pytest.mark.parametrize(
    "tz", [UTC, timezone(timedelta(hours=-8)), timezone(timedelta(hours=5, minutes=30))]
)
def test_storage_normalises_any_offset_to_utc(tz):
    instant = datetime(2026, 6, 15, 18, 30, 0, tzinfo=UTC)
    assert to_storage(instant) == to_storage(instant.astimezone(tz))
