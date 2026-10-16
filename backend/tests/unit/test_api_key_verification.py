import hashlib
import hmac
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from src.api.routes import api_keys
from src.database import get_db


@pytest.fixture
async def key_client(monkeypatch):
    user = SimpleNamespace(id="user-1", email="user@example.com", emailVerified=False)
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    service = SimpleNamespace(
        create_api_key=AsyncMock(return_value={"id": "key-1", "key": "sk_one-time-secret"}),
        list_api_keys=AsyncMock(return_value=[]),
        revoke_api_key=AsyncMock(return_value=True),
    )
    mail = SimpleNamespace(is_configured=True, send_created_email=AsyncMock())
    config = SimpleNamespace(backend_auth_secret="test-secret", allow_unsigned_backend_auth=False, auth_signature_ttl_seconds=300)
    monkeypatch.setattr(api_keys, "get_config", lambda: config)
    monkeypatch.setattr(api_keys, "ApiKeyService", lambda db: service)
    monkeypatch.setattr(api_keys, "ApiKeyEmailService", lambda cfg: mail)
    app = FastAPI()
    app.include_router(api_keys.router)
    app.dependency_overrides[get_db] = lambda: db
    timestamp = str(int(time.time()))
    headers = {
        "x-supoclip-user-id": user.id,
        "x-supoclip-ts": timestamp,
        "x-supoclip-signature": hmac.new(b"test-secret", f"{user.id}:{timestamp}".encode(), hashlib.sha256).hexdigest(),
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http, headers, user, service, mail, result


async def test_unverified_user_cannot_mint_even_with_verified_body(key_client):
    http, headers, _, service, mail, _ = key_client
    response = await http.post("/api-keys/", headers=headers, json={"name": "Test", "emailVerified": True})
    assert response.status_code == 403
    service.create_api_key.assert_not_awaited()
    mail.send_created_email.assert_not_awaited()


async def test_fresh_database_verification_allows_creation(key_client):
    http, headers, user, service, mail, _ = key_client
    assert (await http.post("/api-keys/", headers=headers, json={})).status_code == 403
    user.emailVerified = True
    response = await http.post("/api-keys/", headers=headers, json={"name": "Automation"})
    assert response.status_code == 200
    assert response.json()["api_key"]["key"] == "sk_one-time-secret"
    service.create_api_key.assert_awaited_once_with(user.id, "Automation")
    mail.send_created_email.assert_awaited_once()


async def test_missing_user_does_not_mint(key_client):
    http, headers, _, service, mail, result = key_client
    result.scalar_one_or_none.return_value = None
    assert (await http.post("/api-keys/", headers=headers, json={})).status_code == 404
    service.create_api_key.assert_not_awaited()
    mail.send_created_email.assert_not_awaited()


async def test_unverified_user_can_list_and_revoke(key_client):
    http, headers, _, service, _, _ = key_client
    assert (await http.get("/api-keys/", headers=headers)).status_code == 200
    assert (await http.delete("/api-keys/key-1", headers=headers)).status_code == 200
    service.revoke_api_key.assert_awaited_once_with("user-1", "key-1")


async def test_api_key_cannot_authenticate_mint(key_client):
    http, _, _, service, _, _ = key_client
    response = await http.post("/api-keys/", headers={"Authorization": "Bearer sk_existing", "x-supoclip-user-id": "user-1"}, json={})
    assert response.status_code == 401
    service.create_api_key.assert_not_awaited()


async def test_notification_failure_preserves_created_secret(key_client):
    http, headers, user, _, mail, _ = key_client
    user.emailVerified = True
    mail.send_created_email.side_effect = RuntimeError("SES unavailable")
    response = await http.post("/api-keys/", headers=headers, json={})
    assert response.status_code == 200
    assert response.json()["api_key"]["key"] == "sk_one-time-secret"
