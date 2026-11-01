"""Lenient accessors for service JSON and the dates in it."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any


def as_mapping(value: Any) -> Mapping[str, Any]:
    """``value`` if it is a JSON object, else an empty one."""
    return value if isinstance(value, Mapping) else {}


def attributes(obj: Mapping[str, Any]) -> Mapping[str, Any]:
    """A resource's ``attributes`` object (JSON:API, Apple), else an empty one."""
    return as_mapping(obj.get("attributes"))


def iso_datetime(raw: Any, *, assume_utc: bool = False) -> datetime | None:
    """An ISO 8601 timestamp, or None when missing or malformed. With
    ``assume_utc``, one without an offset is taken as UTC."""
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if assume_utc and parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def expires_after(raw: str | None, lifetime: timedelta) -> datetime | None:
    """``lifetime`` after the ISO timestamp ``raw``; None when there is none."""
    start = iso_datetime(raw)
    return start + lifetime if start is not None else None
