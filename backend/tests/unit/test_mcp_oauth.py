"""OAuth state for the hosted MCP server: internal routes, consent and code exchange."""

import hashlib
import hmac
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from src.api.routes import mcp_oauth
from src.database import get_db
from src.services import mcp_oauth_service as service_module
from src.services.mcp_oauth_service import redirect_uri_allowed, with_query

SECRET = "mcp-secret"
AUTH_SECRET = "auth-secret"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


class FakeRedis:
    def __init__(self):
        self.data = {}

    def _alive(self, key):
        value = self.data.get(key)
        if value and value[1] is not None and value[1] < time.time():
            del self.data[key]
            return None
        return value

    async def get(self, key):
        value = self._alive(key)
        return value[0] if value else None

    async def set(self, key, value, ex=None):
        self.data[key] = (value, time.time() + ex if ex else None)

    async def getdel(self, key):
        value = self._alive(key)
        self.data.pop(key, None)
        return value[0] if value else None

    async def incr(self, key):
        value = self._alive(key)
        count = int(value[0]) + 1 if value else 1
        self.data[key] = (str(count), value[1] if value else None)
        return count

    async def expire(self, key, seconds):
        value, _ = self.data[key]
        self.data[key] = (value, time.time() + seconds)


@pytest.fixture
async def oauth(monkeypatch):
    redis = FakeRedis()
    clients = {}
    keys = []
    user = SimpleNamespace(id="user-1", email="owner@example.com", emailVerified=True)

    async def create_client(db, *, client_id, client_secret, client_name, metadata):
        clients[client_id] = {"client_id": client_id, "client_secret": client_secret, "client_name": client_name, "metadata": metadata}

    async def get_client(db, client_id):
        return clients.get(client_id)

    async def list_api_keys(db, user_id):
        return [{"revoked": False} for _ in keys]

    async def create_api_key(user_id, name):
        if len(keys) >= service_module.MAX_KEYS_PER_USER:
            raise service_module.ApiKeyLimitExceeded("limit")
        keys.append(name)
        return {"id": f"key-{len(keys)}", "name": name, "key": f"sk_issued_{len(keys)}", "key_prefix": "sk_issued"}

    monkeypatch.setattr(service_module.McpOAuthClientRepository, "create_client", create_client)
    monkeypatch.setattr(service_module.McpOAuthClientRepository, "get_client", get_client)
    monkeypatch.setattr(service_module.McpOAuthClientRepository, "touch_client", AsyncMock())
    monkeypatch.setattr(service_module.ApiKeyRepository, "list_api_keys", list_api_keys)
    monkeypatch.setattr(service_module, "ApiKeyService", lambda db: SimpleNamespace(create_api_key=create_api_key))

    async def load_user(self, user_id):
        if user_id != user.id:
            raise service_module.McpOAuthError("user_not_found", "User not found.", 404)
        return user

    monkeypatch.setattr(service_module.McpOAuthService, "_user", load_user)
    notify = AsyncMock()
    monkeypatch.setattr(mcp_oauth, "notify_api_key_created", notify)
    config = SimpleNamespace(
        mcp_oauth_secret=SECRET, backend_auth_secret=AUTH_SECRET,
        allow_unsigned_backend_auth=False, auth_signature_ttl_seconds=300,
    )
    monkeypatch.setattr(mcp_oauth, "get_config", lambda: config)

    app = FastAPI()
    app.include_router(mcp_oauth.internal_router)
    app.include_router(mcp_oauth.consent_router)
    app.dependency_overrides[get_db] = lambda: SimpleNamespace()
    app.dependency_overrides[mcp_oauth.get_oauth_redis] = lambda: redis
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield SimpleNamespace(http=http, redis=redis, clients=clients, keys=keys, user=user, notify=notify, config=config)


INTERNAL = {"x-supoclip-mcp-secret": SECRET}


def signed(user_id="user-1"):
    ts = str(int(time.time()))
    return {
        "x-supoclip-user-id": user_id,
        "x-supoclip-ts": ts,
        "x-supoclip-signature": hmac.new(AUTH_SECRET.encode(), f"{user_id}:{ts}".encode(), hashlib.sha256).hexdigest(),
    }


async def register(oauth, client_id="client-1", uris=(REDIRECT,)):
    return await oauth.http.post("/internal/mcp-oauth/clients", headers=INTERNAL, json={
        "client_id": client_id, "client_secret": "s3cret", "client_name": "Claude", "redirect_uris": list(uris),
    })


