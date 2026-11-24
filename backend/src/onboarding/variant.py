"""Which onboarding a new account gets: ``pebble`` (guided by Agent) or ``classic``.

Accounts created since ``PEBBLE_ONBOARDING_SINCE`` are split by ``PEBBLE_ONBOARDING_PERCENT``
(default 50) the first time they load home. The group is stored on the user, so it never changes
for that account and every event can carry it. Older accounts get neither (``None``).

Self-hosted installs (monetization off) always get ``pebble``: there is nothing to compare there.
The ``pebble_onboarding`` feature flag turns the Pebble group off from /admin; accounts already
in it then see the classic onboarding.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import feature_flags
from ..analytics import capture_event
from ..config import get_config

PEBBLE = "pebble"
CLASSIC = "classic"
DEFAULT_SINCE = "2026-11-25T00:00:00+00:00"
DEFAULT_PERCENT = 50
FLAG = "pebble_onboarding"
# The PostHog experiment's flag key. Assignment is ours (users.onboarding_variant); PostHog learns each account's
# group from the exposure event below, which carries `$feature/<key>`.
EXPERIMENT_KEY = "pebble-onboarding"


def pebble_percent() -> int:
    try:
        return min(100, max(0, int(os.getenv("PEBBLE_ONBOARDING_PERCENT", str(DEFAULT_PERCENT)))))
    except ValueError:
        return DEFAULT_PERCENT


def onboarding_since() -> datetime:
    raw = os.getenv("PEBBLE_ONBOARDING_SINCE") or DEFAULT_SINCE
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.fromisoformat(DEFAULT_SINCE)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def bucket(user_id: str) -> int:
    """0-99, stable for an account (the split doesn't depend on when or where it loads)."""
    return int(hashlib.sha256(f"onboarding:{user_id}".encode()).hexdigest()[:8], 16) % 100


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def onboarding_variant(db: AsyncSession, user_id: str, platform: feature_flags.Platform) -> Optional[str]:
    """The account's onboarding group, assigned on first call. ``None`` for accounts made before it."""
    row = (
        await db.execute(
            text('SELECT onboarding_variant, "createdAt" AS created FROM users WHERE id = :id'),
            {"id": user_id},
        )
    ).fetchone()
    if row is None:
        return None
    enabled = await feature_flags.is_enabled(db, FLAG, platform=platform, user_id=user_id)
    stored = row.onboarding_variant
    if stored:
        return stored if enabled else CLASSIC
    created = _aware(row.created)
    if created is None or created < onboarding_since():
        return None
    if not enabled:
        return CLASSIC
    monetized = get_config().monetization_enabled
    variant = PEBBLE if (not monetized or bucket(user_id) < pebble_percent()) else CLASSIC
    assigned = await db.execute(
        text("UPDATE users SET onboarding_variant = :v WHERE id = :id AND onboarding_variant IS NULL"),
        {"v": variant, "id": user_id},
    )
    await db.commit()
    if assigned.rowcount:
        # Exactly once per account: the experiment's exposure. The variant also lands on the person, so any
        # insight can break down by it.
        capture_event(
            "onboarding_assigned",
            user_id,
            {"$feature/" + EXPERIMENT_KEY: variant, "variant": variant, "platform": platform},
            set_properties={"onboarding_variant": variant},
        )
    again = (
        await db.execute(text("SELECT onboarding_variant FROM users WHERE id = :id"), {"id": user_id})
    ).fetchone()
    return (again.onboarding_variant if again else None) or variant
