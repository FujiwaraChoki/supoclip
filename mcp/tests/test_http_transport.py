"""Routing and authentication for the hosted HTTP app (Streamable HTTP + SSE)."""

import asyncio
from dataclasses import replace
from pathlib import Path
import socket
import sys
import threading
import time

import httpx
import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from supoclip_mcp import auth, config, server

VALID = "sk_transport_test_key"
BASE = "http://127.0.0.1:9100"
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "transport-test", "version": "1"},
    },
}
STREAMABLE_HEADERS = {"Accept": "application/json, text/event-stream"}


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setattr(server, "SETTINGS", replace(
        server.SETTINGS,
        mcp_host="127.0.0.1",
        mcp_port=9100,
        mcp_require_bearer_auth=True,
        mcp_public_url=BASE,
        mcp_mount_path="/",
        app_url="https://www.supoclip.test",
    ))
    calls = []

    async def is_valid(self, token):
        calls.append(token)
        if token == "sk_backend_down":
            raise auth.BackendUnavailable("down")
        return token == VALID

    monkeypatch.setattr(auth.SupoClipApiKeyVerifier, "_is_valid_key", is_valid)
    return calls


@pytest.fixture
def client(settings):
    app = server.build_app(server._build_mcp())
    with TestClient(app, base_url=BASE) as test_client:
        yield test_client


