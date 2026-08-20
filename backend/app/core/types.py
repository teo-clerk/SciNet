"""Custom SQLAlchemy column types.

SQLite has no native datetime type — values are stored as text. SQLAlchemy's
built-in ``DateTime`` formats the fields of whatever datetime it is given and
silently drops any UTC offset, so a timezone-aware value and a naive one round
-trip to *different strings*. Mixing the two in one table breaks both Python
comparison (aware vs naive raises TypeError) and SQL comparison (the trailing
"+00:00" changes the lexicographic ordering that SQLite relies on).

``UtcDateTime`` removes the ambiguity: everything is stored as a naive UTC
string in one fixed format, and everything read back is timezone-aware UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Dialect
from sqlalchemy.types import TypeDecorator

# Matches SQLAlchemy's own SQLite DATETIME storage format, so existing rows and
# hand-written SQL agree.
SQLITE_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S.%f"


def utcnow() -> datetime:
    """Timezone-aware current time. The one clock the application uses."""
    return datetime.now(UTC)


def to_storage(value: datetime) -> str:
    """Render a datetime the way the database stores it.

    Needed by hand-written SQL, which bypasses the type decorator.
    """
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value.strftime(SQLITE_DATETIME_FORMAT)


class UtcDateTime(TypeDecorator[datetime]):
    """Stores naive UTC, returns aware UTC."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError(f"expected datetime, got {type(value).__name__}")
        if value.tzinfo is None:
            # Naive input is taken as UTC rather than rejected: it is what
            # SQLite hands back on a legacy row.
            return value
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, str):
            value = datetime.strptime(value, SQLITE_DATETIME_FORMAT)
        return value.replace(tzinfo=UTC)
