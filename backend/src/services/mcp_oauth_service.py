"""
OAuth for the hosted MCP server: client registry, consent and code exchange.

The MCP server is the OAuth authorization server that connectors (claude.ai,
ChatGPT, ...) talk to. It keeps no state of its own; it calls the internal
endpoints in ``api/routes/mcp_oauth.py``, which use this service:

* Registered clients are rows in ``mcp_oauth_clients`` (PostgreSQL).
* A pending authorize request (30 minutes, long enough to verify an email
  address on the way) and an authorization code (10 minutes) are Redis
  entries, each consumed exactly once (``GETDEL``). Codes are
  stored under their SHA-256, never in plaintext.
* Approving consent creates nothing yet. The code exchange creates an ordinary
  API key named after the client and returns it as the access token, once. Only
  its hash is stored, as for every API key.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
import unicodedata
from ipaddress import ip_address
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import User
from ..repositories.api_key_repository import ApiKeyRepository
from ..repositories.mcp_oauth_repository import McpOAuthClientRepository
from .api_key_service import MAX_KEYS_PER_USER, ApiKeyLimitExceeded, ApiKeyService

SCOPE = "supoclip"
REQUEST_TTL_SECONDS = 1800
CODE_TTL_SECONDS = 600
MAX_REDIRECT_URIS = 10
CLIENT_NAME_MAX = 60

_REQUEST_KEY = "mcp_oauth:request:{}"
_CODE_KEY = "mcp_oauth:code:{}"
_RATE_KEY = "mcp_oauth:rate:{}:{}:{}"

# (limit, window seconds) per bucket. "global" counts every caller together.
RATE_LIMITS: Dict[str, Tuple[int, int]] = {
    "register": (20, 3600),
    "register:global": (1000, 3600),
    "token": (60, 600),
    "token:global": (3000, 600),
}

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class McpOAuthError(Exception):
    """An expected failure with a stable code for the caller."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def redirect_uri_allowed(uri: str) -> bool:
    """https anywhere, plain http only on loopback, never a fragment."""
    try:
        parts = urlsplit(uri)
    except ValueError:
        return False
    if parts.fragment or not parts.hostname:
        return False
    if parts.scheme == "https":
        return True
    if parts.scheme != "http":
        return False
    host = parts.hostname.lower()
    if host in _LOOPBACK_HOSTS:
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def clean_client_name(name: Optional[str]) -> str:
    text = unicodedata.normalize("NFC", name or "")
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:CLIENT_NAME_MAX].strip() or "MCP client"


def with_query(uri: str, params: Dict[str, Optional[str]]) -> str:
    """Add ``params`` to ``uri``'s query, keeping what is already there."""
    parts = urlsplit(uri)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query.extend((key, value) for key, value in params.items() if value is not None)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


