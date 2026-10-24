import asyncio
import json
import time
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.repositories.chatgpt_repository import ChatGPTRepository as Repo
from src.services import chatgpt_service
from src.services.chatgpt_service import ChatGPTService, PLAN_SCOPE
from tests.fixtures.factories import create_user, create_source, create_task


@pytest.fixture
def encryption_key(monkeypatch):
    monkeypatch.setenv("APP_SETTINGS_ENCRYPTION_KEY", "chatgpt-test-encryption-key-never-use-in-production")


async def account(db, user_id, *, client_id="oaiapp_test", expires_at=None):
    tokens = {"access_token": "private-access", "refresh_token": "private-refresh", "scope": PLAN_SCOPE,
              "expires_at": expires_at if expires_at is not None else time.time() + 3600}
    account_id = await Repo.save_account(db, user_id, client_id, {"sub": "identity", "email": "same@example.com"}, tokens)
    await db.commit()
    return account_id


async def test_credentials_are_encrypted_and_never_returned_to_other_users(db_session, encryption_key):
    alice, bob = await create_user(db_session), await create_user(db_session)
    account_id = await account(db_session, alice["id"])
    stored = await Repo.account(db_session, alice["id"], account_id)
    assert "private-access" not in stored["encrypted_tokens"]
    assert "private-refresh" not in stored["encrypted_tokens"]
    assert Repo.tokens(stored)["access_token"] == "private-access"
    assert await Repo.account(db_session, bob["id"], account_id) is None
    status = await ChatGPTService(db_session).status(alice["id"])
    assert status["accounts"][0]["connected"] is True
    assert "private" not in json.dumps(status)
    assert (await ChatGPTService(db_session).status(bob["id"]))["accounts"] == []


async def test_state_is_single_use_expiring_and_bound_to_user(db_session, encryption_key):
    alice, bob = await create_user(db_session), await create_user(db_session)
    await Repo.save_attempt(db_session, alice["id"], "state", {"verifier": "private"})
    await db_session.commit()
    assert await Repo.consume_attempt(db_session, bob["id"], "state") is None
    assert await Repo.consume_attempt(db_session, alice["id"], "state") == {"verifier": "private"}
    assert await Repo.consume_attempt(db_session, alice["id"], "state") is None
    await Repo.save_attempt(db_session, alice["id"], "expired", {})
    await db_session.execute(text("UPDATE chatgpt_oauth_attempts SET expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second' WHERE user_id = :user_id"), {"user_id": alice["id"]})
    await db_session.commit()
    assert await Repo.consume_attempt(db_session, alice["id"], "expired") is None


async def test_host_and_account_registration_survive_local_signout(db_session, encryption_key):
    user = await create_user(db_session)
    first_host = await Repo.host_id(db_session)
    await db_session.commit()
    assert await Repo.host_id(db_session) == first_host
    account_id = await account(db_session, user["id"])
    second = await account(db_session, user["id"], client_id="oaiapp_other_workspace")
    assert second != account_id
    await Repo.select_model(db_session, user["id"], account_id, "chosen")
    await db_session.commit()
    await Repo.select_model(db_session, user["id"], second, "other-model")
    await db_session.commit()
    assert (await Repo.active(db_session, user["id"]))["id"] == second
    await db_session.execute(text("UPDATE chatgpt_accounts SET encrypted_tokens = NULL, enabled = false WHERE id = :id"), {"id": second})
    await db_session.commit()
    row = await Repo.account(db_session, user["id"], second)
    assert row["client_id"] == "oaiapp_other_workspace"
    assert Repo.tokens(row) == {}


async def test_concurrent_workers_refresh_rotating_tokens_once(db_session, initialized_database, encryption_key, monkeypatch):
    user = await create_user(db_session)
    account_id = await account(db_session, user["id"], expires_at=0)
    await Repo.select_model(db_session, user["id"], account_id, "chosen")
    await db_session.commit()
    async def rotate(data):
        await asyncio.sleep(0.05)
        assert data["refresh_token"] == "private-refresh"
        return {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}
    exchange = AsyncMock(side_effect=rotate)
    monkeypatch.setattr(chatgpt_service, "token_request", exchange)
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)
    async def refresh():
        async with sessions() as db:
            return await ChatGPTService(db).access_token(user["id"], account_id, require_enabled=True)
    assert await asyncio.gather(refresh(), refresh(), refresh()) == ["new-access"] * 3
    assert exchange.await_count == 1


