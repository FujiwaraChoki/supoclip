"""OAuth for connectors: discovery, registration, consent hand-off and token exchange.

The backend's /internal/mcp-oauth API is replaced by a small in-memory fake
with the same contract (single-use requests and codes, keys as tokens).
"""

import base64
from dataclasses import replace
import hashlib
from pathlib import Path
import secrets
import sys
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from supoclip_mcp import server

BASE = "http://127.0.0.1:9100"
API = "http://backend.test"
APP = "https://www.supoclip.test"
SECRET = "test-oauth-secret"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"
STREAMABLE = {"Accept": "application/json, text/event-stream"}
INIT = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "oauth-test", "version": "1"}},
}


class FakeBackend:
    """In-memory /internal/mcp-oauth plus API key validation."""

    def __init__(self):
        self.clients = {}
        self.requests = {}
        self.codes = {}
        self.keys = set()
        self.calls = []
        self.rate = {}
        self.limit = 1000

    def approve(self, request_id, user="user-1"):
        request = self.requests.pop(request_id)
        code = secrets.token_urlsafe(32)
        self.codes[code] = {**request, "user_id": user, "expires_at": time.time() + 600}
        return code, request.get("state")

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/tasks/billing/summary":
            token = request.headers.get("authorization", "")[7:]
            return httpx.Response(200 if token in self.keys else 401, json={})
        assert path.startswith("/internal/mcp-oauth/")
        if request.headers.get("x-supoclip-mcp-secret") != SECRET:
            return httpx.Response(401, json={"detail": "bad secret"})
        sub = path.removeprefix("/internal/mcp-oauth")
        body = httpx.Response(200, content=request.content).json() if request.content else {}
        self.calls.append(sub)
        if sub == "/clients" and request.method == "POST":
            self.clients[body["client_id"]] = body
            return httpx.Response(201, json={"client_id": body["client_id"]})
        if sub.startswith("/clients/"):
            client = self.clients.get(sub.split("/")[-1])
            return httpx.Response(200, json=client) if client else httpx.Response(404, json={"detail": "nope"})
        if sub == "/requests":
            request_id = secrets.token_urlsafe(8)
            self.requests[request_id] = body
            return httpx.Response(200, json={"request_id": request_id})
        if sub == "/codes/peek":
            code = self.codes.get(body["code"])
            if not code:
                return httpx.Response(404, json={"detail": "nope"})
            return httpx.Response(200, json={k: v for k, v in code.items() if k != "user_id"})
        if sub == "/codes/exchange":
            code = self.codes.pop(body["code"], None)
            if not code or code["client_id"] != body["client_id"]:
                return httpx.Response(400, json={"detail": {"code": "invalid_grant", "message": "invalid or used"}})
            key = "sk_oauth_" + secrets.token_urlsafe(16)
            self.keys.add(key)
            return httpx.Response(200, json={"access_token": key, "scope": "supoclip"})
        if sub == "/rate-limit":
            count = self.rate[body["bucket"]] = self.rate.get(body["bucket"], 0) + 1
            return httpx.Response(200, json={"allowed": count <= self.limit, "retry_after": 30})
        return httpx.Response(404, json={"detail": "unknown"})


@pytest.fixture
def backend(monkeypatch):
    fake = FakeBackend()
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(fake.handle), **kw))
    monkeypatch.setattr(server, "SETTINGS", replace(
        server.SETTINGS,
        api_url=API, app_url=APP, mcp_host="127.0.0.1", mcp_port=9100, mcp_public_url=BASE,
        mcp_require_bearer_auth=True, mcp_mount_path="/", mcp_oauth_secret=SECRET,
    ))
    return fake


def fresh_app():
    mcp = server._build_mcp()

    @mcp.tool()
    def supoclip_ping() -> str:
        """Test tool."""
        return "pong"

    return server.build_app(mcp)


@pytest.fixture
def client(backend):
    with TestClient(fresh_app(), base_url=BASE, follow_redirects=False) as test_client:
        yield test_client


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def register(client, **overrides):
    body = {"redirect_uris": [REDIRECT], "client_name": "Claude", "token_endpoint_auth_method": "none", **overrides}
    return client.post("/register", json=body)


def authorize(client, client_id, challenge, redirect=REDIRECT, **extra):
    params = {
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": "xyz", **extra,
    }
    return client.get("/authorize", params=params)


def token(client, client_id, code, verifier, redirect=REDIRECT):
    return client.post("/token", data={
        "grant_type": "authorization_code", "client_id": client_id, "code": code,
        "code_verifier": verifier, "redirect_uri": redirect,
    })


