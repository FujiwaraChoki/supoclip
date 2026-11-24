"""
Social publishing routes.

Connecting and disconnecting accounts requires the frontend's signed session
headers (an API key must not be able to attach new social accounts). In a team
workspace, connecting and disconnecting social accounts requires the owner or
admin role. Posting and reading performance data accept either an API key or
session headers so programmatic clients (MCP, scripts) can publish too.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import AuthContext, resolve_auth_context
from ...feature_flags import client_platform
from ...config import get_config
from ...database import get_db
from ...media_storage import is_s3
from ...services.social_service import SocialError, SocialService
from ...utils.async_helpers import run_in_thread

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/social", tags=["social"])


def _service(request: Request, db: AsyncSession) -> SocialService:
    queue_adapter = getattr(request.app.state, "queue_adapter", None)
    return SocialService(
        db, get_config(), queue_adapter=queue_adapter, platform=client_platform(request)
    )


async def _auth_context(
    request: Request, db: AsyncSession, *, session_only: bool = False
) -> AuthContext:
    return await resolve_auth_context(
        request, db, get_config(), session_only=session_only
    )


async def _allowed_context(
    request: Request,
    db: AsyncSession,
    *,
    session_only: bool = False,
    require_team_manager: bool = False,
) -> AuthContext:
    """Resolve user + verified team workspace and enforce rollout & role."""
    ctx = await _auth_context(request, db, session_only=session_only)
    if not await _service(request, db).has_access(ctx.user_id):
        raise HTTPException(
            status_code=403, detail="Social publishing isn't available on your account yet."
        )
    if require_team_manager and ctx.team_id and not ctx.is_team_manager:
        raise HTTPException(
            status_code=403,
            detail="Only team owners and admins can manage team social accounts.",
        )
    return ctx


def _http_error(exc: SocialError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


async def _json_body(request: Request) -> Dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - empty or invalid body
        return {}
    return payload if isinstance(payload, dict) else {}


# ----------------------------------------------------------------------
# Providers & connections
# ----------------------------------------------------------------------
@router.get("/providers")
async def list_providers(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Which platforms this server can publish to (based on configured OAuth apps).

    Users outside the rollout get ``enabled: false`` and every platform reported
    as unconfigured, so clients that predate the flag simply hide publishing.
    """
    ctx = await _auth_context(request, db)
    service = _service(request, db)
    providers = service.list_providers()
    enabled = await service.has_access(ctx.user_id)
    if not enabled:
        providers = [{**provider, "configured": False} for provider in providers]
    else:
        # A paused platform (TikTok) reads as unconfigured + paused so the web
        # explains why and older clients (iOS) hide it. Other flag-gated
        # platforms (Instagram) read as unconfigured ("Coming soon") for
        # non-admins while admins bypass for testing.
        filtered = []
        for provider in providers:
            if not provider["configured"]:
                filtered.append(provider)
            elif not await service.provider_flag_enabled(provider["provider"], ctx.user_id):
                if provider["provider"] == "tiktok":
                    filtered.append({**provider, "configured": False, "paused": True})
                else:
                    filtered.append({**provider, "configured": False})
            else:
                filtered.append(provider)
        providers = filtered
    can_schedule = enabled and await service.can_schedule(ctx.user_id, team_id=ctx.team_id)
    return {"enabled": enabled, "can_schedule": can_schedule, "providers": providers}


@router.get("/connections")
async def list_connections(request: Request, db: AsyncSession = Depends(get_db)):
    ctx = await _allowed_context(request, db)
    connections = await _service(request, db).list_connections(
        ctx.user_id, team_id=ctx.team_id
    )
    return {"connections": connections, "total": len(connections)}


