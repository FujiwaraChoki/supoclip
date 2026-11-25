"""The free preview clip: one per account, from the person's own video, before they pay.

A preview is a normal project marked with ``tasks.preview``. It is analysed in full (so the
person sees how many moments were found), only the best clip is rendered, and the rest are listed
as locked moments. Nothing is charged. After they subscribe, ``POST /tasks/{id}/unlock`` charges
the video's minutes and renders the rest in place, reusing the transcript and analysis.

Who may start one (``preview_eligibility``): the ``pebble_onboarding`` flag is on, the install
is monetized, the account has no paid plan, isn't in a team workspace, has confirmed its email,
hasn't finished a preview, has none running, and today's previews across everyone are under
``PREVIEW_DAILY_LIMIT`` (a cost brake). The video must be at most ``PREVIEW_MAX_MINUTES`` long.
A preview is used up when its clip is saved; one that fails before that doesn't use it up.

Until unlocked, a preview's clip can be watched, downloaded and posted, but not edited or
re-rendered (``preview_locked``).
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import feature_flags
from ..config import get_config
from ..services.billing_service import BillingService, grants_paid_access
from .variant import FLAG

DEFAULT_MAX_MINUTES = 90
DEFAULT_DAILY_LIMIT = 300
ACTIVE_STATUSES = ("queued", "processing")

# Plain-language refusals for POST /tasks/ with "preview": true.
PREVIEW_REFUSALS = {
    "disabled": "Free previews aren't available right now.",
    "not_needed": "Your plan already makes every clip. Create a project instead.",
    "team": "Free previews are for personal accounts.",
    "unverified": "Confirm your email address first, then try again.",
    "used": "You've already made your free preview. Pick a plan to make more clips.",
    "running": "Your free preview is already being made.",
    "busy": "Free previews are paused for today. Pick a plan to make clips now.",
}


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def preview_max_minutes() -> int:
    return _int_env("PREVIEW_MAX_MINUTES", DEFAULT_MAX_MINUTES) or DEFAULT_MAX_MINUTES


def preview_daily_limit() -> int:
    return _int_env("PREVIEW_DAILY_LIMIT", DEFAULT_DAILY_LIMIT)


def preview_state(task: Optional[dict]) -> Optional[dict[str, Any]]:
    """A task's ``preview`` as a dict (rows may carry JSONB as text), or None."""
    value = (task or {}).get("preview")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


def is_preview(task: Optional[dict]) -> bool:
    return preview_state(task) is not None


def is_locked_preview(task: Optional[dict]) -> bool:
    state = preview_state(task)
    return state is not None and not state.get("unlocked_at")


async def latest_preview_task(db: AsyncSession, user_id: str) -> Optional[dict[str, Any]]:
    row = (
        await db.execute(
            text(
                """
                SELECT id, status, preview FROM tasks
                WHERE user_id = :id AND preview IS NOT NULL AND team_id IS NULL
                ORDER BY created_at DESC LIMIT 1
                """
            ),
            {"id": user_id},
        )
    ).fetchone()
    return {"id": row.id, "status": row.status, "preview": row.preview} if row else None


async def preview_eligibility(
    db: AsyncSession,
    user_id: str,
    team_id: Optional[str],
    platform: feature_flags.Platform,
    *,
    summary: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """``{"eligible", "reason", "max_minutes"}``. ``reason`` when not: ``disabled``, ``not_needed``
    (self-hosted or already paid), ``team``, ``unverified``, ``used``, ``running``, ``busy``."""
    result: dict[str, Any] = {"eligible": False, "reason": None, "max_minutes": preview_max_minutes()}
    if not get_config().monetization_enabled:
        return {**result, "reason": "not_needed"}
    if team_id:
        return {**result, "reason": "team"}
    if not await feature_flags.is_enabled(db, FLAG, platform=platform, user_id=user_id):
        return {**result, "reason": "disabled"}
    if summary is None:
        summary = await BillingService(db).get_usage_summary(user_id)
    if grants_paid_access(summary.get("plan"), summary.get("subscription_status"), summary.get("subscription_provider")):
        return {**result, "reason": "not_needed"}
    row = (
        await db.execute(
            text('SELECT "emailVerified" AS verified, preview_used_at FROM users WHERE id = :id'),
            {"id": user_id},
        )
    ).fetchone()
    if row is None or not row.verified:
        return {**result, "reason": "unverified"}
    if row.preview_used_at is not None:
        return {**result, "reason": "used"}
    running = (
        await db.execute(
            text(
                "SELECT 1 FROM tasks WHERE user_id = :id AND preview IS NOT NULL AND status IN ('queued', 'processing') LIMIT 1"
            ),
            {"id": user_id},
        )
    ).fetchone()
    if running is not None:
        return {**result, "reason": "running"}
    limit = preview_daily_limit()
    if limit:
        today = (
            await db.execute(
                text("SELECT COUNT(*) AS n FROM tasks WHERE preview IS NOT NULL AND created_at >= NOW() - INTERVAL '24 hours'")
            )
        ).fetchone()
        if today is not None and int(today.n) >= limit:
            return {**result, "reason": "busy"}
    return {**result, "eligible": True}


async def mark_preview_used(db: AsyncSession, user_id: str) -> None:
    await db.execute(
        text("UPDATE users SET preview_used_at = COALESCE(preview_used_at, NOW()) WHERE id = :id"),
        {"id": user_id},
    )


def pick_preview(segments: list[dict[str, Any]]) -> int:
    """Index of the clip a preview renders: the highest virality score (first on a tie)."""
    best, best_score = 0, float("-inf")
    for index, segment in enumerate(segments):
        try:
            score = float(segment.get("virality_score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        if score > best_score:
            best, best_score = index, score
    return best


def _seconds(value: Any) -> float:
    """Seconds from a number or an ``MM:SS`` / ``HH:MM:SS`` label (segments use both)."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            parts = [float(part) for part in value.strip().split(":")]
        except ValueError:
            return 0.0
        total = 0.0
        for part in parts:
            total = total * 60 + part
        return total
    return 0.0


def locked_moments(segments: list[dict[str, Any]], rendered: int) -> list[dict[str, Any]]:
    """What a preview shows about the clips it didn't render: hook, score and length only."""
    moments = []
    for index, segment in enumerate(segments):
        if index == rendered:
            continue
        start = _seconds(segment.get("start_time", segment.get("start")))
        end = _seconds(segment.get("end_time", segment.get("end")))
        moments.append(
            {
                "position": index + 1,
                "hook_title": (segment.get("hook_title") or "")[:120] or None,
                "virality_score": segment.get("virality_score"),
                "duration": round(max(0.0, end - start), 1) or None,
                "start": round(start, 1),
            }
        )
    return moments
