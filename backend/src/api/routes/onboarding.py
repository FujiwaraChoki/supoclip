"""Onboarding for new accounts (docs/onboarding.md): whether they get Agent's guided onboarding, what
they make, a look at their video before they commit, and when the guided onboarding ended."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...admin_auth import user_is_admin
from ...ai import max_final_clips_for_duration
from ...auth_headers import AuthContext, resolve_auth_context
from ...config import get_config
from ...database import get_db
from ...feature_flags import client_platform
from ...onboarding.preview import latest_preview_task, preview_eligibility
from ...onboarding.variant import PEBBLE, onboarding_variant
from ...services.video_service import VideoService
from ...youtube_utils import canonical_youtube_url
from .tasks import _preflight_stream_source, _preflight_youtube_source

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/onboarding", tags=["onboarding"])

MAKES = {"podcasts", "streams", "youtube", "talks", "other"}
LINK_CHECKS_PER_HOUR = 30


async def _ctx(request: Request, db: AsyncSession) -> AuthContext:
    # API keys too: the iOS app against a self-hosted server signs in with one.
    return await resolve_auth_context(request, db, get_config())


async def _body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(422, "Send a JSON object") from None
    if not isinstance(body, dict):
        raise HTTPException(422, "Send a JSON object")
    return body


@router.get("/status")
async def status(request: Request, db: AsyncSession = Depends(get_db), variant: Optional[str] = None):
    """Whether the account gets the guided onboarding (``variant``: ``pebble`` or null), its answers,
    and whether a free preview can start.

    Admins may pass ``?variant=pebble`` to see the guided onboarding (nothing is stored).
    """
    ctx = await _ctx(request, db)
    platform = client_platform(request)
    group = await onboarding_variant(db, ctx.user_id, platform)
    if variant == PEBBLE and await user_is_admin(db, ctx.user_id):
        group = variant
    row = (
        await db.execute(
            text("SELECT onboarding_answers, onboarding_completed_at FROM users WHERE id = :id"),
            {"id": ctx.user_id},
        )
    ).fetchone()
    eligibility = await preview_eligibility(db, ctx.user_id, ctx.team_id, platform)
    task = await latest_preview_task(db, ctx.user_id)
    return {
        "variant": group,
        "completed": bool(row and row.onboarding_completed_at),
        "answers": (row.onboarding_answers if row else None) or {},
        "monetized": get_config().monetization_enabled,
        "preview": {**eligibility, "task": task},
    }


@router.post("/answers")
async def save_answers(request: Request, db: AsyncSession = Depends(get_db)):
    """What the person makes (``makes``), for the examples and the plan Agent suggests."""
    ctx = await _ctx(request, db)
    body = await _body(request)
    makes = body.get("makes")
    if makes not in MAKES:
        raise HTTPException(422, f"makes must be one of {sorted(MAKES)}")
    await db.execute(
        text(
            "UPDATE users SET onboarding_answers = COALESCE(onboarding_answers, '{}'::jsonb) || CAST(:answers AS JSONB) WHERE id = :id"
        ),
        {"answers": json.dumps({"makes": makes}), "id": ctx.user_id},
    )
    await db.commit()
    return {"ok": True}


@router.post("/complete")
async def complete(request: Request, db: AsyncSession = Depends(get_db)):
    """The guided onboarding ended, finished or skipped. It isn't shown again."""
    ctx = await _ctx(request, db)
    await db.execute(
        text("UPDATE users SET onboarding_completed_at = COALESCE(onboarding_completed_at, NOW()) WHERE id = :id"),
        {"id": ctx.user_id},
    )
    await db.commit()
    return {"ok": True}


async def _count_link_check(user_id: str) -> int:
    config = get_config()
    client = redis.Redis(
        host=config.redis_host, port=config.redis_port, password=config.redis_password, decode_responses=True
    )
    try:
        key = f"onboarding:link-checks:{user_id}"
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, 3600)
        return int(count)
    finally:
        await client.aclose()


@router.post("/link")
async def check_link(request: Request, db: AsyncSession = Depends(get_db)):
    """A look at a video link before anything is made: title, length, picture, and about how
    many clips it holds. Nothing is created or charged."""
    ctx = await _ctx(request, db)
    body = await _body(request)
    url = body.get("url")
    if not isinstance(url, str) or not url.strip().lower().startswith(("http://", "https://")) or len(url) > 2000:
        raise HTTPException(422, {"code": "LINK_INVALID", "message": "Paste a link to a video."})
    url = url.strip()
    try:
        source_type = VideoService().determine_source_type(url)
    except ValueError:
        source_type = None
    if source_type not in {"youtube", "twitch", "kick"}:
        raise HTTPException(422, {"code": "LINK_UNSUPPORTED", "message": "Paste a YouTube, Twitch or Kick link."})
    try:
        if await _count_link_check(ctx.user_id) > LINK_CHECKS_PER_HOUR:
            raise HTTPException(429, {"code": "LINK_CHECKS_LIMIT", "message": "That's a lot of links. Try again in a bit."})
    except HTTPException:
        raise
    except Exception:
        logger.warning("Onboarding link counter unavailable", exc_info=True)

    thumbnail = None
    if source_type == "youtube":
        url = canonical_youtube_url(url) or url
        metadata = await _preflight_youtube_source(url, None)
        video_id = url.rsplit("v=", 1)[-1][:20] if "v=" in url else None
        thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else None
    else:
        metadata = await _preflight_stream_source(url, source_type, None)
        thumbnail = metadata.get("thumbnail") if isinstance(metadata.get("thumbnail"), str) else None
    duration = float(metadata.get("duration") or 0)
    eligibility = await preview_eligibility(db, ctx.user_id, ctx.team_id, client_platform(request))
    return {
        "url": url,
        "source_type": source_type,
        "title": metadata.get("title") or None,
        "channel": metadata.get("uploader") or metadata.get("channel") or None,
        "duration_seconds": round(duration),
        "thumbnail_url": thumbnail,
        "estimated_clips": max_final_clips_for_duration(duration, get_config().max_clips) if duration else None,
        "preview": {
            **eligibility,
            "fits": duration <= eligibility["max_minutes"] * 60,
        },
    }
