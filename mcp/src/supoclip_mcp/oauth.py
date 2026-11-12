"""
OAuth 2.1 authorization server for connectors (claude.ai, ChatGPT, ...).

The SDK's ``create_auth_routes`` implements the protocol (metadata, dynamic
registration, ``/authorize``, ``/token`` with PKCE S256). This module supplies
the provider behind it and keeps no state in the process or on disk: clients,
pending authorize requests and codes live in the backend (PostgreSQL and
Redis), reached through ``/internal/mcp-oauth`` with ``MCP_OAUTH_SECRET``.

* ``/authorize`` stores the request and redirects to the consent page on the
  web app, where the signed-in user allows or denies it.
* ``/token`` swaps the code for an access token. The token is a regular,
  non-expiring SupoClip API key named after the client, listed and revocable at
  /settings/api-keys, so every other part of the system authenticates it as it
  does any key. No refresh tokens are issued.
"""

from __future__ import annotations

import json
from ipaddress import ip_address
from typing import Any, Optional
from urllib.parse import urlencode, urlsplit

import httpx
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.server.auth.routes import create_auth_routes, create_protected_resource_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .auth import REQUIRED_SCOPE

INTERNAL_SECRET_HEADER = "x-supoclip-mcp-secret"
REGISTRATION_BODY_LIMIT = 64 * 1024
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def redirect_uri_allowed(uri: str) -> bool:
    """https anywhere, plain http only on a loopback host, never a fragment."""
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


class BackendUnreachable(Exception):
    pass


class OAuthBackend:
    """The backend's ``/internal/mcp-oauth`` API."""

    def __init__(self, api_url: str, secret: str, timeout: float = 15.0) -> None:
        self.base = f"{api_url.rstrip('/')}/internal/mcp-oauth"
        self.secret = secret
        self.timeout = timeout

    async def call(self, method: str, path: str, body: Optional[dict] = None) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(
                    method,
                    f"{self.base}{path}",
                    json=body,
                    headers={INTERNAL_SECRET_HEADER: self.secret},
                )
        except httpx.HTTPError as exc:
            raise BackendUnreachable(type(exc).__name__) from exc
        if response.status_code >= 500 or response.status_code in (401, 403):
            raise BackendUnreachable(f"backend answered {response.status_code}")
        return response

    @staticmethod
    def error(response: httpx.Response) -> tuple[str, str]:
        try:
            detail = response.json().get("detail")
        except (ValueError, AttributeError):
            detail = None
        if isinstance(detail, dict):
            return str(detail.get("code") or "invalid_request"), str(detail.get("message") or "")
        return "invalid_request", str(detail or response.text)


class SupoClipOAuthProvider:
    """``OAuthAuthorizationServerProvider`` backed by the SupoClip backend."""

    def __init__(self, backend: OAuthBackend, consent_url: str, verifier: Any) -> None:
        self.backend = backend
        self.consent_url = consent_url
        self.verifier = verifier

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        if not client_id or len(client_id) > 64:
            return None
        try:
            response = await self.backend.call("GET", f"/clients/{client_id}")
        except BackendUnreachable:
            return None
        if response.status_code != 200:
            return None
        return OAuthClientInformationFull.model_validate(response.json())

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        uris = [str(uri) for uri in client_info.redirect_uris or []]
        if not uris:
            raise RegistrationError("invalid_redirect_uri", "At least one redirect_uri is required.")
        for uri in uris:
            if not redirect_uri_allowed(uri):
                raise RegistrationError(
                    "invalid_redirect_uri",
                    f"{uri} is not allowed: use https, or http on localhost.",
                )
        client_info.scope = REQUIRED_SCOPE
        try:
            response = await self.backend.call(
                "POST", "/clients", client_info.model_dump(mode="json", exclude_none=True)
            )
        except BackendUnreachable as exc:
            raise RegistrationError("invalid_client_metadata", "Registration is unavailable right now.") from exc
        if response.status_code >= 400:
            code, message = self.backend.error(response)
            if code not in ("invalid_redirect_uri", "invalid_client_metadata"):
                code = "invalid_client_metadata"
            raise RegistrationError(code, message)  # type: ignore[arg-type]

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        body = {
            "client_id": client.client_id,
            "redirect_uri": str(params.redirect_uri),
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "code_challenge": params.code_challenge,
            "state": params.state,
            "scopes": [REQUIRED_SCOPE],
            "resource": params.resource,
        }
        try:
            response = await self.backend.call("POST", "/requests", body)
        except BackendUnreachable as exc:
            raise AuthorizeError("temporarily_unavailable", "Try again in a minute.") from exc
        if response.status_code != 200:
            raise AuthorizeError("invalid_request", self.backend.error(response)[1])
        request_id = response.json()["request_id"]
        return f"{self.consent_url}?{urlencode({'request': request_id})}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        if not authorization_code or len(authorization_code) > 200:
            return None
        try:
            response = await self.backend.call("POST", "/codes/peek", {"code": authorization_code})
        except BackendUnreachable:
            return None
        if response.status_code != 200:
            return None
        data = response.json()
        return AuthorizationCode(
            code=authorization_code,
            scopes=data.get("scopes") or [REQUIRED_SCOPE],
            expires_at=float(data["expires_at"]),
            client_id=data["client_id"],
            code_challenge=data["code_challenge"],
            redirect_uri=data["redirect_uri"],
            redirect_uri_provided_explicitly=bool(data.get("redirect_uri_provided_explicitly", True)),
            resource=data.get("resource"),
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        try:
            response = await self.backend.call(
                "POST",
                "/codes/exchange",
                {"code": authorization_code.code, "client_id": client.client_id},
            )
        except BackendUnreachable as exc:
            raise TokenError("invalid_grant", "Could not issue a token right now. Connect again.") from exc
        if response.status_code != 200:
            raise TokenError("invalid_grant", self.backend.error(response)[1])
        data = response.json()
        return OAuthToken(access_token=data["access_token"], token_type="Bearer", scope=REQUIRED_SCOPE)

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        return None  # access tokens never expire, so none are issued

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        raise TokenError("invalid_grant", "Refresh tokens are not issued.")

    async def load_access_token(self, token: str) -> AccessToken | None:
        return await self.verifier.verify_token(token)

    async def revoke_token(self, token: Any) -> None:
        return None  # revocation endpoint is disabled; keys are revoked in settings


def client_ip(scope: Scope) -> str:
    """The caller's address behind nginx: X-Real-IP, else the proxy-added XFF entry."""
    headers = Headers(scope=scope)
    real = (headers.get("x-real-ip") or "").strip()
    if real:
        return real
    forwarded = [part.strip() for part in (headers.get("x-forwarded-for") or "").split(",") if part.strip()]
    if forwarded:
        return forwarded[-1]
    client = scope.get("client")
    return client[0] if client else "unknown"


class RateLimited:
    """Count POSTs per caller in the backend's Redis; answer 429 past the limit."""

    def __init__(self, app: ASGIApp, backend: OAuthBackend, bucket: str) -> None:
        self.app = app
        self.backend = backend
        self.bucket = bucket

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] == "POST":
            allowed, retry_after = True, 0
            try:
                response = await self.backend.call(
                    "POST", "/rate-limit", {"bucket": self.bucket, "caller": client_ip(scope)}
                )
                if response.status_code == 200:
                    data = response.json()
                    allowed, retry_after = bool(data.get("allowed", True)), int(data.get("retry_after") or 0)
            except BackendUnreachable:
                pass  # the flow itself needs the backend and will fail clearly
            if not allowed:
                await JSONResponse(
                    {"error": "slow_down", "error_description": "Too many requests. Try again later."},
                    status_code=429,
                    headers={"Retry-After": str(max(retry_after, 1))},
                )(scope, receive, send)
                return
        await self.app(scope, receive, send)