def bearer(token):
    return {**STREAMABLE_HEADERS, "Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("path", ["/mcp", "/mcp/", "/sse", "/"])
def test_streamable_http_initialize_works_on_every_mcp_path(client, path):
    response = client.post(path, json=INIT, headers=bearer(VALID))
    assert response.status_code == 200
    assert "serverInfo" in response.text


@pytest.mark.parametrize(
    ("method", "path", "headers"),
    [
        ("POST", "/mcp", STREAMABLE_HEADERS),
        ("POST", "/sse", STREAMABLE_HEADERS),
        ("POST", "/", STREAMABLE_HEADERS),
        ("GET", "/sse", {"Accept": "text/event-stream"}),
        ("GET", "/", {"Accept": "text/event-stream"}),
        ("POST", "/messages/?session_id=00000000000000000000000000000000", {}),
    ],
)
def test_every_transport_refuses_a_missing_key_the_same_way(client, method, path, headers):
    response = client.request(method, path, json=INIT, headers=headers)
    assert response.status_code == 401
    body = response.json()
    assert body["error"] == "invalid_token"
    assert "https://www.supoclip.test/settings/api-keys" in body["error_description"]
    assert body["setup"]["url"] == f"{BASE}/mcp"
    assert "claude mcp add --transport http supoclip" in body["setup"]["claude_code"]
    assert response.headers["www-authenticate"].startswith("Bearer ")


@pytest.mark.parametrize("path", ["/mcp", "/sse"])
def test_placeholder_key_is_called_out(client, settings, path):
    method = "POST" if path == "/mcp" else "GET"
    response = client.request(method, path, json=INIT, headers=bearer("sk_your_supoclip_api_key"))
    assert response.status_code == 401
    assert "placeholder" in response.json()["error_description"]
    assert settings == []  # never sent to the backend


@pytest.mark.parametrize(
    ("token", "expected"),
    [("invalid-format", "not a SupoClip API key"), ("sk_rejected_key", "invalid or was revoked")],
)
def test_bad_keys_are_explained(client, token, expected):
    response = client.post("/mcp", json=INIT, headers=bearer(token))
    assert response.status_code == 401
    assert expected in response.json()["error_description"]


def test_authorization_without_bearer_prefix_is_explained(client):
    response = client.post("/mcp", json=INIT, headers={**STREAMABLE_HEADERS, "Authorization": VALID})
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_request"
    assert "Bearer" in response.json()["error_description"]


@pytest.mark.parametrize(
    "headers",
    [{"Authorization": f"Bearer Bearer {VALID}"}, {"x-api-key": VALID}, {"authorization": f"bearer {VALID}"}],
)
def test_accepted_key_spellings(client, headers):
    response = client.post("/mcp", json=INIT, headers={**STREAMABLE_HEADERS, **headers})
    assert response.status_code == 200


def test_backend_outage_is_not_reported_as_a_bad_key(client):
    response = client.post("/mcp", json=INIT, headers=bearer("sk_backend_down"))
    assert response.status_code == 503
    assert response.json()["error"] == "temporarily_unavailable"


def test_root_get_is_a_help_page(client):
    response = client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["endpoints"] == {"streamable_http": f"{BASE}/mcp", "sse": f"{BASE}/sse"}
    assert "settings/api-keys" in body["auth"]
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize(
    "path",
    [
        "/sse%20Transport%3ASSE",
        "/sse%20Transport%3A%20SSE%20Authentication%3A%20Bearer%20Token%20Token%3A%20sk_your_supoclip_api_key",
    ],
)
def test_pasted_doc_text_in_the_url_gets_a_hint(client, path):
    response = client.post(path, json=INIT)
    assert response.status_code == 404
    assert "extra text" in response.json()["error_description"]


def test_unknown_path_points_at_the_endpoints(client):
    response = client.get("/nope")
    assert response.status_code == 404
    assert f"{BASE}/mcp" in response.json()["error_description"]


@pytest.mark.parametrize(
    "path",
    [
        "/.well-known/oauth-authorization-server",
        "/.well-known/openid-configuration/sse",
        "/sse/.well-known/openid-configuration",
    ],
)
def test_oauth_discovery_explains_api_keys(client, path):
    response = client.get(path)
    assert response.status_code == 404
    assert response.json()["error"] == "oauth_not_supported"


def test_put_on_sse_is_method_not_allowed(client):
    response = client.put("/sse", headers=bearer(VALID))
    assert response.status_code == 405
    assert response.json()["error"] == "method_not_allowed"


def test_mount_path_prefixes_every_route(settings, monkeypatch):
    monkeypatch.setattr(server, "SETTINGS", replace(server.SETTINGS, mcp_mount_path="/supoclip/"))
    with TestClient(server.build_app(server._build_mcp()), base_url=BASE) as prefixed:
        assert prefixed.post("/supoclip/mcp", json=INIT, headers=bearer(VALID)).status_code == 200
        assert prefixed.post("/supoclip/sse", json=INIT, headers=bearer(VALID)).status_code == 200
        assert prefixed.get("/supoclip/health").status_code == 200
        assert prefixed.post("/mcp", json=INIT, headers=bearer(VALID)).status_code == 404


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("sk_your_supoclip_api_key", True),
        ("sk_your_key_here", True),
        ("sk_xxxxxxxxxxxxxxxxxxxx", True),
        ("sk_...", True),
        ("sk_live_abc123", False),
        ("sk_1a2b3c", False),
    ],
)
def test_placeholder_detection(token, expected):
    assert auth.is_placeholder_key(token) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http", "http"), ("sse", "http"), ("streamable-http", "http"),
        ("both", "http"), ("stdio", "stdio"), ("", "stdio"), ("bogus", "stdio"),
    ],
)
def test_transport_values(monkeypatch, value, expected):
    monkeypatch.setenv("SUPOCLIP_MCP_TRANSPORT", value)
    assert config.load_settings().mcp_transport == expected


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_real_clients_list_tools_over_both_transports(settings, monkeypatch):
    """Start the server for real and connect with the MCP SDK's own clients."""
    import uvicorn
    from mcp import ClientSession
    from mcp.client.sse import sse_client
    from mcp.client.streamable_http import streamable_http_client

    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    monkeypatch.setattr(server, "SETTINGS", replace(server.SETTINGS, mcp_public_url=base))
    mcp_server = server.mcp
    uv = uvicorn.Server(uvicorn.Config(server.build_app(mcp_server), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not uv.started and time.time() < deadline:
        time.sleep(0.05)
    assert uv.started

    headers = {"Authorization": f"Bearer {VALID}"}

    async def list_over(transport):
        async with transport as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                return {tool.name for tool in (await session.list_tools()).tools}

    async def run():
        async with httpx.AsyncClient(headers=headers) as http:
            streamable = await list_over(streamable_http_client(f"{base}/mcp", http_client=http))
            # A Streamable HTTP client pointed at the old /sse URL.
            redirected = await list_over(streamable_http_client(f"{base}/sse", http_client=http))
        legacy = await list_over(sse_client(f"{base}/sse", headers=headers))
        return streamable, legacy, redirected

    try:
        streamable, legacy, redirected = asyncio.run(run())
        refused = httpx.get(f"{base}/sse", headers={"Accept": "text/event-stream"})
    finally:
        uv.should_exit = True
        thread.join(timeout=10)

    assert "supoclip_create_clip_task" in streamable
    assert streamable == legacy == redirected
    assert refused.status_code == 401
