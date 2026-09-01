"""
Social publishing routes.

Connecting and disconnecting accounts requires the frontend's signed session
headers (an API key must not be able to attach new social accounts). Posting
and reading performance data accept either an API key or session headers so
programmatic clients (MCP, scripts) can publish too.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import get_authenticated_user_id, resolve_authenticated_user_id
from ...config import get_config
from ...database import get_db
from ...services.social_service import SocialError, SocialService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/social", tags=["social"])


def _service(request: Request, db: AsyncSession) -> SocialService:
    queue_adapter = getattr(request.app.state, "queue_adapter", None)
    return SocialService(db, get_config(), queue_adapter=queue_adapter)


async def _user_id(request: Request, db: AsyncSession) -> str:
    return await resolve_authenticated_user_id(request, db, get_config())


def _session_user_id(request: Request) -> str:
    """Resolve the user from signed session headers only (never an API key)."""
    return get_authenticated_user_id(request, get_config())


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
    """Which platforms this server can publish to (based on configured OAuth apps)."""
    await _user_id(request, db)
    return {"providers": _service(request, db).list_providers()}


@router.get("/connections")
async def list_connections(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user_id(request, db)
    connections = await _service(request, db).list_connections(user_id)
    return {"connections": connections, "total": len(connections)}


@router.post("/connections/{provider}/authorize")
async def start_connection(
    provider: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Begin the OAuth handshake; the caller redirects the browser to ``authorize_url``."""
    user_id = _session_user_id(request)
    try:
        return await _service(request, db).start_connection(user_id, provider)
    except SocialError as exc:
        raise _http_error(exc)


@router.post("/connections/{provider}/callback")
async def complete_connection(
    provider: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Finish the OAuth handshake with the ``code`` and ``state`` the platform returned."""
    user_id = _session_user_id(request)
    body = await _json_body(request)
    try:
        connection = await _service(request, db).complete_connection(
            user_id,
            provider,
            code=str(body.get("code") or ""),
            state=str(body.get("state") or ""),
        )
    except SocialError as exc:
        raise _http_error(exc)
    return {"connection": connection}


@router.delete("/connections/{account_id}")
async def disconnect_account(
    account_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = _session_user_id(request)
    removed = await _service(request, db).disconnect(user_id, account_id)
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
):
    user_id = await _user_id(request, db)
    posts = await _service(request, db).list_posts(
        user_id, task_id=task_id, clip_id=clip_id, status=status, limit=limit
    )
    return {"posts": posts, "total": len(posts)}


@router.post("/posts")
async def create_post(request: Request, db: AsyncSession = Depends(get_db)):
    """Publish (or schedule) a clip to a connected account."""
    user_id = await _user_id(request, db)
    body = await _json_body(request)
    for field in ("task_id", "clip_id", "social_account_id"):
        if not body.get(field):
            raise HTTPException(status_code=422, detail=f"{field} is required")
    hashtags = body.get("hashtags")
    if isinstance(hashtags, str):
        hashtags = [hashtags]
    try:
        post = await _service(request, db).create_post(
            user_id,
            task_id=str(body["task_id"]),
            clip_id=str(body["clip_id"]),
            social_account_id=str(body["social_account_id"]),
            title=body.get("title"),
            caption=body.get("caption"),
            hashtags=hashtags,
            privacy_level=body.get("privacy_level"),
            scheduled_for=body.get("scheduled_for"),
        )
    except SocialError as exc:
        raise _http_error(exc)
    return {"post": post}


@router.get("/performance")
async def get_performance(request: Request, db: AsyncSession = Depends(get_db)):
    """Aggregate audience performance, grouped the same way the AI uses it."""
    user_id = await _user_id(request, db)
    return await _service(request, db).get_performance_summary(user_id)


@router.get("/posts/{post_id}")
async def get_post(post_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user_id(request, db)
    try:
        return {"post": await _service(request, db).get_post(user_id, post_id)}
    except SocialError as exc:
        raise _http_error(exc)


@router.post("/posts/{post_id}/cancel")
async def cancel_post(post_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user_id(request, db)
    try:
        return {"post": await _service(request, db).cancel_post(user_id, post_id)}
    except SocialError as exc:
        raise _http_error(exc)


@router.post("/posts/{post_id}/retry")
async def retry_post(post_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user_id(request, db)
    try:
        return {"post": await _service(request, db).retry_post(user_id, post_id)}
    except SocialError as exc:
        raise _http_error(exc)


@router.post("/posts/{post_id}/refresh-metrics")
async def refresh_post_metrics(
    post_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _user_id(request, db)
    service = _service(request, db)
    try:
        post = await service.get_post(user_id, post_id)
        refreshed = await service.refresh_post_metrics(post, user_id=user_id)
    except SocialError as exc:
        raise _http_error(exc)
    return {"post": refreshed}


@router.delete("/posts/{post_id}")
async def delete_post(post_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user_id(request, db)
    try:
        deleted = await _service(request, db).delete_post(user_id, post_id)
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
    path = await _service(request, db).get_public_media_path(media_token)
    if path is None:
        raise HTTPException(status_code=404, detail="Media not found")
    # Release the session before streaming (see get_clip_file in tasks.py).
    await db.close()
    return FileResponse(
        path=str(path),
        media_type="video/mp4",
        filename=path.name,
        content_disposition_type="inline",
        headers={"Cache-Control": "private, no-store"},
    )
