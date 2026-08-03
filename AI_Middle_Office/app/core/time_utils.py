"""Shared datetime helpers for the middle-office backend.

The current database still contains a mix of UTC-aware values and historical
Asia/Shanghai naive values. These helpers make that boundary explicit while the
schema is migrated in smaller, safer steps.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


APP_TIMEZONE_NAME = "Asia/Shanghai"
APP_TZ = ZoneInfo(APP_TIMEZONE_NAME)
UTC = timezone.utc


def utc_now() -> datetime:
    """Return an aware UTC timestamp for new audit/runtime records."""

    return datetime.now(UTC)


def app_now() -> datetime:
    """Return the current application display time as an aware timestamp."""

    return datetime.now(APP_TZ)


def as_utc(value: datetime | None, *, naive_tz: ZoneInfo = APP_TZ) -> datetime | None:
    """Convert a datetime to aware UTC, treating naive values as app-local."""

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=naive_tz).astimezone(UTC)
    return value.astimezone(UTC)


def as_app_time(value: datetime | None, *, naive_tz: ZoneInfo = APP_TZ) -> datetime | None:
    """Convert a datetime to aware application time."""

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=naive_tz)
    return value.astimezone(APP_TZ)


def app_local_naive(value: datetime | None = None) -> datetime:
    """Return app-local time without tzinfo for legacy naive DB columns."""

    source = value or app_now()
    if source.tzinfo is None:
        return source
    return source.astimezone(APP_TZ).replace(tzinfo=None)


def parse_iso_datetime(value: str) -> datetime:
    """Parse an ISO datetime string, accepting a trailing Z for UTC."""

    return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