async def start(oauth, client_id="client-1", state="abc"):
    response = await oauth.http.post("/internal/mcp-oauth/requests", headers=INTERNAL, json={
        "client_id": client_id, "redirect_uri": REDIRECT, "code_challenge": CHALLENGE, "state": state,
    })
    assert response.status_code == 200, response.text
    return response.json()["request_id"]


async def approve(oauth, request_id):
    response = await oauth.http.post(f"/oauth/requests/{request_id}/decision", headers=signed(), json={"approve": True})
    assert response.status_code == 200, response.text
    query = parse_qs(urlsplit(response.json()["redirect_url"]).query)
    return query["code"][0], query.get("state", [None])[0]


@pytest.mark.parametrize("headers", [{}, {"x-supoclip-mcp-secret": "wrong"}])
async def test_internal_routes_need_the_mcp_secret(oauth, headers):
    response = await oauth.http.get("/internal/mcp-oauth/clients/client-1", headers=headers)
    assert response.status_code == 401


async def test_internal_routes_are_off_without_a_secret(oauth):
    oauth.config.mcp_oauth_secret = None
    response = await oauth.http.get("/internal/mcp-oauth/clients/client-1", headers=INTERNAL)
    assert response.status_code == 503


async def test_register_and_read_back_a_client(oauth):
    assert (await register(oauth)).status_code == 201
    client = (await oauth.http.get("/internal/mcp-oauth/clients/client-1", headers=INTERNAL)).json()
    assert client["client_secret"] == "s3cret"
    assert client["redirect_uris"] == [REDIRECT]
    assert "client_secret" not in oauth.clients["client-1"]["metadata"]
    assert (await oauth.http.get("/internal/mcp-oauth/clients/nope", headers=INTERNAL)).status_code == 404


@pytest.mark.parametrize("uri", ["http://evil.example/cb", "https://claude.ai/cb#x", "custom://cb"])
async def test_register_rejects_bad_redirects(oauth, uri):
    response = await register(oauth, uris=[uri])
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_redirect_uri"
    assert oauth.clients == {}


def test_redirect_rules():
    assert redirect_uri_allowed("https://chatgpt.com/connector_platform_oauth_redirect")
    assert redirect_uri_allowed("http://localhost:6274/oauth/callback")
    assert redirect_uri_allowed("http://127.0.0.1/cb")
    assert redirect_uri_allowed("http://[::1]:9/cb")
    assert not redirect_uri_allowed("http://localhost.evil.com/cb")
    assert not redirect_uri_allowed("http://10.0.0.1/cb")
    assert with_query("https://a.example/cb?x=1", {"code": "c", "state": None}) == "https://a.example/cb?x=1&code=c"


async def test_request_needs_a_registered_redirect(oauth):
    await register(oauth)
    response = await oauth.http.post("/internal/mcp-oauth/requests", headers=INTERNAL, json={
        "client_id": "client-1", "redirect_uri": "https://claude.ai/other", "code_challenge": CHALLENGE,
    })
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_redirect_uri"


async def test_request_stays_open_long_enough_to_verify_an_email(oauth):
    assert service_module.REQUEST_TTL_SECONDS == 1800
    assert service_module.CODE_TTL_SECONDS == 600
    await register(oauth)
    before = time.time()
    request_id = await start(oauth)
    _, expires = oauth.redis.data[f"mcp_oauth:request:{request_id}"]
    assert 1795 <= expires - before <= 1805
    described = (await oauth.http.get(f"/oauth/requests/{request_id}", headers=signed())).json()
    assert 1795 <= described["expires_at"] - before <= 1805

    code, _ = await approve(oauth, request_id)
    code_key = next(key for key in oauth.redis.data if key.startswith("mcp_oauth:code:"))
    assert 595 <= oauth.redis.data[code_key][1] - time.time() <= 605
    assert code


async def test_consent_describes_the_request(oauth):
    await register(oauth)
    request_id = await start(oauth)
    response = await oauth.http.get(f"/oauth/requests/{request_id}", headers=signed())
    assert response.status_code == 200
    body = response.json()
    assert body["client_name"] == "Claude"
    assert body["redirect_host"] == "claude.ai"
    assert body["email_verified"] is True
    assert body["at_key_limit"] is False
    assert "redirect_uri" not in body


async def test_consent_needs_a_signed_session_not_an_api_key(oauth):
    await register(oauth)
    request_id = await start(oauth)
    response = await oauth.http.post(
        f"/oauth/requests/{request_id}/decision",
        headers={"Authorization": "Bearer sk_leaked", "x-supoclip-user-id": "user-1"},
        json={"approve": True},
    )
    assert response.status_code == 401