def connect(client, backend):
    """Register, authorize, approve; return (client_id, code, verifier)."""
    client_id = register(client).json()["client_id"]
    verifier, challenge = pkce()
    response = authorize(client, client_id, challenge)
    request_id = parse_qs(urlsplit(response.headers["location"]).query)["request"][0]
    code, _ = backend.approve(request_id)
    return client_id, code, verifier


def test_discovery_documents(client):
    metadata = client.get("/.well-known/oauth-authorization-server").json()
    assert metadata["issuer"].rstrip("/") == BASE
    assert metadata["authorization_endpoint"] == f"{BASE}/authorize"
    assert metadata["token_endpoint"] == f"{BASE}/token"
    assert metadata["registration_endpoint"] == f"{BASE}/register"
    assert metadata["code_challenge_methods_supported"] == ["S256"]
    assert metadata["scopes_supported"] == ["supoclip"]
    assert "revocation_endpoint" not in metadata
    assert client.get("/.well-known/oauth-authorization-server/mcp").json() == metadata

    for suffix, resource in (("", f"{BASE}/"), ("/mcp", f"{BASE}/mcp"), ("/sse", f"{BASE}/sse")):
        document = client.get(f"/.well-known/oauth-protected-resource{suffix}").json()
        assert document["resource"] == resource
        assert [url.rstrip("/") for url in document["authorization_servers"]] == [BASE]
        assert document["scopes_supported"] == ["supoclip"]


@pytest.mark.parametrize(
    ("method", "path", "suffix"),
    [("POST", "/mcp", "/mcp"), ("POST", "/sse", "/sse"), ("GET", "/sse", "/sse"), ("POST", "/", "")],
)
def test_401_points_at_resource_metadata(client, method, path, suffix):
    response = client.request(method, path, json=INIT, headers=STREAMABLE)
    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    assert f'resource_metadata="{BASE}/.well-known/oauth-protected-resource{suffix}"' in challenge
    assert "connectors" in response.json()["setup"]


def test_plain_api_keys_still_work(client, backend):
    backend.keys.add("sk_plain_key")
    response = client.post("/mcp", json=INIT, headers={**STREAMABLE, "Authorization": "Bearer sk_plain_key"})
    assert response.status_code == 200


@pytest.mark.parametrize(
    "uri",
    ["https://claude.ai/api/mcp/auth_callback", "http://localhost:33418/callback", "http://127.0.0.1:5000/cb", "http://[::1]:8080/cb"],
)
def test_registration_accepts_https_and_loopback(client, backend, uri):
    response = register(client, redirect_uris=[uri])
    assert response.status_code == 201, response.text
    stored = backend.clients[response.json()["client_id"]]
    assert stored["scope"] == "supoclip"


@pytest.mark.parametrize("uri", ["http://evil.example/cb", "ftp://claude.ai/cb", "https://claude.ai/cb#frag", "javascript:alert(1)"])
def test_registration_rejects_other_redirects(client, backend, uri):
    response = register(client, redirect_uris=[uri])
    assert response.status_code == 400
    assert response.json()["error"] in ("invalid_redirect_uri", "invalid_client_metadata")
    assert backend.clients == {}


def test_registration_without_refresh_grant_is_accepted(client):
    response = register(client, grant_types=["authorization_code"])
    assert response.status_code == 201, response.text


def test_authorize_redirects_to_consent_and_stores_the_request(client, backend):
    client_id = register(client).json()["client_id"]
    _, challenge = pkce()
    response = authorize(client, client_id, challenge)
    assert response.status_code == 302
    location = urlsplit(response.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == f"{APP}/oauth/consent"
    request_id = parse_qs(location.query)["request"][0]
    stored = backend.requests[request_id]
    assert stored["client_id"] == client_id
    assert stored["redirect_uri"] == REDIRECT
    assert stored["code_challenge"] == challenge
    assert stored["state"] == "xyz"


def test_plain_pkce_is_rejected(client, backend):
    client_id = register(client).json()["client_id"]
    response = authorize(client, client_id, "a" * 43, code_challenge_method="plain")
    assert response.status_code in (302, 400)
    if response.status_code == 302:
        assert "error=invalid_request" in response.headers["location"]
    assert backend.requests == {}


def test_unregistered_redirect_is_rejected_at_authorize(client, backend):
    client_id = register(client).json()["client_id"]
    _, challenge = pkce()
    response = authorize(client, client_id, challenge, redirect="https://claude.ai/other")
    assert response.status_code == 400
    assert backend.requests == {}


def test_full_flow_issues_a_key_that_calls_tools(client, backend):
    client_id, code, verifier = connect(client, backend)
    response = token(client, client_id, code, verifier)
    assert response.status_code == 200, response.text
    issued = response.json()
    assert issued["token_type"] == "Bearer"
    assert issued["access_token"].startswith("sk_")
    assert issued["scope"] == "supoclip"
    assert "refresh_token" not in issued and "expires_in" not in issued
    assert response.headers["cache-control"] == "no-store"

    headers = {**STREAMABLE, "Authorization": f"Bearer {issued['access_token']}"}
    init = client.post("/mcp", json=INIT, headers=headers)
    assert init.status_code == 200
    session = init.headers["mcp-session-id"]
    client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers={**headers, "mcp-session-id": session})
    called = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "supoclip_ping", "arguments": {}}},
        headers={**headers, "mcp-session-id": session},
    )
    assert called.status_code == 200
    assert "pong" in called.text


