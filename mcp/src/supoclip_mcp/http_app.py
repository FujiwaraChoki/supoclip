"""
The hosted MCP server's HTTP app: Streamable HTTP and legacy SSE in one process.

Routes:

* ``/mcp``        Streamable HTTP, the current MCP transport.
* ``/sse``        Legacy HTTP+SSE stream (GET) plus ``/messages/`` for its posts.
                  Streamable HTTP requests sent here (POST, DELETE, or a GET that
                  carries ``Mcp-Session-Id``) go to the Streamable HTTP handler,
                  so clients configured with the old URL keep working.
* ``/``           The same routing as ``/sse``; a plain GET returns a help page.
* ``/health``     Liveness.

Both transports sit behind the same API key check (:mod:`supoclip_mcp.auth`).
"""

from __future__ import annotations

from typing import Optional

from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import StreamableHTTPASGIApp
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from .oauth import resource_metadata_url
from .auth import (
    AuthHelp,
    SupoClipAuthBackend,
    backend_unavailable_response,
    require_auth,
)

MESSAGE_PATH = "/messages/"


class _ASGIEndpoint:
    """Hand Starlette's ``Route`` an ASGI callable it won't wrap as a request handler."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.app(scope, receive, send)


def _wants_streamable_http(scope: Scope) -> bool:
    """True for requests only a Streamable HTTP client sends."""
    method = scope["method"]
    if method in ("POST", "DELETE"):
        return True
    return method == "GET" and bool(Headers(scope=scope).get("mcp-session-id"))


def _wants_event_stream(scope: Scope) -> bool:
    return "text/event-stream" in (Headers(scope=scope).get("accept") or "")


def build_http_app(
    mcp: FastMCP,
    *,
    public_url: str,
    app_url: str,
    verifier: Optional[object],
    mount_path: str = "/",
    oauth_routes: Optional[list] = None,
) -> Starlette:
    """Return one ASGI app serving both MCP transports.

    ``verifier`` validates API keys; ``None`` turns authentication off (for a
    self-hosted server that authenticates with environment credentials).
    ``oauth_routes`` (from :func:`supoclip_mcp.oauth.build_oauth_routes`) adds
    OAuth discovery, registration, authorize and token endpoints, and makes
    401s point connectors at them.
    """
    oauth = bool(oauth_routes) and verifier is not None
    help = AuthHelp(public_url, app_url, oauth=oauth)

    def metadata_for(resource_path: str) -> Optional[str]:
        return resource_metadata_url(public_url, resource_path) if oauth else None

    sse = SseServerTransport(
        MESSAGE_PATH,
        security_settings=mcp.settings.transport_security,
        max_request_body_size=mcp.settings.max_request_body_size,
    )
    low_level = mcp._mcp_server  # FastMCP exposes no public handle for a custom SSE route.

    async def legacy_sse(scope: Scope, receive: Receive, send: Send) -> None:
        async with sse.connect_sse(scope, receive, send) as (read_stream, write_stream):
            await low_level.run(
                read_stream,
                write_stream,
                low_level.create_initialization_options(),
            )

    # Creates mcp.session_manager; its lifespan runs below.
    mcp.streamable_http_app()
    streamable_app: ASGIApp = StreamableHTTPASGIApp(mcp.session_manager)
    streamable = streamable_on_sse = streamable_on_root = streamable_app
    sse_app: ASGIApp = legacy_sse
    sse_on_root: ASGIApp = legacy_sse
    messages: ASGIApp = sse.handle_post_message

    if verifier is not None:
        # One check everywhere; the 401 names the metadata for the URL the client used.
        streamable = require_auth(streamable_app, help, metadata_for("/mcp"))
        streamable_on_sse = require_auth(streamable_app, help, metadata_for("/sse"))
        streamable_on_root = require_auth(streamable_app, help, metadata_for(""))
        sse_app = require_auth(legacy_sse, help, metadata_for("/sse"))
        sse_on_root = require_auth(legacy_sse, help, metadata_for(""))
        messages = require_auth(messages, help, metadata_for("/sse"))

    def help_payload() -> dict:
        return {
            "name": "SupoClip MCP server",
            "description": "Turn long videos into short vertical clips from any MCP client.",
            "endpoints": {
                "streamable_http": help.endpoint,
                "sse": help.sse_endpoint,
            },
            "auth": (
                "Send your SupoClip API key as 'Authorization: Bearer sk_...'. "
                f"Create a key at {help.keys_url}."
                + (" Apps with custom connectors can sign in with OAuth instead." if oauth else "")
            ),
            "setup": help.setup(),
            "docs": help.docs_url,
        }

    async def method_not_allowed(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            {
                "error": "method_not_allowed",
                "error_description": f"Use POST {help.endpoint} (Streamable HTTP) or GET {help.sse_endpoint} (SSE).",
            },
            status_code=405,
            headers={"Allow": "GET, POST, DELETE"},
        )
        await response(scope, receive, send)

    async def sse_route(scope: Scope, receive: Receive, send: Send) -> None:
        if _wants_streamable_http(scope):
            await streamable_on_sse(scope, receive, send)
        elif scope["method"] == "GET":
            await sse_app(scope, receive, send)
        else:
            await method_not_allowed(scope, receive, send)

    async def root_route(scope: Scope, receive: Receive, send: Send) -> None:
        if _wants_streamable_http(scope):
            await streamable_on_root(scope, receive, send)
        elif scope["method"] == "GET" and _wants_event_stream(scope):
            await sse_on_root(scope, receive, send)
        elif scope["method"] in ("GET", "HEAD"):
            await JSONResponse(help_payload())(scope, receive, send)
        else:
            await method_not_allowed(scope, receive, send)

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def oauth_discovery(request: Request) -> JSONResponse:
        if oauth:
            return JSONResponse(
                {
                    "error": "not_found",
                    "error_description": "OAuth metadata is at /.well-known/oauth-authorization-server "
                    "and /.well-known/oauth-protected-resource.",
                },
                status_code=404,
            )
        return JSONResponse(
            {
                "error": "oauth_not_supported",
                "error_description": (
                    "This server does not support OAuth sign-in yet. Connect with a SupoClip "
                    f"API key instead: create one at {help.keys_url} and send it as "
                    "'Authorization: Bearer sk_...'."
                ),
                "setup": help.setup(),
            },
            status_code=404,
        )

    async def not_found(request: Request, exc: HTTPException) -> JSONResponse:
        if exc.status_code != 404:
            return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
        path = request.url.path
        hint = None
        stripped = path.lstrip("/")
        if " " in path or ":" in stripped or "sk_" in path:
            hint = (
                "The server URL contains extra text. Put only the URL in the URL field "
                f"({help.endpoint}) and your key in the Authorization header."
            )
        return JSONResponse(
            {
                "error": "not_found",
                "error_description": hint
                or f"No MCP endpoint here. Use {help.endpoint} (Streamable HTTP) or {help.sse_endpoint} (SSE).",
                "setup": help.setup(),
            },
            status_code=404,
        )

    routes = [
        *(oauth_routes if oauth else []),
        Route("/", endpoint=_ASGIEndpoint(root_route)),
        Route("/mcp", endpoint=_ASGIEndpoint(streamable)),
        Route("/mcp/", endpoint=_ASGIEndpoint(streamable)),
        Route("/sse", endpoint=_ASGIEndpoint(sse_route)),
        Route("/sse/", endpoint=_ASGIEndpoint(sse_route)),
        Mount(MESSAGE_PATH, app=messages),
        Route("/health", endpoint=health, methods=["GET", "HEAD"]),
        Route("/.well-known/{path:path}", endpoint=oauth_discovery),
        Route("/{prefix:path}/.well-known/{path:path}", endpoint=oauth_discovery),
    ]

    middleware = []
    if verifier is not None:
        middleware = [
            Middleware(
                AuthenticationMiddleware,
                backend=SupoClipAuthBackend(verifier),
                on_error=backend_unavailable_response,
            ),
            Middleware(AuthContextMiddleware),
        ]

    prefix = "/" + mount_path.strip("/") if mount_path.strip("/") else ""
    app_routes = [Mount(prefix, routes=routes)] if prefix else routes

    return Starlette(
        routes=app_routes,
        middleware=middleware,
        exception_handlers={404: not_found},
        lifespan=lambda app: mcp.session_manager.run(),
    )