class McpOAuthService:
    def __init__(self, db: AsyncSession, redis: Any):
        self.db = db
        self.redis = redis

    # -- clients -------------------------------------------------------------
    async def register_client(self, info: Dict[str, Any]) -> None:
        client_id = str(info.get("client_id") or "")
        redirect_uris = info.get("redirect_uris") or []
        if not client_id or len(client_id) > 64:
            raise McpOAuthError("invalid_client_metadata", "client_id is missing or too long.")
        if not isinstance(redirect_uris, list) or not 1 <= len(redirect_uris) <= MAX_REDIRECT_URIS:
            raise McpOAuthError("invalid_redirect_uri", f"Register 1 to {MAX_REDIRECT_URIS} redirect URIs.")
        for uri in redirect_uris:
            if not isinstance(uri, str) or len(uri) > 2000 or not redirect_uri_allowed(uri):
                raise McpOAuthError(
                    "invalid_redirect_uri",
                    "Redirect URIs must use https, or http on localhost, and have no fragment.",
                )
        secret = info.get("client_secret")
        metadata = {key: value for key, value in info.items() if key != "client_secret"}
        await McpOAuthClientRepository.create_client(
            self.db,
            client_id=client_id,
            client_secret=str(secret) if secret else None,
            client_name=clean_client_name(info.get("client_name")) if info.get("client_name") else None,
            metadata=metadata,
        )

    async def get_client(self, client_id: str) -> Optional[Dict[str, Any]]:
        row = await McpOAuthClientRepository.get_client(self.db, client_id)
        if row is None:
            return None
        info = dict(row["metadata"])
        info["client_id"] = row["client_id"]
        info["client_secret"] = row["client_secret"]
        return info

    # -- authorize requests ----------------------------------------------------
    async def create_request(self, params: Dict[str, Any]) -> str:
        client = await self.get_client(str(params.get("client_id") or ""))
        if client is None:
            raise McpOAuthError("invalid_client", "Unknown client.", 404)
        redirect_uri = str(params.get("redirect_uri") or "")
        if redirect_uri not in (client.get("redirect_uris") or []):
            raise McpOAuthError("invalid_redirect_uri", "redirect_uri is not registered for this client.")
        challenge = str(params.get("code_challenge") or "")
        if not 43 <= len(challenge) <= 128:
            raise McpOAuthError("invalid_request", "A PKCE S256 code_challenge is required.")
        request_id = secrets.token_urlsafe(24)
        payload = {
            "client_id": client["client_id"],
            "client_name": clean_client_name(client.get("client_name")),
            "redirect_uri": redirect_uri,
            "redirect_uri_provided_explicitly": bool(params.get("redirect_uri_provided_explicitly", True)),
            "code_challenge": challenge,
            "state": params.get("state"),
            "scopes": [SCOPE],
            "resource": params.get("resource"),
            "created_at": time.time(),
        }
        await self.redis.set(_REQUEST_KEY.format(request_id), json.dumps(payload), ex=REQUEST_TTL_SECONDS)
        return request_id

    async def _load_request(self, request_id: str) -> Dict[str, Any]:
        raw = await self.redis.get(_REQUEST_KEY.format(request_id)) if request_id else None
        if not raw:
            raise McpOAuthError(
                "request_expired",
                "This connection request has expired. Start connecting again from your app.",
                404,
            )
        return json.loads(raw)

    async def _user(self, user_id: str) -> User:
        user = (await self.db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if user is None:
            raise McpOAuthError("user_not_found", "User not found.", 404)
        return user

    async def _active_key_count(self, user_id: str) -> int:
        keys = await ApiKeyRepository.list_api_keys(self.db, user_id)
        return sum(1 for key in keys if not key.get("revoked"))

    async def describe_request(self, request_id: str, user_id: str) -> Dict[str, Any]:
        request = await self._load_request(request_id)
        user = await self._user(user_id)
        redirect = urlsplit(request["redirect_uri"])
        return {
            "client_name": request["client_name"],
            "redirect_host": redirect.hostname or "",
            "scopes": request["scopes"],
            "email_verified": bool(user.emailVerified),
            "at_key_limit": await self._active_key_count(user_id) >= MAX_KEYS_PER_USER,
            "max_keys": MAX_KEYS_PER_USER,
            "expires_at": request["created_at"] + REQUEST_TTL_SECONDS,
        }

    async def decide(self, request_id: str, user_id: str, approve: bool) -> str:
        """Consume the request and return where to send the browser."""
        if approve:
            await self._load_request(request_id)
            user = await self._user(user_id)
            if not user.emailVerified:
                raise McpOAuthError(
                    "email_not_verified", "Verify your email address before connecting an app.", 403
                )
            if await self._active_key_count(user_id) >= MAX_KEYS_PER_USER:
                raise McpOAuthError(
                    "key_limit",
                    f"You already have {MAX_KEYS_PER_USER} active API keys. Revoke one, then try again.",
                    409,
                )

        raw = await self.redis.getdel(_REQUEST_KEY.format(request_id))
        if not raw:
            raise McpOAuthError(
                "request_expired",
                "This connection request has expired. Start connecting again from your app.",
                404,
            )
        request = json.loads(raw)

        if not approve:
            return with_query(
                request["redirect_uri"],
                {"error": "access_denied", "error_description": "The user denied access.", "state": request.get("state")},
            )

        code = secrets.token_urlsafe(32)
        expires_at = time.time() + CODE_TTL_SECONDS
        payload = {
            "client_id": request["client_id"],
            "client_name": request["client_name"],
            "redirect_uri": request["redirect_uri"],
            "redirect_uri_provided_explicitly": request["redirect_uri_provided_explicitly"],
            "code_challenge": request["code_challenge"],
            "scopes": request["scopes"],
            "resource": request.get("resource"),
            "user_id": user_id,
            "expires_at": expires_at,
        }
        await self.redis.set(_CODE_KEY.format(hash_secret(code)), json.dumps(payload), ex=CODE_TTL_SECONDS)
        return with_query(request["redirect_uri"], {"code": code, "state": request.get("state")})

    # -- codes -----------------------------------------------------------------
    async def peek_code(self, code: str) -> Optional[Dict[str, Any]]:
        raw = await self.redis.get(_CODE_KEY.format(hash_secret(code))) if code else None
        if not raw:
            return None
        payload = json.loads(raw)
        payload.pop("user_id", None)
        return payload

    async def exchange_code(self, code: str, client_id: str) -> Tuple[User, Dict[str, Any]]:
        """Consume a code once and mint the API key that is the access token."""
        raw = await self.redis.getdel(_CODE_KEY.format(hash_secret(code))) if code else None
        if not raw:
            raise McpOAuthError("invalid_grant", "The authorization code is invalid or was already used.")
        payload = json.loads(raw)
        if not hmac.compare_digest(str(payload.get("client_id", "")), str(client_id)):
            raise McpOAuthError("invalid_grant", "The authorization code was issued to another client.")
        if float(payload.get("expires_at") or 0) < time.time():
            raise McpOAuthError("invalid_grant", "The authorization code has expired.")

        user = await self._user(payload["user_id"])
        name = f"{payload.get('client_name') or 'MCP client'} connector"[:120]
        try:
            created = await ApiKeyService(self.db).create_api_key(user.id, name)
        except ApiKeyLimitExceeded as exc:
            raise McpOAuthError("invalid_grant", str(exc)) from exc
        await McpOAuthClientRepository.touch_client(self.db, payload["client_id"])
        return user, created

    # -- rate limits -------------------------------------------------------------
    async def hit_rate_limit(self, bucket: str, caller: str) -> Tuple[bool, int]:
        """Count one call; return (allowed, retry_after_seconds)."""
        checks = [(bucket, caller)]
        if f"{bucket}:global" in RATE_LIMITS:
            checks.append((f"{bucket}:global", "all"))
        for name, who in checks:
            limit, window = RATE_LIMITS[name]
            now = int(time.time())
            window_start = now - now % window
            key = _RATE_KEY.format(name, hash_secret(who)[:32], window_start)
            count = await self.redis.incr(key)
            if count == 1:
                await self.redis.expire(key, window)
            if count > limit:
                return False, window_start + window - now
        return True, 0