def test_wrong_pkce_verifier_is_refused(client, backend):
    client_id, code, _ = connect(client, backend)
    response = token(client, client_id, code, secrets.token_urlsafe(48))
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"
    assert backend.keys == set()


def test_code_is_single_use(client, backend):
    client_id, code, verifier = connect(client, backend)
    assert token(client, client_id, code, verifier).status_code == 200
    again = token(client, client_id, code, verifier)
    assert again.status_code == 400
    assert again.json()["error"] == "invalid_grant"
    assert len(backend.keys) == 1


def test_wrong_redirect_at_token_is_refused(client, backend):
    client_id, code, verifier = connect(client, backend)
    response = token(client, client_id, code, verifier, redirect="https://claude.ai/elsewhere")
    assert response.status_code == 400
    assert backend.keys == set()


def test_code_for_another_client_is_refused(client, backend):
    _, code, verifier = connect(client, backend)
    other = register(client).json()["client_id"]
    response = token(client, other, code, verifier)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


def test_expired_code_is_refused(client, backend):
    client_id, code, verifier = connect(client, backend)
    backend.codes[code]["expires_at"] = time.time() - 1
    response = token(client, client_id, code, verifier)
    assert response.status_code == 400
    assert "expired" in response.json()["error_description"]
    assert backend.keys == set()


def test_revoked_key_stops_working(client, backend, monkeypatch):
    client_id, code, verifier = connect(client, backend)
    key = token(client, client_id, code, verifier).json()["access_token"]
    backend.keys.discard(key)  # revoked at /settings/api-keys
    # A fresh app has a fresh verifier cache (a live server forgets a key within 60 s).
    with TestClient(fresh_app(), base_url=BASE) as fresh:
        response = fresh.post("/mcp", json=INIT, headers={**STREAMABLE, "Authorization": f"Bearer {key}"})
    assert response.status_code == 401
    assert "resource_metadata" in response.headers["www-authenticate"]


def test_refresh_grant_is_refused(client, backend):
    client_id = register(client).json()["client_id"]
    response = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": "x", "client_id": client_id})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


def test_register_and_token_are_rate_limited(client, backend):
    backend.limit = 2
    assert register(client).status_code == 201
    assert register(client).status_code == 201
    limited = register(client)
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "30"
    assert client.post("/token", data={}).status_code != 429
    assert client.post("/token", data={}).status_code != 429
    assert client.post("/token", data={}).status_code == 429


def test_rate_limit_caller_is_the_proxied_client(client, backend, monkeypatch):
    from supoclip_mcp import oauth

    assert oauth.client_ip({"type": "http", "headers": [(b"x-real-ip", b"203.0.113.9")], "client": ("172.18.0.1", 1)}) == "203.0.113.9"
    assert oauth.client_ip({"type": "http", "headers": [(b"x-forwarded-for", b"1.1.1.1, 203.0.113.9")], "client": None}) == "203.0.113.9"
    assert oauth.client_ip({"type": "http", "headers": [], "client": ("172.18.0.1", 1)}) == "172.18.0.1"


def test_oauth_is_off_without_the_secret(backend, monkeypatch):
    monkeypatch.setattr(server, "SETTINGS", replace(server.SETTINGS, mcp_oauth_secret=None))
    with TestClient(server.build_app(server._build_mcp()), base_url=BASE) as plain:
        assert plain.get("/.well-known/oauth-authorization-server").json()["error"] == "oauth_not_supported"
        assert plain.post("/register", json={}).status_code == 404
        response = plain.post("/mcp", json=INIT, headers=STREAMABLE)
        assert "resource_metadata" not in response.headers["www-authenticate"]
