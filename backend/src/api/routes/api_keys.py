"""
API key management routes.

These endpoints let an authenticated user mint, list and revoke their own API
keys. They are authenticated with the frontend's signed session headers (not
with an API key) so that a leaked key cannot be used to create more keys.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import get_authenticated_user_id
from ...config import get_config
from ...database import get_db
from ...services.api_key_service import ApiKeyLimitExceeded, ApiKeyService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api-keys", tags=["api-keys"])


def _get_user_id(request: Request) -> str:
    """Resolve the user from signed session headers (never from an API key)."""
    return get_authenticated_user_id(request, get_config())


@router.get("/")
async def list_api_keys(request: Request, db: AsyncSession = Depends(get_db)):
    """List the authenticated user's API keys (metadata only, no secrets)."""
    user_id = _get_user_id(request)
    service = ApiKeyService(db)
    keys = await service.list_api_keys(user_id)
    return {"api_keys": keys, "total": len(keys)}


@router.post("/")
async def create_api_key(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Create a new API key.

    Returns the plaintext key exactly once in the ``key`` field; it cannot be
    retrieved again afterwards.
    """
    data = await request.json()
    name = data.get("name", "API Key")

    user_id = _get_user_id(request)
    service = ApiKeyService(db)
    try:
        created = await service.create_api_key(user_id, name)
    except ApiKeyLimitExceeded as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Error creating API key: %s", exc)
        raise HTTPException(status_code=500, detail="Error creating API key")

    return {"api_key": created, "message": "API key created. Copy it now — it won't be shown again."}


@router.delete("/{key_id}")
async def revoke_api_key(
    key_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Revoke (permanently disable) one of the user's API keys."""
    user_id = _get_user_id(request)
    service = ApiKeyService(db)
    revoked = await service.revoke_api_key(user_id, key_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="API key not found")
    return {"message": "API key revoked"}
