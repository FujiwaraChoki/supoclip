from __future__ import annotations

from dataclasses import dataclass
import hmac
import hashlib
import time
from typing import TYPE_CHECKING, Optional

from fastapi import HTTPException, Request

from .config import Config
from .observability import bind_request_user

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


USER_ID_HEADER = "x-supoclip-user-id"
TEAM_ID_HEADER = "x-supoclip-team-id"
TIMESTAMP_HEADER = "x-supoclip-ts"
SIGNATURE_HEADER = "x-supoclip-signature"

# Programmatic clients (e.g. the MCP server) authenticate with a per-user API
# key instead of the frontend's HMAC-signed session headers.
API_KEY_HEADER = "x-api-key"
API_KEY_PREFIX = "sk_"


@dataclass(frozen=True)
class AuthContext:
    """Verified caller identity and optional active team workspace."""

    user_id: str
    team_id: Optional[str] = None
    team_role: Optional[str] = None
    via_api_key: bool = False

    @property
    def is_team_manager(self) -> bool:
        return self.team_role in {"owner", "admin"}


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


def _normalize_team_id(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    cleaned = value.strip()
    return cleaned or None


def _expected_signature(
    secret: str,
    user_id: str,
    timestamp: str,
    team_id: Optional[str] = None,
) -> str:
    if team_id:
        payload = f"{user_id}:{team_id}:{timestamp}".encode("utf-8")
    else:
        payload = f"{user_id}:{timestamp}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def get_signed_identity(
    request: Request, config: Config
) -> tuple[str, Optional[str]]:
    user_id = request.headers.get(USER_ID_HEADER)
    team_id = _normalize_team_id(request.headers.get(TEAM_ID_HEADER))
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

    expected = _expected_signature(
        config.backend_auth_secret, user_id, timestamp, team_id
    )
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="Invalid auth signature")

    return user_id, team_id


def get_signed_user_id(request: Request, config: Config) -> str:
    user_id, _team_id = get_signed_identity(request, config)
    return user_id


def _get_authenticated_identity(
    request: Request, config: Config
) -> tuple[str, Optional[str]]:
    if config.backend_auth_secret:
        has_signed_headers = bool(
            request.headers.get(TIMESTAMP_HEADER) or request.headers.get(SIGNATURE_HEADER)
        )
        if has_signed_headers or not config.allow_unsigned_backend_auth:
            return get_signed_identity(request, config)

    if config.allow_unsigned_backend_auth:
        user_id = request.headers.get(USER_ID_HEADER)
        if not user_id:
            raise HTTPException(status_code=401, detail="User authentication required")
        team_id = _normalize_team_id(request.headers.get(TEAM_ID_HEADER))
        return user_id, team_id

    raise HTTPException(
        status_code=503,
        detail="Server authentication is not configured",
    )


def get_authenticated_identity(
    request: Request, config: Config
) -> tuple[str, Optional[str]]:
    user_id, team_id = _get_authenticated_identity(request, config)
    bind_request_user(request, user_id)
    return user_id, team_id


def get_authenticated_user_id(request: Request, config: Config) -> str:
    user_id, _team_id = get_authenticated_identity(request, config)
    return user_id


async def _resolve_api_key_context(
    request: Request, db: "AsyncSession", raw_key: str
) -> AuthContext:
    """The key's owner and workspace. A team key stops working when its
    creator leaves the team, on every route."""
    from .repositories.api_key_repository import ApiKeyRepository
    from .repositories.team_repository import TeamRepository

    key_context = await ApiKeyRepository.resolve_key_context(db, hash_api_key(raw_key))
    if not key_context or not key_context.get("user_id"):
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")

    user_id = str(key_context["user_id"])
    team_id = _normalize_team_id(key_context.get("team_id"))
    bind_request_user(request, user_id)

    team_role: Optional[str] = None
    if team_id:
        team_role = await TeamRepository.get_member_role(db, team_id, user_id)
        if not team_role:
            raise HTTPException(
                status_code=403,
                detail="API key owner is no longer a member of this team",
            )

    return AuthContext(
        user_id=user_id,
        team_id=team_id,
        team_role=team_role,
        via_api_key=True,
    )


async def resolve_auth_context(
    request: Request,
    db: "AsyncSession",
    config: Config,
    *,
    session_only: bool = False,
) -> AuthContext:
    """
    Resolve verified user + optional active team workspace.

    Team membership is ALWAYS verified against ``team_members`` in PostgreSQL;
    a signed ``x-supoclip-team-id`` header or team-scoped API key is never
    trusted without an active membership row.
    """
    from .repositories.team_repository import TeamRepository

    if not session_only:
        raw_key = extract_api_key(request)
        if raw_key:
            return await _resolve_api_key_context(request, db, raw_key)

    user_id, team_id = get_authenticated_identity(request, config)
    team_role = None
    if team_id:
        if db is None:
            raise HTTPException(status_code=403, detail="Unable to verify team membership")
        team_role = await TeamRepository.get_member_role(db, team_id, user_id)
        if not team_role:
            raise HTTPException(status_code=403, detail="Not a member of this team")

    return AuthContext(
        user_id=user_id,
        team_id=team_id,
        team_role=team_role,
        via_api_key=False,
    )


async def resolve_authenticated_user_id(
    request: Request, db: "AsyncSession", config: Config
) -> str:
    """
    Resolve the authenticated user for a request.

    An API key (``Authorization: Bearer`` / ``x-api-key``) takes precedence and
    is validated against the database. When no API key is present this falls
    back to the frontend's HMAC-signed session headers.
    """
    raw_key = extract_api_key(request)
    if raw_key:
        return (await _resolve_api_key_context(request, db, raw_key)).user_id

    return get_authenticated_user_id(request, config)
