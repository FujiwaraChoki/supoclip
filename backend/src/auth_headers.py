from __future__ import annotations

import hmac
import hashlib
import time
from typing import TYPE_CHECKING, Optional

from fastapi import HTTPException, Request

from .config import Config

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


USER_ID_HEADER = "x-supoclip-user-id"
TIMESTAMP_HEADER = "x-supoclip-ts"
SIGNATURE_HEADER = "x-supoclip-signature"

# Programmatic clients (e.g. the MCP server) authenticate with a per-user API
# key instead of the frontend's HMAC-signed session headers.
API_KEY_HEADER = "x-api-key"
API_KEY_PREFIX = "sk_"


def hash_api_key(raw_key: str) -> str:
    """Return the SHA-256 hex digest used to look a key up in the database."""
    return hashlib.sha256(raw_key.strip().encode("utf-8")).hexdigest()


def extract_api_key(request: Request) -> Optional[str]:
    """
    Pull an API key out of the request.

    Accepts either ``Authorization: Bearer <key>`` or ``x-api-key: <key>``.
    Returns ``None`` when no key is present.
    """
    authorization = request.headers.get("authorization")
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        if token:
            return token

    header_key = request.headers.get(API_KEY_HEADER)
    if header_key and header_key.strip():
        return header_key.strip()

    return None


def _expected_signature(secret: str, user_id: str, timestamp: str) -> str:
    payload = f"{user_id}:{timestamp}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def get_signed_user_id(request: Request, config: Config) -> str:
    user_id = request.headers.get(USER_ID_HEADER)
    timestamp = request.headers.get(TIMESTAMP_HEADER)
    signature = request.headers.get(SIGNATURE_HEADER)

    if not user_id or not timestamp or not signature:
        raise HTTPException(status_code=401, detail="Signed authentication required")

    if not config.backend_auth_secret:
        raise HTTPException(
            status_code=500,
            detail="Server authentication secret is not configured",
        )

    try:
        timestamp_int = int(timestamp)
    except ValueError as e:
        raise HTTPException(status_code=401, detail="Invalid auth timestamp") from e

    now = int(time.time())
    if abs(now - timestamp_int) > config.auth_signature_ttl_seconds:
        raise HTTPException(status_code=401, detail="Expired auth signature")

    expected = _expected_signature(config.backend_auth_secret, user_id, timestamp)
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="Invalid auth signature")

    return user_id


def get_authenticated_user_id(request: Request, config: Config) -> str:
    if config.backend_auth_secret:
        has_signed_headers = bool(
            request.headers.get(TIMESTAMP_HEADER) or request.headers.get(SIGNATURE_HEADER)
        )
        if has_signed_headers or not config.allow_unsigned_backend_auth:
            return get_signed_user_id(request, config)

    if config.allow_unsigned_backend_auth:
        user_id = request.headers.get(USER_ID_HEADER)
        if not user_id:
            raise HTTPException(status_code=401, detail="User authentication required")
        return user_id

    raise HTTPException(
        status_code=503,
        detail="Server authentication is not configured",
    )


async def resolve_authenticated_user_id(
    request: Request, db: "AsyncSession", config: Config
) -> str:
    """
    Resolve the authenticated user for a request.

    An API key (``Authorization: Bearer`` / ``x-api-key``) takes precedence and
    is validated against the database. When no API key is present this falls
    back to the frontend's HMAC-signed session headers. This lets the same
    endpoints serve both the web app and programmatic clients like the MCP
    server.
    """
    raw_key = extract_api_key(request)
    if raw_key:
        # Imported lazily to avoid a circular import at module load time.
        from .repositories.api_key_repository import ApiKeyRepository

        user_id = await ApiKeyRepository.resolve_user_id(db, hash_api_key(raw_key))
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid or revoked API key")
        return user_id

    return get_authenticated_user_id(request, config)
