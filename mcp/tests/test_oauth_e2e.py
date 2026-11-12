"""End-to-end OAuth against a running backend and MCP server.

Skipped unless these are set (the backend and MCP server share MCP_OAUTH_SECRET,
the user exists with a verified email):

    SUPOCLIP_E2E_MCP_URL=http://127.0.0.1:19100
    SUPOCLIP_E2E_API_URL=http://127.0.0.1:18000
    SUPOCLIP_E2E_BACKEND_AUTH_SECRET=...   # backend BACKEND_AUTH_SECRET
    SUPOCLIP_E2E_USER_ID=...

The consent step calls the backend the way the web app's /api/oauth proxy does
(signed session headers), standing in for the browser.
"""

import asyncio
import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

MCP = os.getenv("SUPOCLIP_E2E_MCP_URL", "").rstrip("/")
API = os.getenv("SUPOCLIP_E2E_API_URL", "").rstrip("/")
AUTH_SECRET = os.getenv("SUPOCLIP_E2E_BACKEND_AUTH_SECRET", "")
USER_ID = os.getenv("SUPOCLIP_E2E_USER_ID", "")
REDIRECT = "http://127.0.0.1:33418/callback"

pytestmark = pytest.mark.skipif(
    not (MCP and API and AUTH_SECRET and USER_ID), reason="set SUPOCLIP_E2E_* to run against live services"
)


def signed():
    ts = str(int(time.time()))
    signature = hmac.new(AUTH_SECRET.encode(), f"{USER_ID}:{ts}".encode(), hashlib.sha256).hexdigest()
    return {"x-supoclip-user-id": USER_ID, "x-supoclip-ts": ts, "x-supoclip-signature": signature}


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def start(http, client, challenge, state):
    response = http.get(f"{MCP}/authorize", params={
        "response_type": "code", "client_id": client["client_id"], "redirect_uri": REDIRECT,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
        "scope": "supoclip", "resource": f"{MCP}/mcp",
    })
    assert response.status_code == 302, response.text
    location = urlsplit(response.headers["location"])
    assert location.path == "/oauth/consent"
    return parse_qs(location.query)["request"][0]


def decide(http, request_id, approve):
    response = http.post(f"{API}/oauth/requests/{request_id}/decision", headers=signed(), json={"approve": approve})
    assert response.status_code == 200, response.text
    return parse_qs(urlsplit(response.json()["redirect_url"]).query)


def exchange(http, client, code, verifier, redirect=REDIRECT):
    return http.post(f"{MCP}/token", data={
        "grant_type": "authorization_code", "code": code, "code_verifier": verifier, "redirect_uri": redirect,
        "client_id": client["client_id"], "client_secret": client.get("client_secret") or "",
    })


def test_register_authorize_consent_token_and_call_a_tool():
    http = httpx.Client(timeout=20, follow_redirects=False)

    # Discovery, starting from an unauthenticated call like a connector does.
    challenge_header = http.post(f"{MCP}/mcp", json={}, headers={"Accept": "application/json, text/event-stream"}).headers["www-authenticate"]
    metadata_url = re.search(r'resource_metadata="([^"]+)"', challenge_header).group(1)
    resource = http.get(metadata_url).json()
    assert resource["resource"] == f"{MCP}/mcp"
    server_metadata = http.get(f"{resource['authorization_servers'][0].rstrip('/')}/.well-known/oauth-authorization-server").json()

    client = http.post(server_metadata["registration_endpoint"], json={
        "client_name": "E2E Claude", "redirect_uris": [REDIRECT],
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_post",
    }).json()
    assert client["client_id"] and client["client_secret"]

    # A user who denies sends the connector an access_denied.
    _, challenge = pkce()
    denied = decide(http, start(http, client, challenge, "deny-state"), approve=False)
    assert denied["error"] == ["access_denied"] and denied["state"] == ["deny-state"]

    verifier, challenge = pkce()
    request_id = start(http, client, challenge, "st-1")
    described = http.get(f"{API}/oauth/requests/{request_id}", headers=signed()).json()
    assert described["client_name"] == "E2E Claude" and described["redirect_host"] == "127.0.0.1"
    approved = decide(http, request_id, approve=True)
    assert approved["state"] == ["st-1"]
    code = approved["code"][0]

    bad_pkce = exchange(http, client, code, secrets.token_urlsafe(48))
    assert bad_pkce.status_code == 400 and bad_pkce.json()["error"] == "invalid_grant"
    wrong_redirect = exchange(http, client, code, verifier, redirect="http://127.0.0.1:33418/other")
    assert wrong_redirect.status_code == 400

    issued = exchange(http, client, code, verifier)
    assert issued.status_code == 200, issued.text
    token = issued.json()
    assert token["token_type"] == "Bearer" and token["access_token"].startswith("sk_")
    reused = exchange(http, client, code, verifier)
    assert reused.status_code == 400 and reused.json()["error"] == "invalid_grant"

    keys = http.get(f"{API}/api-keys/", headers=signed()).json()["api_keys"]
    key = next(item for item in keys if item["name"] == "E2E Claude connector" and not item["revoked"])

    async def use_token():
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token['access_token']}"}) as authed:
            async with streamable_http_client(f"{MCP}/mcp", http_client=authed) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    tools = {tool.name for tool in (await session.list_tools()).tools}
                    before = (await session.call_tool("supoclip_billing_summary", {})).content[0].text
                    # Revoking the key in settings disconnects the connector.
                    assert http.delete(f"{API}/api-keys/{key['id']}", headers=signed()).status_code == 200
                    after = (await session.call_tool("supoclip_billing_summary", {})).content[0].text
                    return tools, before, after

    tools, before, after = asyncio.run(use_token())
    assert "supoclip_create_clip_task" in tools
    assert '"plan"' in before
    assert after.startswith("Error: Authentication failed (401)")
