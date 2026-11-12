"""
OAuth for the hosted MCP server.

Two groups of routes:

* ``/internal/mcp-oauth/*``: called only by the MCP server, authenticated with
  the shared ``MCP_OAUTH_SECRET`` (``x-supoclip-mcp-secret``). The MCP server is
  the authorization server connectors talk to; these routes hold its state.
* ``/oauth/requests/*``: the consent page on the web app, authenticated with the
  frontend's signed session headers (never an API key, so a leaked key cannot
  approve new connections).
"""

from __future__ import annotations

import hmac
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import get_authenticated_user_id
from ...config import get_config
from ...database import get_db
from ...services.mcp_oauth_service import McpOAuthError, McpOAuthService
from .api_keys import notify_api_key_created

logger = logging.getLogger(__name__)

INTERNAL_SECRET_HEADER = "x-supoclip-mcp-secret"

internal_router = APIRouter(prefix="/internal/mcp-oauth", tags=["mcp-oauth"], include_in_schema=False)
consent_router = APIRouter(prefix="/oauth", tags=["mcp-oauth"])


async def get_oauth_redis() -> AsyncIterator[Any]:
    config = get_config()
    client = redis.Redis(
        host=config.redis_host,
        port=config.redis_port,
        password=config.redis_password,
        decode_responses=True,
    )
    try:
        yield client
    finally:
        await client.aclose()


def get_service(
    db: AsyncSession = Depends(get_db), redis_client: Any = Depends(get_oauth_redis)
) -> McpOAuthService:
    return McpOAuthService(db, redis_client)


def require_mcp_server(request: Request) -> None:
    secret = get_config().mcp_oauth_secret
    if not secret:
        raise HTTPException(status_code=503, detail="MCP OAuth is not configured")
    provided = request.headers.get(INTERNAL_SECRET_HEADER) or ""
    if not hmac.compare_digest(provided.encode("utf-8"), secret.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid MCP server credentials")


def _error(exc: McpOAuthError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={"detail": {"code": exc.code, "message": exc.message}},
    )


# --------------------------------------------------------------------------- #
# Internal: the MCP server
# --------------------------------------------------------------------------- #
class AuthorizeRequestBody(BaseModel):
    client_id: str = Field(min_length=1, max_length=64)
    redirect_uri: str = Field(min_length=1, max_length=2000)
    redirect_uri_provided_explicitly: bool = True
    code_challenge: str = Field(min_length=43, max_length=128)
    state: Optional[str] = Field(default=None, max_length=2000)
    scopes: Optional[List[str]] = None
    resource: Optional[str] = Field(default=None, max_length=2000)


class CodeBody(BaseModel):
    code: str = Field(min_length=1, max_length=200)


class ExchangeBody(CodeBody):
    client_id: str = Field(min_length=1, max_length=64)


class RateLimitBody(BaseModel):
    bucket: str
    caller: str = Field(min_length=1, max_length=200)


@internal_router.post("/clients", dependencies=[Depends(require_mcp_server)], status_code=201)
async def register_client(body: Dict[str, Any], service: McpOAuthService = Depends(get_service)):
    try:
        await service.register_client(body)
    except McpOAuthError as exc:
        return _error(exc)
    return {"client_id": body.get("client_id")}


@internal_router.get("/clients/{client_id}", dependencies=[Depends(require_mcp_server)])
async def get_client(client_id: str, service: McpOAuthService = Depends(get_service)):
    client = await service.get_client(client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Unknown client")
    return client


@internal_router.post("/requests", dependencies=[Depends(require_mcp_server)])
async def create_request(body: AuthorizeRequestBody, service: McpOAuthService = Depends(get_service)):
    try:
        request_id = await service.create_request(body.model_dump())
    except McpOAuthError as exc:
        return _error(exc)
    return {"request_id": request_id}


@internal_router.post("/codes/peek", dependencies=[Depends(require_mcp_server)])
async def peek_code(body: CodeBody, service: McpOAuthService = Depends(get_service)):
    code = await service.peek_code(body.code)
    if code is None:
        raise HTTPException(status_code=404, detail="Unknown code")
    return code


@internal_router.post("/codes/exchange", dependencies=[Depends(require_mcp_server)])
async def exchange_code(body: ExchangeBody, service: McpOAuthService = Depends(get_service)):
    try:
        user, created = await service.exchange_code(body.code, body.client_id)
    except McpOAuthError as exc:
        return _error(exc)
    await notify_api_key_created(user, created)
    logger.info("MCP OAuth issued API key %s to client %s for user %s", created.get("id"), body.client_id, user.id)
    return {"access_token": created["key"], "scope": "supoclip", "key_id": created.get("id")}


@internal_router.post("/rate-limit", dependencies=[Depends(require_mcp_server)])
async def rate_limit(body: RateLimitBody, service: McpOAuthService = Depends(get_service)):
    if body.bucket not in {"register", "token"}:
        raise HTTPException(status_code=400, detail="Unknown bucket")
    try:
        allowed, retry_after = await service.hit_rate_limit(body.bucket, body.caller)
    except Exception as exc:  # Redis down: fail open, the flow itself needs Redis anyway
        logger.warning("MCP OAuth rate limit check failed: %s", exc)
        return {"allowed": True, "retry_after": 0}
    return {"allowed": allowed, "retry_after": retry_after}


# --------------------------------------------------------------------------- #
# Consent: the web app, for the signed-in user
# --------------------------------------------------------------------------- #
class DecisionBody(BaseModel):
    approve: bool


@consent_router.get("/requests/{request_id}")
async def describe_request(
    request_id: str, request: Request, service: McpOAuthService = Depends(get_service)
):
    user_id = get_authenticated_user_id(request, get_config())
    try:
        return await service.describe_request(request_id, user_id)
    except McpOAuthError as exc:
        return _error(exc)


@consent_router.post("/requests/{request_id}/decision")
async def decide(
    request_id: str,
    body: DecisionBody,
    request: Request,
    service: McpOAuthService = Depends(get_service),
):
    user_id = get_authenticated_user_id(request, get_config())
    try:
        redirect_url = await service.decide(request_id, user_id, body.approve)
    except McpOAuthError as exc:
        return _error(exc)
    return {"redirect_url": redirect_url}
