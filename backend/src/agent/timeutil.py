"""Times the agent reads and writes, always in the creator's own timezone.

The model never handles UTC offsets: it is told the local date and time, says
"2026-11-24T09:00", and code turns that into an instant in the person's zone.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def zone(name: Optional[str]) -> ZoneInfo:
    try:
        return ZoneInfo((name or "UTC").strip() or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def parse_local(value: str, tz: ZoneInfo) -> datetime:
    """``2026-11-24T09:00`` (local) or a full ISO timestamp -> aware UTC datetime."""
    text = (value or "").strip()
    if not text:
        raise ValueError("A time is required, like 2026-11-24T09:00.")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    text = text.replace(" ", "T", 1) if "T" not in text and " " in text else text
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(f"'{value}' isn't a time I can read. Use the form 2026-11-24T09:00.") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(timezone.utc)


def parse_any(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def local_text(value, tz: ZoneInfo) -> Optional[str]:
    """``Tue 2026-11-24 09:00`` in the person's zone, for the model to read."""
    parsed = parse_any(value)
    if parsed is None:
        return None
    return parsed.astimezone(tz).strftime("%a %Y-%m-%d %H:%M")


def now_text(tz: ZoneInfo) -> str:
    now = datetime.now(timezone.utc).astimezone(tz)
    return now.strftime("%A %Y-%m-%d %H:%M")
