"""The agent home's routes with a real database: access, one turn at a time, and the transcript."""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from src.agent import access as agent_access_module
from src.agent import runtime
from src.agent.loop import Turn
from tests.fixtures.factories import create_user


@pytest.fixture
async def agent_app(app, db_session, feature_flag, monkeypatch):
    await create_user(db_session, user_id="user-1", name="Sam Rivera")
    await feature_flag("creator_agent", web_enabled=True)
    monkeypatch.setattr(agent_access_module, "agent_configured", lambda: True)
    started: list[str] = []

    async def no_model(self):
        # The turn stays open: no model call in tests.
        started.append(self.run_id)

    monkeypatch.setattr(Turn, "go", no_model)
    runtime.bind(app)
    yield started
    await db_session.execute(text("DELETE FROM agent_conversations WHERE user_id = 'user-1'"))
    await db_session.commit()


@pytest.mark.asyncio
async def test_status_follows_the_feature_flag(client, auth_headers, agent_app, feature_flag):
    status = (await client.get("/agent/status", headers=auth_headers)).json()
    assert status["available"] is True and status["home_mode"] is None
    await feature_flag("creator_agent", web_enabled=False, admins_bypass=False)
    status = (await client.get("/agent/status", headers=auth_headers)).json()
    assert status == {**status, "available": False, "reason": "disabled"}


@pytest.mark.asyncio
async def test_a_chat_starts_one_turn_at_a_time(client, auth_headers, agent_app, db_session):
    created = await client.post(
        "/agent/conversations", headers=auth_headers,
        json={"message": "Plan my week", "timezone": "Europe/Zurich", "locale": "en"},
    )
    assert created.status_code == 201, created.text
    conversation_id = created.json()["conversation"]["id"]
    assert created.json()["conversation"]["title"] == "Plan my week"
    assert len(agent_app) == 1

    # The first turn is still open (the fake model never finished it).
    busy = await client.post(
        f"/agent/conversations/{conversation_id}/messages", headers=auth_headers, json={"message": "and?"},
    )
    assert busy.status_code == 409

    loaded = (await client.get(f"/agent/conversations/{conversation_id}", headers=auth_headers)).json()
    assert [item["type"] for item in loaded["items"]] == ["user"]
    assert loaded["items"][0]["text"] == "Plan my week"
    assert loaded["run"]["status"] == "queued"
    # What the model receives carries the context block before the person's words.
    stored = (await db_session.execute(
        text("SELECT content FROM agent_messages WHERE conversation_id = :id"), {"id": conversation_id},
    )).scalar_one()
    assert "Europe/Zurich" in stored[0]["text"] and "Sam" in stored[0]["text"]
    assert stored[1]["text"] == "Plan my week"

    listed = (await client.get("/agent/conversations", headers=auth_headers)).json()["conversations"]
    assert [c["id"] for c in listed] == [conversation_id]


@pytest.mark.asyncio
async def test_other_people_cannot_open_a_chat(client, auth_headers, agent_app):
    created = await client.post("/agent/conversations", headers=auth_headers, json={"message": "hi"})
    conversation_id = created.json()["conversation"]["id"]
    import hashlib, hmac, time

    timestamp = str(int(time.time()))
    other = {
        "x-supoclip-user-id": "user-2",
        "x-supoclip-ts": timestamp,
        "x-supoclip-signature": hmac.new(
            b"test-backend-auth-secret", f"user-2:{timestamp}".encode(), hashlib.sha256
        ).hexdigest(),
    }
    assert (await client.get(f"/agent/conversations/{conversation_id}", headers=other)).status_code == 404


@pytest.mark.asyncio
async def test_empty_and_oversized_messages_are_refused(client, auth_headers, agent_app):
    assert (await client.post("/agent/conversations", headers=auth_headers, json={"message": "  "})).status_code == 422
    long = await client.post("/agent/conversations", headers=auth_headers, json={"message": "x" * 5000})
    assert long.status_code == 422
    # A refused first message leaves no empty chat behind.
    assert (await client.get("/agent/conversations", headers=auth_headers)).json()["conversations"] == []


@pytest.mark.asyncio
async def test_paid_hosting_needs_the_creator_plan(app, client, auth_headers, agent_app, db_session):
    app.state.config.monetization_enabled = True
    app.state.config.self_host = False
    try:
        status = (await client.get("/agent/status", headers=auth_headers)).json()
        assert status["available"] is False and status["reason"] == "plan"
        refused = await client.post("/agent/conversations", headers=auth_headers, json={"message": "hi"})
        assert refused.status_code == 402
    finally:
        app.state.config.monetization_enabled = False
        app.state.config.self_host = True


