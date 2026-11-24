"""Who gets the agent, and who is asked "How do you want to work?".

- **Full:** Creator ("scale") and Team plans while paid, admins, and every self-hosted install.
- **Trial:** Pro, while paid: a few messages to try it (``AGENT_TRIAL_MESSAGES``, default 5), counted
  over the account's lifetime. Follow-ups and approvals don't count.
- **None:** free accounts, and Pro once the trial is used. They still see the agent and are offered Creator.

Checked on every message, not only when home loads, so a plan that lapses mid-chat stops new turns.
The ``creator_agent`` feature flag switches the whole thing off per platform from /admin.

The choice screen is shown once to everyone with full access, and to accounts created since the agent
launched (``AGENT_ONBOARDING_SINCE``). Existing Free and Pro accounts aren't interrupted; they find the
agent from the home screen and Settings.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import feature_flags
from ..services.billing_service import BillingService, grants_paid_access
from .loop import agent_configured

DEFAULT_DAILY_TURNS = 150
DEFAULT_TRIAL_MESSAGES = 5
DEFAULT_ONBOARDING_SINCE = "2026-11-24T00:00:00+00:00"
FULL_PLANS = {"scale", "team"}


def daily_turn_limit() -> int:
    try:
        return max(1, int(os.getenv("AGENT_DAILY_TURN_LIMIT", str(DEFAULT_DAILY_TURNS))))
    except ValueError:
        return DEFAULT_DAILY_TURNS


def trial_message_limit() -> int:
    try:
        return max(0, int(os.getenv("AGENT_TRIAL_MESSAGES", str(DEFAULT_TRIAL_MESSAGES))))
    except ValueError:
        return DEFAULT_TRIAL_MESSAGES


def onboarding_since() -> datetime:
    raw = os.getenv("AGENT_ONBOARDING_SINCE") or DEFAULT_ONBOARDING_SINCE
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.fromisoformat(DEFAULT_ONBOARDING_SINCE)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def trial_messages_used(db: AsyncSession, user_id: str) -> int:
    row = (
        await db.execute(
            text("SELECT COUNT(*) AS n FROM agent_runs WHERE user_id = :id AND trigger = 'user'"),
            {"id": user_id},
        )
    ).fetchone()
    return int(row.n if row else 0)


async def agent_access(
    db: AsyncSession, user_id: str, team_id: Optional[str], platform: feature_flags.Platform
) -> dict:
    """``{"available", "reason", "access", "trial", "plan"}``.

    ``access`` is ``full`` or ``trial`` when available. ``reason`` when not: ``disabled``,
    ``not_configured``, ``plan`` (Free, or a plan without the agent) or ``trial_used`` (Pro after
    its free messages). ``trial`` is ``{"limit", "used"}`` for Pro accounts.
    """
    if not await feature_flags.is_enabled(db, feature_flags.CREATOR_AGENT, platform=platform, user_id=user_id):
        return {"available": False, "reason": "disabled", "access": None, "trial": None, "plan": None}
    if not agent_configured():
        return {"available": False, "reason": "not_configured", "access": None, "trial": None, "plan": None}
    row = (await db.execute(text("SELECT is_admin FROM users WHERE id = :id"), {"id": user_id})).fetchone()
    summary = await BillingService(db).get_usage_summary(user_id, **({"team_id": team_id} if team_id else {}))
    plan = summary.get("plan")
    if row and row.is_admin:
        return {"available": True, "reason": None, "access": "full", "trial": None, "plan": plan}
    if not summary.get("monetization_enabled", True):
        return {"available": True, "reason": None, "access": "full", "trial": None, "plan": plan}
    # Creator ("scale") and Team, while paid: the same plans that may schedule posts.
    if summary.get("can_schedule_posts") or (plan in FULL_PLANS and not summary.get("upgrade_required")):
        return {"available": True, "reason": None, "access": "full", "trial": None, "plan": plan}
    paid_pro = (
        plan == "pro"
        and not team_id
        and grants_paid_access(plan, summary.get("subscription_status"), summary.get("subscription_provider"))
    )
    if paid_pro:
        limit = trial_message_limit()
        used = await trial_messages_used(db, user_id)
        trial = {"limit": limit, "used": min(used, limit)}
        if used < limit:
            return {"available": True, "reason": None, "access": "trial", "trial": trial, "plan": plan}
        return {"available": False, "reason": "trial_used", "access": None, "trial": trial, "plan": plan}
    return {"available": False, "reason": "plan", "access": None, "trial": None, "plan": plan}


async def offer_choice(db: AsyncSession, user_id: str, access: dict, mode: Optional[str]) -> bool:
    """Whether home asks "How do you want to work?" (only before a choice was saved)."""
    if mode is not None or access.get("reason") in {"disabled", "not_configured"}:
        return False
    if access.get("access") == "full":
        return True
    row = (await db.execute(text('SELECT "createdAt" AS created FROM users WHERE id = :id'), {"id": user_id})).fetchone()
    created = getattr(row, "created", None) if row else None
    if created is None:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return created >= onboarding_since()


async def home_mode(db: AsyncSession, user_id: str) -> Optional[str]:
    row = (await db.execute(text("SELECT home_mode FROM users WHERE id = :id"), {"id": user_id})).fetchone()
    value = getattr(row, "home_mode", None) if row else None
    return value if value in {"agent", "manual"} else None


async def first_name(db: AsyncSession, user_id: str) -> Optional[str]:
    row = (await db.execute(text("SELECT name FROM users WHERE id = :id"), {"id": user_id})).fetchone()
    name = (getattr(row, "name", None) or "").strip()
    return name.split()[0][:40] if name else None
