"""The agent's hands: SupoClip's own HTTP API, called in-process as the user.

Every action the agent takes goes through the same routes the web app and the
MCP server use, signed exactly like the frontend signs them. Plan limits,
billing, feature flags, team scopes and validation all apply unchanged; the
agent cannot do anything the person couldn't do themselves.

Requests never leave the process (``httpx.ASGITransport``), so no URL or
network configuration is needed, and the app's startup state is already there.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, Optional

import httpx

from ..auth_headers import SIGNATURE_HEADER, TEAM_ID_HEADER, TIMESTAMP_HEADER, USER_ID_HEADER
from ..config import get_config

INTERNAL_BASE_URL = "http://supoclip-agent.internal"
REQUEST_TIMEOUT_SECONDS = 120.0


class AgentAPIError(Exception):
    """A route said no. ``detail`` is what the route returned, for the model to read."""

    def __init__(self, status: int, detail: Any):
        self.status = status
        self.detail = detail
        super().__init__(f"HTTP {status}: {_detail_text(detail)}")


def _detail_text(detail: Any) -> str:
    if isinstance(detail, dict):
        message = detail.get("message") or detail.get("detail") or detail.get("error")
        code = detail.get("code")
        if message and code:
            return f"{message} ({code})"
        if message:
            return str(message)
        return json.dumps(detail)[:600]
    return str(detail)[:600]


def signed_headers(user_id: str, team_id: Optional[str]) -> dict[str, str]:
    """The headers the frontend's ``fetchBackend`` sends for this person and workspace."""
    config = get_config()
    headers = {USER_ID_HEADER: user_id, "x-supoclip-client": "web", "x-supoclip-source": "agent"}
    if team_id:
        headers[TEAM_ID_HEADER] = team_id
    if config.backend_auth_secret:
        timestamp = str(int(time.time()))
        payload = f"{user_id}:{team_id}:{timestamp}" if team_id else f"{user_id}:{timestamp}"
        headers[TIMESTAMP_HEADER] = timestamp
        headers[SIGNATURE_HEADER] = hmac.new(
            config.backend_auth_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()
    return headers


class AgentAPI:
    """One person's view of the API, in one workspace."""

    def __init__(self, app, user_id: str, team_id: Optional[str] = None, trace_id: Optional[str] = None):
        self.app = app
        self.user_id = user_id
        self.team_id = team_id
        self.trace_id = trace_id

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
    ) -> Any:
        # Signed per request: a long turn outlives the signature's lifetime.
        sent = signed_headers(self.user_id, self.team_id)
        if self.trace_id:
            sent["x-trace-id"] = self.trace_id
        if headers:
            sent.update(headers)
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(
            transport=transport, base_url=INTERNAL_BASE_URL, timeout=timeout
        ) as client:
            response = await client.request(
                method, path, json=json_body, params=clean_params or None, headers=sent
            )
        if response.status_code >= 400:
            try:
                body = response.json()
                detail = body.get("detail", body) if isinstance(body, dict) else body
            except ValueError:
                detail = response.text[:600] or response.reason_phrase
            raise AgentAPIError(response.status_code, detail)
        if response.status_code == 204 or not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {"raw": response.text[:2000]}

    async def get(self, path: str, **params: Any) -> Any:
        return await self.request("GET", path, params=params)

    async def post(self, path: str, body: Any = None, **kwargs: Any) -> Any:
        return await self.request("POST", path, json_body=body if body is not None else {}, **kwargs)

    async def patch(self, path: str, body: Any) -> Any:
        return await self.request("PATCH", path, json_body=body)

    async def delete(self, path: str) -> Any:
        return await self.request("DELETE", path)