async def test_disconnect_revokes_and_preserves_registration(db_session, encryption_key, monkeypatch):
    user = await create_user(db_session)
    account_id = await account(db_session, user["id"])
    requests = []
    def handle(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"revocation_endpoint": "https://auth.openai.com/revoke"})
        return httpx.Response(200)
    original = httpx.AsyncClient
    monkeypatch.setattr(chatgpt_service.httpx, "AsyncClient", lambda **_: original(transport=httpx.MockTransport(handle)))
    assert await ChatGPTService(db_session).disconnect(user["id"], account_id) is True
    assert b"private-refresh" in requests[-1].content
    row = await Repo.account(db_session, user["id"], account_id)
    assert row["encrypted_tokens"] is None and not row["enabled"]
    assert row["client_id"] == "oaiapp_test"


async def test_controls_require_signed_session_headers(client, auth_headers, monkeypatch):
    monkeypatch.setenv("NEXT_PUBLIC_APP_URL", "http://localhost:3107")
    assert (await client.get("/chatgpt/status")).status_code == 401
    assert (await client.get("/chatgpt/status", headers={"x-supoclip-user-id": "user-1"})).status_code == 401
    response = await client.get("/chatgpt/status", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["accounts"] == []


async def test_public_loopback_hop_never_exchanges_or_reflects_untrusted_parameters(client, monkeypatch):
    monkeypatch.setenv("NEXT_PUBLIC_APP_URL", "http://localhost:3107")
    exchange = AsyncMock()
    monkeypatch.setattr(chatgpt_service, "token_request", exchange)
    response = await client.get("/chatgpt/callback?code=code&state=state&redirect_uri=https://attacker.example&access_token=private")
    assert response.status_code == 303
    assert response.headers["location"] == "http://localhost:3107/api/chatgpt/callback?state=state&code=code"
    assert response.headers["referrer-policy"] == "no-referrer"
    exchange.assert_not_awaited()


async def test_worker_uses_own_plan_bypasses_shared_analysis_and_restores_context(db_session, encryption_key, monkeypatch):
    from src import ai
    from src.chatgpt_analysis import chatgpt_account
    from src.config import Config
    from src.services.task_service import TaskService

    user = await create_user(db_session)
    account_id = await account(db_session, user["id"])
    await Repo.select_model(db_session, user["id"], account_id, "chosen-model")
    await db_session.commit()
    source = await create_source(db_session, source_type="video_url")
    task = await create_task(db_session, user_id=user["id"], source_id=source["id"], status="queued")
    config = Config()
    config.self_host = True
    task_service = TaskService(db_session, config)
    monkeypatch.setattr(task_service, "_load_performance_context", AsyncMock(return_value=None))
    monkeypatch.setattr(task_service.cache_repo, "get_cache", AsyncMock(return_value={"transcript_text": "cached transcript", "analysis_json": {"other": "provider's analysis"}}))
    async def process(**kwargs):
        assert kwargs["cached_transcript"] == "cached transcript"
        assert kwargs["cached_analysis_json"] is None
        assert ai.get_transcript_agent().binding == (user["id"], account_id, "chosen-model")
        raise RuntimeError("simulated analysis failure")
    monkeypatch.setattr(task_service.video_service, "process_video_complete", process)
    outer = ("unrelated-user", "unrelated-account", "unrelated-model")
    token = chatgpt_account.set(outer)
    try:
        with pytest.raises(RuntimeError, match="simulated analysis failure"):
            await task_service.process_task(task["id"], url="upload://missing.mp4", source_type="upload", user_id=user["id"])
        assert chatgpt_account.get() == outer
    finally:
        chatgpt_account.reset(token)