async def _set_account(db_session, *, plan, created_at="2026-01-01T00:00:00+00:00", status="active"):
    from datetime import datetime

    await db_session.execute(
        text('UPDATE users SET plan = :plan, subscription_status = :status, "createdAt" = :created WHERE id = \'user-1\''),
        {"plan": plan, "status": status, "created": datetime.fromisoformat(created_at)},
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_pro_gets_a_few_free_messages_then_creator(app, client, auth_headers, agent_app, db_session, monkeypatch):
    monkeypatch.setenv("AGENT_TRIAL_MESSAGES", "2")
    app.state.config.monetization_enabled = True
    app.state.config.self_host = False
    try:
        await _set_account(db_session, plan="pro")
        status = (await client.get("/agent/status", headers=auth_headers)).json()
        assert status["access"] == "trial" and status["trial"] == {"limit": 2, "used": 0}
        # An existing Pro account isn't interrupted by the choice screen.
        assert status["offer_choice"] is False

        first = await client.post("/agent/conversations", headers=auth_headers, json={"message": "hi"})
        assert first.status_code == 201
        conversation_id = first.json()["conversation"]["id"]
        await db_session.execute(text("UPDATE agent_runs SET status = 'done' WHERE conversation_id = :id"), {"id": conversation_id})
        await db_session.commit()
        second = await client.post(f"/agent/conversations/{conversation_id}/messages", headers=auth_headers, json={"message": "and now?"})
        assert second.status_code == 202

        status = (await client.get("/agent/status", headers=auth_headers)).json()
        assert status["available"] is False and status["reason"] == "trial_used"
        assert status["trial"] == {"limit": 2, "used": 2}
        await db_session.execute(text("UPDATE agent_runs SET status = 'done' WHERE conversation_id = :id"), {"id": conversation_id})
        await db_session.commit()
        refused = await client.post(f"/agent/conversations/{conversation_id}/messages", headers=auth_headers, json={"message": "one more"})
        assert refused.status_code == 402 and refused.json()["detail"]["code"] == "AGENT_TRIAL_USED"
        # The chat stays readable.
        assert (await client.get(f"/agent/conversations/{conversation_id}", headers=auth_headers)).status_code == 200

        await _set_account(db_session, plan="scale")
        status = (await client.get("/agent/status", headers=auth_headers)).json()
        assert status["access"] == "full" and status["available"] is True
    finally:
        app.state.config.monetization_enabled = False
        app.state.config.self_host = True


@pytest.mark.asyncio
async def test_the_choice_screen_goes_to_new_accounts_and_creators(app, client, auth_headers, agent_app, db_session, monkeypatch):
    monkeypatch.setenv("AGENT_ONBOARDING_SINCE", "2026-06-01T00:00:00Z")
    app.state.config.monetization_enabled = True
    app.state.config.self_host = False
    try:
        await _set_account(db_session, plan="free", status="inactive")
        status = (await client.get("/agent/status", headers=auth_headers)).json()
        assert status["reason"] == "plan" and status["offer_choice"] is False

        await _set_account(db_session, plan="free", status="inactive", created_at="2026-07-01T00:00:00+00:00")
        status = (await client.get("/agent/status", headers=auth_headers)).json()
        assert status["reason"] == "plan" and status["offer_choice"] is True

        await _set_account(db_session, plan="scale")
        assert (await client.get("/agent/status", headers=auth_headers)).json()["offer_choice"] is True

        await db_session.execute(text("UPDATE users SET home_mode = 'manual' WHERE id = 'user-1'"))
        await db_session.commit()
        assert (await client.get("/agent/status", headers=auth_headers)).json()["offer_choice"] is False
    finally:
        app.state.config.monetization_enabled = False
        app.state.config.self_host = True


@pytest.mark.asyncio
async def test_a_chat_opened_mid_reply_shows_what_was_written(client, auth_headers, agent_app, monkeypatch):
    from src.agent import events

    started = await client.post("/agent/conversations", headers=auth_headers, json={"message": "hi"})
    conversation_id = started.json()["conversation"]["id"]
    run_id = started.json()["run"]["id"]
    from src.agent import store

    last = (await store.list_messages(conversation_id))[-1]["id"]
    saved = {"run_id": run_id, "text": "Looking at your library", "event_id": "171-0", "after_message_id": last}
    monkeypatch.setattr(events, "get_partial", AsyncMock(return_value=saved))
    opened = (await client.get(f"/agent/conversations/{conversation_id}", headers=auth_headers)).json()
    assert opened["partial"] == {"text": "Looking at your library", "event_id": "171-0"}

    # Saved already (the transcript moved past where it began): shown once, from the transcript.
    monkeypatch.setattr(events, "get_partial", AsyncMock(return_value={**saved, "after_message_id": last - 1}))
    assert (await client.get(f"/agent/conversations/{conversation_id}", headers=auth_headers)).json()["partial"] is None

    # Another run's leftovers are never shown.
    monkeypatch.setattr(events, "get_partial", AsyncMock(return_value={**saved, "run_id": "other"}))
    assert (await client.get(f"/agent/conversations/{conversation_id}", headers=auth_headers)).json()["partial"] is None


@pytest.mark.asyncio
async def test_two_sends_at_once_start_one_turn(client, auth_headers, agent_app, db_session):
    import asyncio

    from src.agent import store

    started = await client.post("/agent/conversations", headers=auth_headers, json={"message": "hi"})
    conversation_id = started.json()["conversation"]["id"]
    await db_session.execute(text("UPDATE agent_runs SET status = 'completed' WHERE conversation_id = :id"), {"id": conversation_id})
    await db_session.commit()
    runs = await asyncio.gather(*(store.create_run(conversation_id, "user-1") for _ in range(6)))
    assert sum(run is not None for run in runs) == 1
