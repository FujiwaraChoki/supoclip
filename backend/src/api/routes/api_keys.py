"""
API key management routes.

These endpoints let an authenticated user mint, list and revoke personal or
team-scoped API keys. They are authenticated with the frontend's signed session
headers (not with an API key) so that a leaked key cannot be used to create
more keys.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import AuthContext, resolve_auth_context
from ...config import get_config
from ...database import get_db
from ...models import User
from ...services.api_key_email_service import ApiKeyEmailService
from ...services.api_key_service import ApiKeyLimitExceeded, ApiKeyService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api-keys", tags=["api-keys"])


async def notify_api_key_created(user: User, created: dict) -> None:
    """Send the "new API key" security email; never fails the request."""
    try:
        email_service = ApiKeyEmailService(get_config())
        if not email_service.is_configured:
            logger.warning(
                "API key %s was created for user %s but SES is not configured",
                created.get("id"),
                user.id,
            )
        else:
            await email_service.send_created_email(user, created)
    except Exception:
        logger.exception(
            "Failed to send API key creation email for key %s and user %s",
            created.get("id"),
            user.id,
        )


async def _get_session_context(request: Request, db: AsyncSession) -> AuthContext:
    """Resolve user + verified team membership from signed session headers only."""
    return await resolve_auth_context(request, db, get_config(), session_only=True)


@router.get("/")
async def list_api_keys(request: Request, db: AsyncSession = Depends(get_db)):
    """List the active workspace's API keys (metadata only, no secrets)."""
    ctx = await _get_session_context(request, db)
    service = ApiKeyService(db)
    keys = await service.list_api_keys(
        ctx.user_id, **({"team_id": ctx.team_id} if ctx.team_id else {})
    )
    return {"api_keys": keys, "total": len(keys), "team_id": ctx.team_id}


@router.post("/")
async def create_api_key(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Create a new API key scoped to the active workspace.

    Team API keys require the owner or admin role.
    """
    data = await request.json()
    name = data.get("name", "API Key")

    ctx = await _get_session_context(request, db)
    if ctx.team_id and not ctx.is_team_manager:
        raise HTTPException(
            status_code=403,
            detail="Only team owners and admins can create team API keys.",
        )

    user_result = await db.execute(select(User).where(User.id == ctx.user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.emailVerified:
        raise HTTPException(
            status_code=403,
            detail="Verify your email address before creating an API key.",
        )

    service = ApiKeyService(db)
    try:
        created = await service.create_api_key(
            ctx.user_id, name, **({"team_id": ctx.team_id} if ctx.team_id else {})
        )
    except ApiKeyLimitExceeded as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Error creating API key: %s", exc)
        raise HTTPException(status_code=500, detail="Error creating API key")

    await notify_api_key_created(user, created)

    return {"api_key": created, "message": "API key created. Copy it now — it won't be shown again."}


@router.delete("/{key_id}")
async def revoke_api_key(
    key_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Revoke (permanently disable) an API key in the active workspace."""
    ctx = await _get_session_context(request, db)
    if ctx.team_id and not ctx.is_team_manager:
        raise HTTPException(
            status_code=403,
            detail="Only team owners and admins can revoke team API keys.",
        )
    service = ApiKeyService(db)
    revoked = await service.revoke_api_key(
        ctx.user_id, key_id, **({"team_id": ctx.team_id} if ctx.team_id else {})
    )
    if not revoked:
        raise HTTPException(status_code=404, detail="API key not found")
    return {"message": "API key revoked"}
