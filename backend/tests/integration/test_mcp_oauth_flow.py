"""MCP OAuth against real PostgreSQL and Redis: consent to a working, revocable API key."""

import socket
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.config import get_config
from src.services.api_key_email_service import ApiKeyEmailService
from tests.fixtures.factories import create_user

SECRET = "integration-mcp-secret"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
INTERNAL = {"x-supoclip-mcp-secret": SECRET}


@pytest.fixture
def redis_available():
    config = get_config()
    try:
        socket.create_connection((config.redis_host, config.redis_port), 1).close()
    except OSError:
        pytest.skip("Redis is not reachable")


async def test_consent_issues_a_revocable_key(client, db_session, auth_headers, monkeypatch, redis_available):
    get_config().mcp_oauth_secret = SECRET
    monkeypatch.setattr(ApiKeyEmailService, "is_configured", property(lambda self: False))
    await create_user(db_session, user_id="user-1", email_verified=True)
    client_id = f"client-{uuid4()}"

    registered = await client.post("/internal/mcp-oauth/clients", headers=INTERNAL, json={
        "client_id": client_id, "client_secret": "secret", "client_name": "Claude",
        "redirect_uris": [REDIRECT], "grant_types": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_method": "client_secret_post",
    })
    assert registered.status_code == 201, registered.text
    row = (await db_session.execute(
        text("SELECT client_name, metadata FROM mcp_oauth_clients WHERE client_id = :id"), {"id": client_id}
    )).mappings().one()
    assert row["client_name"] == "Claude"
    assert "client_secret" not in row["metadata"]

    started = await client.post("/internal/mcp-oauth/requests", headers=INTERNAL, json={
        "client_id": client_id, "redirect_uri": REDIRECT, "code_challenge": CHALLENGE, "state": "st",
    })
    request_id = started.json()["request_id"]
    described = (await client.get(f"/oauth/requests/{request_id}", headers=auth_headers)).json()
    assert described["client_name"] == "Claude" and described["redirect_host"] == "claude.ai"

    decided = await client.post(f"/oauth/requests/{request_id}/decision", headers=auth_headers, json={"approve": True})
    code = parse_qs(urlsplit(decided.json()["redirect_url"]).query)["code"][0]

    exchanged = await client.post("/internal/mcp-oauth/codes/exchange", headers=INTERNAL, json={"code": code, "client_id": client_id})
    assert exchanged.status_code == 200, exchanged.text
    key = exchanged.json()["access_token"]
    reused = await client.post("/internal/mcp-oauth/codes/exchange", headers=INTERNAL, json={"code": code, "client_id": client_id})
    assert reused.status_code == 400

    # The token is an ordinary API key: it authenticates, is listed, and only its hash is stored.
    assert (await client.get("/tasks/billing/summary", headers={"Authorization": f"Bearer {key}"})).status_code == 200
    listed = (await client.get("/api-keys/", headers=auth_headers)).json()["api_keys"]
    issued = next(item for item in listed if item["id"] == exchanged.json()["key_id"])
    assert issued["name"] == "Claude connector"
    stored = await db_session.scalar(text("SELECT count(*) FROM api_keys WHERE key_hash = :raw"), {"raw": key})
    assert stored == 0

    assert (await client.delete(f"/api-keys/{issued['id']}", headers=auth_headers)).status_code == 200
    assert (await client.get("/tasks/billing/summary", headers={"Authorization": f"Bearer {key}"})).status_code == 401

    await db_session.execute(text("DELETE FROM mcp_oauth_clients WHERE client_id = :id"), {"id": client_id})
    await db_session.execute(text("DELETE FROM api_keys WHERE user_id = 'user-1'"))
    await db_session.commit()