async def test_approve_then_exchange_once(oauth):
    await register(oauth)
    request_id = await start(oauth)
    code, state = await approve(oauth, request_id)
    assert state == "abc"
    # The request is consumed by the decision.
    again = await oauth.http.post(f"/oauth/requests/{request_id}/decision", headers=signed(), json={"approve": True})
    assert again.status_code == 404
    # Codes are stored hashed.
    assert not any(code in key for key in oauth.redis.data)

    peek = (await oauth.http.post("/internal/mcp-oauth/codes/peek", headers=INTERNAL, json={"code": code})).json()
    assert peek["client_id"] == "client-1" and peek["code_challenge"] == CHALLENGE and "user_id" not in peek

    exchanged = await oauth.http.post("/internal/mcp-oauth/codes/exchange", headers=INTERNAL, json={"code": code, "client_id": "client-1"})
    assert exchanged.status_code == 200
    assert exchanged.json()["access_token"] == "sk_issued_1"
    assert oauth.keys == ["Claude connector"]
    oauth.notify.assert_awaited_once()

    reused = await oauth.http.post("/internal/mcp-oauth/codes/exchange", headers=INTERNAL, json={"code": code, "client_id": "client-1"})
    assert reused.status_code == 400
    assert reused.json()["detail"]["code"] == "invalid_grant"
    assert oauth.keys == ["Claude connector"]


async def test_deny_returns_access_denied(oauth):
    await register(oauth)
    request_id = await start(oauth)
    response = await oauth.http.post(f"/oauth/requests/{request_id}/decision", headers=signed(), json={"approve": False})
    query = parse_qs(urlsplit(response.json()["redirect_url"]).query)
    assert query["error"] == ["access_denied"] and query["state"] == ["abc"]


async def test_code_bound_to_its_client(oauth):
    await register(oauth)
    await register(oauth, client_id="client-2")
    code, _ = await approve(oauth, await start(oauth))
    response = await oauth.http.post("/internal/mcp-oauth/codes/exchange", headers=INTERNAL, json={"code": code, "client_id": "client-2"})
    assert response.status_code == 400
    assert oauth.keys == []


async def test_expired_code_is_refused(oauth):
    await register(oauth)
    code, _ = await approve(oauth, await start(oauth))
    for key, (value, _) in list(oauth.redis.data.items()):
        oauth.redis.data[key] = (value, time.time() - 1)
    response = await oauth.http.post("/internal/mcp-oauth/codes/exchange", headers=INTERNAL, json={"code": code, "client_id": "client-1"})
    assert response.status_code == 400
    assert oauth.keys == []


async def test_unverified_email_cannot_approve(oauth):
    oauth.user.emailVerified = False
    await register(oauth)
    request_id = await start(oauth)
    response = await oauth.http.post(f"/oauth/requests/{request_id}/decision", headers=signed(), json={"approve": True})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "email_not_verified"


async def test_key_limit_blocks_approval_and_keeps_the_request(oauth):
    await register(oauth)
    request_id = await start(oauth)
    oauth.keys.extend(["k"] * service_module.MAX_KEYS_PER_USER)
    described = (await oauth.http.get(f"/oauth/requests/{request_id}", headers=signed())).json()
    assert described["at_key_limit"] is True
    response = await oauth.http.post(f"/oauth/requests/{request_id}/decision", headers=signed(), json={"approve": True})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "key_limit"
    oauth.keys.clear()
    code, _ = await approve(oauth, request_id)  # still usable after revoking a key
    assert code


async def test_expired_request(oauth):
    response = await oauth.http.get("/oauth/requests/missing", headers=signed())
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "request_expired"


async def test_rate_limit(oauth, monkeypatch):
    monkeypatch.setitem(service_module.RATE_LIMITS, "register", (2, 3600))
    results = [
        (await oauth.http.post("/internal/mcp-oauth/rate-limit", headers=INTERNAL, json={"bucket": "register", "caller": "203.0.113.9"})).json()
        for _ in range(3)
    ]
    assert [r["allowed"] for r in results] == [True, True, False]
    assert results[-1]["retry_after"] > 0
    other = await oauth.http.post("/internal/mcp-oauth/rate-limit", headers=INTERNAL, json={"bucket": "register", "caller": "198.51.100.1"})
    assert other.json()["allowed"] is True
