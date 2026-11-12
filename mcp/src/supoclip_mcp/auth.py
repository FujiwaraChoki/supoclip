"""
Bearer authentication for the hosted MCP server.

Every HTTP transport (Streamable HTTP and legacy SSE) runs behind the same
:class:`SupoClipAuthBackend` and :func:`require_auth` wrapper, so a request is
accepted or refused the same way whichever endpoint it reaches. Refusals come
back as JSON that says what was wrong and how to fix the client config.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Optional

import httpx
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from starlette.authentication import (
    AuthCredentials,
    AuthenticationBackend,
    AuthenticationError,
)
from starlette.datastructures import Headers
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

REQUIRED_SCOPE = "supoclip"

# Placeholders from our own docs and examples. A client sending one of these
# copied a snippet without pasting a real key in.
PLACEHOLDER_KEYS = frozenset(
    {
        "sk_your_supoclip_api_key",
        "sk_your_key_here",
        "sk_your_api_key",
        "sk_...",
        "sk_…",
    }
)

_CACHE_LIMIT = 10_000


class BackendUnavailable(AuthenticationError):
    """The SupoClip API could not be asked whether a key is valid."""


def is_placeholder_key(token: str) -> bool:
    value = token.strip().lower()
    if value in PLACEHOLDER_KEYS or value.startswith("sk_your"):
        return True
    # sk_xxxxxxxx… as in .env.example
    rest = value[3:] if value.startswith("sk_") else ""
    return len(rest) >= 3 and set(rest) <= {"x", "."}


def extract_token(headers: Headers) -> Optional[str]:
    """Return the API key from ``Authorization: Bearer`` or ``x-api-key``.

    A doubled ``Bearer Bearer sk_...`` (a client that adds the prefix to a
    value that already has it) is accepted.
    """
    authorization = (headers.get("authorization") or "").strip()
    if authorization:
        value = authorization
        while value[:7].lower() == "bearer ":
            value = value[7:].strip()
        if value != authorization and value:
            return value
    api_key = (headers.get("x-api-key") or "").strip()
    return api_key or None


class SupoClipApiKeyVerifier:
    """Validate MCP Bearer tokens against the configured SupoClip backend."""

    def __init__(self, api_url: str, timeout: float) -> None:
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self._cache: dict[str, tuple[float, bool]] = {}

    async def verify_token(self, token: str) -> AccessToken | None:
        raw_token = token.strip()
        if not raw_token.startswith("sk_") or is_placeholder_key(raw_token):
            return None

        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        now = time.time()
        cached = self._cache.get(token_hash)
        if cached and cached[0] > now:
            return self._access_token(raw_token) if cached[1] else None

        valid = await self._is_valid_key(raw_token)
        ttl = 60 if valid else 10
        if len(self._cache) >= _CACHE_LIMIT:
            self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
            if len(self._cache) >= _CACHE_LIMIT:
                self._cache.clear()
        self._cache[token_hash] = (now + ttl, valid)
        return self._access_token(raw_token) if valid else None

    async def _is_valid_key(self, token: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.api_url}/tasks/billing/summary",
                    headers={"Authorization": f"Bearer {token}"},
                )
        except httpx.HTTPError as exc:
            raise BackendUnavailable("SupoClip API unreachable") from exc

        if response.status_code >= 500:
            raise BackendUnavailable(f"SupoClip API answered {response.status_code}")
        return response.status_code < 400

    @staticmethod
    def _access_token(token: str) -> AccessToken:
        fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
        return AccessToken(
            token=token,
            client_id=f"supoclip-api-key-{fingerprint}",
            scopes=[REQUIRED_SCOPE],
        )


class SupoClipAuthBackend(AuthenticationBackend):
    """Resolve the request's API key to an :class:`AuthenticatedUser`.

    It never refuses a request itself; :func:`require_auth` does that, so the
    help endpoints stay public.
    """

    def __init__(self, verifier: Any) -> None:
        self.verifier = verifier

    async def authenticate(self, conn: HTTPConnection):
        token = extract_token(conn.headers)
        if not token:
            return None
        info = await self.verifier.verify_token(token)
        if not info:
            return None
        if info.expires_at and info.expires_at < int(time.time()):
            return None
        return AuthCredentials(info.scopes), AuthenticatedUser(info)


class AuthHelp:
    """Links and copy-paste config used in auth errors and the help page."""

    def __init__(self, public_url: str, app_url: str, oauth: bool = False) -> None:
        self.public_url = public_url.rstrip("/")
        self.app_url = app_url.rstrip("/")
        self.oauth = oauth

    @property
    def endpoint(self) -> str:
        return f"{self.public_url}/mcp"

    @property
    def sse_endpoint(self) -> str:
        return f"{self.public_url}/sse"

    @property
    def keys_url(self) -> str:
        return f"{self.app_url}/settings/api-keys"

    @property
    def upgrade_url(self) -> str:
        return f"{self.app_url}/settings#plan"

    docs_url = "https://github.com/FujiwaraChoki/supoclip/blob/main/mcp/README.md"

    def setup(self) -> dict[str, Any]:
        setup: dict[str, Any] = {
            "url": self.endpoint,
            "header": "Authorization: Bearer sk_...",
            "claude_code": (
                f"claude mcp add --transport http supoclip {self.endpoint} "
                '--header "Authorization: Bearer sk_..."'
            ),
            "json": {
                "mcpServers": {
                    "supoclip": {
                        "url": self.endpoint,
                        "headers": {"Authorization": "Bearer sk_..."},
                    }
                }
            },
            "legacy_sse_url": self.sse_endpoint,
            "note": (
                "Put only the URL in the URL field and the key in the Authorization "
                "header. Replace sk_... with your own key."
            ),
            "docs": self.docs_url,
        }
        if self.oauth:
            setup["connectors"] = (
                f"In claude.ai, ChatGPT or another app with custom connectors, add {self.endpoint} "
                "and sign in with your SupoClip account. No key needed."
            )
        return setup


def auth_failure(headers: Headers, help: AuthHelp) -> tuple[str, str]:
    """Return ``(error, human message)`` for a request that was not authenticated."""
    token = extract_token(headers)
    if not token:
        raw = (headers.get("authorization") or "").strip()
        if raw:
            return (
                "invalid_request",
                "The Authorization header must be 'Bearer ' followed by your SupoClip "
                f"API key, e.g. 'Authorization: Bearer sk_...'. Create a key at {help.keys_url}.",
            )
        return (
            "invalid_token",
            "Missing SupoClip API key. Create one at "
            f"{help.keys_url} and send it as 'Authorization: Bearer sk_...'.",
        )
    if is_placeholder_key(token):
        return (
            "invalid_token",
            f"'{token[:40]}' is the placeholder from the setup instructions, not a real key. "
            f"Create your own key at {help.keys_url} and put it in place of the placeholder.",
        )
    if not token.startswith("sk_"):
        return (
            "invalid_token",
            "That is not a SupoClip API key (keys start with 'sk_'). Create one at "
            f"{help.keys_url}.",
        )
    return (
        "invalid_token",
        "This SupoClip API key is invalid or was revoked. Create a new one at "
        f"{help.keys_url}.",
    )


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class require_auth:
    """Wrap an ASGI app so it only runs for an authenticated SupoClip user.

    A class rather than a closure so Starlette's ``Route`` treats it as an
    ASGI app instead of a request handler.
    """

    def __init__(self, app: ASGIApp, help: AuthHelp, resource_metadata: Optional[str] = None) -> None:
        self.app = app
        self.help = help
        # Set when OAuth is on: where connectors find the authorization server.
        self.resource_metadata = resource_metadata

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        user = scope.get("user")
        credentials = scope.get("auth")
        if isinstance(user, AuthenticatedUser) and credentials and REQUIRED_SCOPE in credentials.scopes:
            await self.app(scope, receive, send)
            return

        error, message = auth_failure(Headers(scope=scope), self.help)
        body = _json_bytes(
            {
                "error": error,
                "error_description": message,
                "create_key_url": self.help.keys_url,
                "setup": self.help.setup(),
            }
        )
        description = message.replace('"', "'")
        challenge = f'Bearer realm="supoclip", error="{error}", error_description="{description}"'
        if self.resource_metadata:
            challenge += f', resource_metadata="{self.resource_metadata}"'
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"www-authenticate", challenge.encode("utf-8")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def backend_unavailable_response(conn: HTTPConnection, exc: Exception) -> JSONResponse:
    """``on_error`` for the authentication middleware."""
    if isinstance(exc, BackendUnavailable):
        return JSONResponse(
            {
                "error": "temporarily_unavailable",
                "error_description": "Could not check your API key with SupoClip right now. Try again in a minute.",
            },
            status_code=503,
            headers={"Retry-After": "30"},
        )
    return JSONResponse({"error": "invalid_request", "error_description": str(exc)}, status_code=400)