class RegistrationCompat:
    """Accept clients that register only the ``authorization_code`` grant.

    The SDK's registration handler insists on ``refresh_token`` too. We never
    issue refresh tokens, so adding it to the registered grant types is harmless
    and lets those clients register.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > REGISTRATION_BODY_LIMIT:
                await JSONResponse(
                    {"error": "invalid_client_metadata", "error_description": "Registration body is too large."},
                    status_code=413,
                )(scope, receive, send)
                return
            chunks.append(chunk)
            if not message.get("more_body"):
                break
        body = b"".join(chunks)
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            data = None
        if isinstance(data, dict):
            grants = data.get("grant_types")
            if isinstance(grants, list) and "authorization_code" in grants and "refresh_token" not in grants:
                data["grant_types"] = [*grants, "refresh_token"]
                body = json.dumps(data).encode("utf-8")

        sent = False

        async def replay() -> Message:
            nonlocal sent
            if sent:
                return await receive()
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        headers = [(k, v) for k, v in scope["headers"] if k.lower() != b"content-length"]
        headers.append((b"content-length", str(len(body)).encode()))
        await self.app({**scope, "headers": headers}, replay, send)


class _Endpoint:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.app(scope, receive, send)


RESOURCE_PATHS = ("", "/mcp", "/sse")


def resource_metadata_url(public_url: str, resource_path: str) -> str:
    return f"{public_url.rstrip('/')}/.well-known/oauth-protected-resource{resource_path}"


def build_oauth_routes(
    *,
    public_url: str,
    app_url: str,
    api_url: str,
    secret: str,
    verifier: Any,
    docs_url: Optional[str] = None,
) -> list[Route]:
    """Discovery, registration, authorize and token routes for the MCP app."""
    base = public_url.rstrip("/")
    backend = OAuthBackend(api_url, secret)
    provider = SupoClipOAuthProvider(backend, f"{app_url.rstrip('/')}/oauth/consent", verifier)
    routes = create_auth_routes(
        provider,
        issuer_url=AnyHttpUrl(base),
        service_documentation_url=AnyHttpUrl(docs_url) if docs_url else None,
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=[REQUIRED_SCOPE],
            default_scopes=[REQUIRED_SCOPE],
        ),
        revocation_options=RevocationOptions(enabled=False),
    )

    wrapped: list[Route] = []
    for route in routes:
        endpoint = route.endpoint
        if route.path == "/register":
            endpoint = RateLimited(RegistrationCompat(endpoint), backend, "register")
        elif route.path == "/token":
            endpoint = RateLimited(endpoint, backend, "token")
        wrapped.append(Route(route.path, endpoint=_Endpoint(endpoint), methods=route.methods))
        if route.path == "/.well-known/oauth-authorization-server":
            # Some clients append the resource path (RFC 8414 style).
            wrapped.append(
                Route(
                    "/.well-known/oauth-authorization-server/{path:path}",
                    endpoint=_Endpoint(endpoint),
                    methods=route.methods,
                )
            )

    for resource_path in RESOURCE_PATHS:
        wrapped.extend(
            create_protected_resource_routes(
                resource_url=AnyHttpUrl(f"{base}{resource_path}"),
                authorization_servers=[AnyHttpUrl(base)],
                scopes_supported=[REQUIRED_SCOPE],
                resource_name="SupoClip",
                resource_documentation=AnyHttpUrl(docs_url) if docs_url else None,
            )
        )
    return wrapped