@router.get("/connections/{account_id}/publishing-options")
async def publishing_options(
    account_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    ctx = await _allowed_context(request, db)
    try:
        return await _service(request, db).publishing_options(
            ctx.user_id, account_id, team_id=ctx.team_id
        )
    except SocialError as exc:
        raise _http_error(exc)


@router.post("/connections/{provider}/authorize")
async def start_connection(
    provider: str, request: Request, db: AsyncSession = Depends(get_db)
):
    ctx = await _allowed_context(
        request, db, session_only=True, require_team_manager=True
    )
    body = await _json_body(request)
    client = body.get("client") if isinstance(body.get("client"), str) else None
    try:
        return await _service(request, db).start_connection(
            ctx.user_id, provider, client=client, team_id=ctx.team_id
        )
    except SocialError as exc:
        raise _http_error(exc)


@router.post("/connections/{provider}/callback")
async def complete_connection(
    provider: str, request: Request, db: AsyncSession = Depends(get_db)
):
    ctx = await _allowed_context(
        request, db, session_only=True, require_team_manager=True
    )
    body = await _json_body(request)
    try:
        connection = await _service(request, db).complete_connection(
            ctx.user_id,
            provider,
            code=str(body.get("code") or ""),
            state=str(body.get("state") or ""),
            team_id=ctx.team_id,
        )
    except SocialError as exc:
        raise _http_error(exc)
    return {"connection": connection}


@router.delete("/connections")
async def disconnect_all_accounts(request: Request, db: AsyncSession = Depends(get_db)):
    """Disconnect (and revoke at the platform) every account; used on account/team deletion."""
    ctx = await _auth_context(request, db, session_only=True)
    if ctx.team_id and not ctx.is_team_manager:
        raise HTTPException(
            status_code=403,
            detail="Only team owners and admins can manage team social accounts.",
        )
    removed = await _service(request, db).disconnect_all(
        ctx.user_id, team_id=ctx.team_id
    )
    return {"message": "Accounts disconnected", "removed": removed}


@router.delete("/connections/{account_id}")
async def disconnect_account(
    account_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    ctx = await _auth_context(request, db, session_only=True)
    if ctx.team_id and not ctx.is_team_manager:
        raise HTTPException(
            status_code=403,
            detail="Only team owners and admins can manage team social accounts.",
        )
    removed = await _service(request, db).disconnect(
        ctx.user_id, account_id, team_id=ctx.team_id
    )
    if not removed:
        raise HTTPException(status_code=404, detail="Connected account not found")
    return {"message": "Account disconnected"}


# ----------------------------------------------------------------------
# Posts
# ----------------------------------------------------------------------
@router.get("/posts")
async def list_posts(
    request: Request,
    db: AsyncSession = Depends(get_db),
    task_id: Optional[str] = None,
    clip_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
    starts_at: Optional[str] = None,
    ends_at: Optional[str] = None,
):
    """Posts, newest first; with ``starts_at``/``ends_at``, the calendar window in time order."""
    ctx = await _allowed_context(request, db)
    try:
        posts = await _service(request, db).list_posts(
            ctx.user_id,
            task_id=task_id,
            clip_id=clip_id,
            status=status,
            limit=limit,
            team_id=ctx.team_id,
            starts_at=starts_at,
            ends_at=ends_at,
        )
    except SocialError as exc:
        raise _http_error(exc)
    return {"posts": posts, "total": len(posts)}


@router.post("/captions")
async def generate_caption(request: Request, db: AsyncSession = Depends(get_db)):
    """Draft a social caption without publishing or scheduling anything."""
    ctx = await _allowed_context(request, db)
    body = await _json_body(request)
    for field in ("task_id", "clip_id", "provider"):
        if not isinstance(body.get(field), str) or not body[field].strip():
            raise HTTPException(status_code=422, detail=f"{field} is required")
    try:
        return await _service(request, db).generate_caption(
            ctx.user_id,
            task_id=body["task_id"],
            clip_id=body["clip_id"],
            provider=body["provider"],
            team_id=ctx.team_id,
        )
    except SocialError as exc:
        raise _http_error(exc)


@router.post("/posts")
async def create_post(request: Request, db: AsyncSession = Depends(get_db)):
    """Publish (or schedule) a clip to a connected account."""
    ctx = await _allowed_context(request, db)
    body = await _json_body(request)
    for field in ("task_id", "clip_id", "social_account_id"):
        if not body.get(field):
            raise HTTPException(status_code=422, detail=f"{field} is required")
    hashtags = body.get("hashtags")
    if isinstance(hashtags, str):
        hashtags = [hashtags]
    try:
        post = await _service(request, db).create_post(
            ctx.user_id,
            task_id=str(body["task_id"]),
            clip_id=str(body["clip_id"]),
            social_account_id=str(body["social_account_id"]),
            title=body.get("title"),
            caption=body.get("caption"),
            hashtags=hashtags,
            privacy_level=body.get("privacy_level"),
            scheduled_for=body.get("scheduled_for"),
            team_id=ctx.team_id,
        )
    except SocialError as exc:
        raise _http_error(exc)
    return {"post": post}


@router.get("/performance")
async def get_performance(request: Request, db: AsyncSession = Depends(get_db)):
    """Aggregate audience performance, grouped the same way the AI uses it."""
    ctx = await _allowed_context(request, db)
    return await _service(request, db).get_performance_summary(
        ctx.user_id, team_id=ctx.team_id
    )


@router.get("/performance/timeseries")
async def get_views_timeseries(
    request: Request, db: AsyncSession = Depends(get_db), days: int = 30
):
    """Daily new views across published posts, for the analytics chart."""
    ctx = await _allowed_context(request, db)
    return await _service(request, db).get_views_timeseries(
        ctx.user_id, days=days, team_id=ctx.team_id
    )


@router.get("/posts/{post_id}")
async def get_post(post_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    ctx = await _allowed_context(request, db)
    try:
        return {"post": await _service(request, db).get_post(ctx.user_id, post_id, team_id=ctx.team_id)}
    except SocialError as exc:
        raise _http_error(exc)


@router.patch("/posts/{post_id}")
async def update_post(post_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Reschedule or reword a scheduled post."""
    ctx = await _allowed_context(request, db)
    body = await _json_body(request)
    hashtags = body.get("hashtags")
    if isinstance(hashtags, str):
        hashtags = [hashtags]
    for field in ("scheduled_for", "title", "caption"):
        if field in body and body[field] is not None and not isinstance(body[field], str):
            raise HTTPException(status_code=422, detail=f"{field} must be a string")
    if hashtags is not None and not isinstance(hashtags, list):
        raise HTTPException(status_code=422, detail="hashtags must be a list")
    try:
        post = await _service(request, db).update_post(
            ctx.user_id,
            post_id,
            scheduled_for=body.get("scheduled_for"),
            title=body.get("title"),
            caption=body.get("caption"),
            hashtags=hashtags,
            team_id=ctx.team_id,
        )
    except SocialError as exc:
        raise _http_error(exc)
    return {"post": post}


@router.post("/posts/{post_id}/cancel")
async def cancel_post(post_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    ctx = await _allowed_context(request, db)
    try:
        return {"post": await _service(request, db).cancel_post(ctx.user_id, post_id, team_id=ctx.team_id)}
    except SocialError as exc:
        raise _http_error(exc)


@router.post("/posts/{post_id}/retry")
async def retry_post(post_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    ctx = await _allowed_context(request, db)
    try:
        return {"post": await _service(request, db).retry_post(ctx.user_id, post_id, team_id=ctx.team_id)}
    except SocialError as exc:
        raise _http_error(exc)


@router.post("/posts/{post_id}/refresh-metrics")
async def refresh_post_metrics(
    post_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    ctx = await _allowed_context(request, db)
    service = _service(request, db)
    try:
        post = await service.get_post(ctx.user_id, post_id, team_id=ctx.team_id)
        refreshed = await service.refresh_post_metrics(post, user_id=ctx.user_id)
    except SocialError as exc:
        raise _http_error(exc)
    return {"post": refreshed}


@router.delete("/posts/{post_id}")
async def delete_post(post_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    ctx = await _allowed_context(request, db)
    try:
        deleted = await _service(request, db).delete_post(
            ctx.user_id, post_id, team_id=ctx.team_id
        )
    except SocialError as exc:
        raise _http_error(exc)
    if not deleted:
        raise HTTPException(status_code=404, detail="Post not found")
    return {"message": "Post deleted"}


# ----------------------------------------------------------------------
# Public media (Instagram fetches the video from a URL)
# ----------------------------------------------------------------------
@router.get("/media/{media_token}")
async def get_public_media(
    media_token: str, request: Request, db: AsyncSession = Depends(get_db, scope="function")
):
    """Serve a clip to a platform using a short-lived, unguessable token."""
    service = _service(request, db)
    reference = await service.get_public_media_reference(media_token)
    if reference is None:
        raise HTTPException(status_code=404, detail="Media not found")
    # Release the session before streaming (see get_clip_file in tasks.py).
    await db.close()
    if is_s3(reference):
        url = await run_in_thread(service.public_media_redirect_url, reference)
        return RedirectResponse(url, status_code=307, headers={"Cache-Control": "private, no-store"})
    path = Path(reference)
    return FileResponse(
        path=str(path),
        media_type="video/mp4",
        filename=path.name,
        content_disposition_type="inline",
        headers={"Cache-Control": "private, no-store"},
    )
