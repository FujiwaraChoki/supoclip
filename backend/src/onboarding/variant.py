"""Whether an account gets Agent's guided onboarding (``pebble``) or the standard home (``None``).

Every account created since the guided onboarding launched (``GUIDED_SINCE``) gets it; older accounts
don't. The first time such an account loads home it is stored on the user (``users.onboarding_variant``)
and ``onboarding_assigned`` is sent once. The ``pebble_onboarding`` feature flag turns the guided
onboarding off from /admin, per platform.

It started as an A/B test against the earlier onboarding (``classic``), which ended on 2026-11-25.
The few accounts still stored as ``classic`` get the standard home, like accounts made before it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import feature_flags
from ..analytics import capture_event

PEBBLE = "pebble"
FLAG = "pebble_onboarding"
# When the guided onboarding launched. Accounts created before it already found their way around.
GUIDED_SINCE = datetime(2026, 11, 24, 18, 43, 2, tzinfo=timezone.utc)


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def onboarding_variant(db: AsyncSession, user_id: str, platform: feature_flags.Platform) -> Optional[str]:
    """``pebble`` when the account gets the guided onboarding, else ``None``."""
    row = (
        await db.execute(
            text('SELECT onboarding_variant, "createdAt" AS created FROM users WHERE id = :id'),
            {"id": user_id},
        )
    ).fetchone()
    if row is None or not await feature_flags.is_enabled(db, FLAG, platform=platform, user_id=user_id):
        return None
    if row.onboarding_variant:
        return PEBBLE if row.onboarding_variant == PEBBLE else None
    created = _aware(row.created)
    if created is None or created < GUIDED_SINCE:
        return None
    assigned = await db.execute(
        text("UPDATE users SET onboarding_variant = :v WHERE id = :id AND onboarding_variant IS NULL"),
        {"v": PEBBLE, "id": user_id},
    )
    await db.commit()
    if assigned.rowcount:
        # Once per account, from the app it was first opened in. `client`, not `platform`: the PostHog
        # client's `platform` super property (the service name) overwrites an event's own `platform`.
        capture_event("onboarding_assigned", user_id, {"client": platform})
    return PEBBLE
