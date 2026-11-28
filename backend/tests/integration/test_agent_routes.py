"""The agent home's routes with a real database: access, one turn at a time, and the transcript."""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from src.agent import access as agent_access_module
from src.agent import rate_limit, runtime
from src.agent.loop import Turn
from tests.fixtures.factories import create_user


@pytest.fixture
async def agent_app(app, db_session, feature_flag, monkeypatch):
    await create_user(db_session, user_id="user-1", name="Sam Rivera")
    await feature_flag("creator_agent", web_enabled=True)
    monkeypatch.setattr(agent_access_module, "agent_configured", lambda: True)
    rate_limit.reset_local_state()
    started: list[str] = []

    async def no_model(self):
        # The turn stays open: no model call in tests.
        started.append(self.run_id)

    monkeypatch.setattr(Turn, "go", no_model)
    runtime.bind(app)
    yield started
    rate_limit.reset_local_state()
    await db_session.execute(text("DELETE FROM agent_conversations WHERE user_id = 'user-1'"))
    await db_session.execute(text("DELETE FROM agent_turn_events WHERE user_id = 'user-1'"))
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


@pytest.mark.asyncio
async def test_deleting_chats_does_not_reset_daily_limit_or_pro_trial(
    app, client, auth_headers, agent_app, db_session, monkeypatch
):
    # Daily limit survives deleting conversations.
    monkeypatch.setenv("AGENT_DAILY_TURN_LIMIT", "2")
    monkeypatch.setenv("AGENT_BURST_TURN_LIMIT", "10")
    for idx in range(2):
        created = await client.post("/agent/conversations", headers=auth_headers, json={"message": f"turn {idx}"})
        assert created.status_code == 201
        cid = created.json()["conversation"]["id"]
        deleted = await client.delete(f"/agent/conversations/{cid}", headers=auth_headers)
        assert deleted.status_code == 200

    blocked = await client.post("/agent/conversations", headers=auth_headers, json={"message": "turn 3"})
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "AGENT_DAILY_LIMIT"
    assert int(blocked.headers["retry-after"]) >= 1
    assert (await client.get("/agent/conversations", headers=auth_headers)).json()["conversations"] == []

    # Pro trial also survives deleting conversations.
    await db_session.execute(text("DELETE FROM agent_turn_events WHERE user_id = 'user-1'"))
    await db_session.commit()
    monkeypatch.setenv("AGENT_DAILY_TURN_LIMIT", "150")
    monkeypatch.setenv("AGENT_TRIAL_MESSAGES", "2")
    app.state.config.monetization_enabled = True
    app.state.config.self_host = False
    try:
        await _set_account(db_session, plan="pro")
        for idx in range(2):
            created = await client.post("/agent/conversations", headers=auth_headers, json={"message": f"trial {idx}"})
            assert created.status_code == 201
            cid = created.json()["conversation"]["id"]
            assert (await client.delete(f"/agent/conversations/{cid}", headers=auth_headers)).status_code == 200

        status = (await client.get("/agent/status", headers=auth_headers)).json()
        assert status["available"] is False and status["reason"] == "trial_used"
        assert status["trial"] == {"limit": 2, "used": 2}
        refused = await client.post("/agent/conversations", headers=auth_headers, json={"message": "one more"})
        assert refused.status_code == 402 and refused.json()["detail"]["code"] == "AGENT_TRIAL_USED"
    finally:
        app.state.config.monetization_enabled = False
        app.state.config.self_host = True


@pytest.mark.asyncio
async def test_burst_rate_limit_returns_429_with_retry_after(client, auth_headers, agent_app, db_session, monkeypatch):
    monkeypatch.setenv("AGENT_BURST_TURN_LIMIT", "2")
    monkeypatch.setenv("AGENT_BURST_WINDOW_SECONDS", "60")
    monkeypatch.setenv("AGENT_DAILY_TURN_LIMIT", "100")

    first = await client.post("/agent/conversations", headers=auth_headers, json={"message": "one"})
    assert first.status_code == 201
    cid = first.json()["conversation"]["id"]
    await db_session.execute(text("UPDATE agent_runs SET status = 'completed' WHERE conversation_id = :id"), {"id": cid})
    await db_session.commit()

    second = await client.post(f"/agent/conversations/{cid}/messages", headers=auth_headers, json={"message": "two"})
    assert second.status_code == 202
    await db_session.execute(text("UPDATE agent_runs SET status = 'completed' WHERE conversation_id = :id"), {"id": cid})
    await db_session.commit()

    third = await client.post(f"/agent/conversations/{cid}/messages", headers=auth_headers, json={"message": "three"})
    assert third.status_code == 429
    detail = third.json()["detail"]
    assert detail["code"] == "AGENT_RATE_LIMITED"
    assert detail["retry_after"] >= 1
    assert int(third.headers["retry-after"]) >= 1

    # Starting a new conversation is also rejected without leaving an empty chat behind.
    new_chat = await client.post("/agent/conversations", headers=auth_headers, json={"message": "new chat"})
    assert new_chat.status_code == 429
    listed = (await client.get("/agent/conversations", headers=auth_headers)).json()["conversations"]
    assert [c["id"] for c in listed] == [cid]


@pytest.mark.asyncio
async def test_concurrent_active_turns_capped_across_conversations(
    client, auth_headers, agent_app, db_session, monkeypatch
):
    monkeypatch.setenv("AGENT_MAX_CONCURRENT_TURNS", "2")

    c1 = await client.post("/agent/conversations", headers=auth_headers, json={"message": "chat 1"})
    c2 = await client.post("/agent/conversations", headers=auth_headers, json={"message": "chat 2"})
    assert c1.status_code == 201 and c2.status_code == 201

    c3 = await client.post("/agent/conversations", headers=auth_headers, json={"message": "chat 3"})
    assert c3.status_code == 429
    assert c3.json()["detail"]["code"] == "AGENT_CONCURRENT_LIMIT"
    assert int(c3.headers["retry-after"]) >= 1

    # A turn waiting on user approval is idle and frees its active slot.
    cid1 = c1.json()["conversation"]["id"]
    await db_session.execute(
        text("UPDATE agent_runs SET status = 'awaiting_approval' WHERE conversation_id = :id"),
        {"id": cid1},
    )
    await db_session.commit()

    c3_retry = await client.post("/agent/conversations", headers=auth_headers, json={"message": "chat 3"})
    assert c3_retry.status_code == 201

    # A stale heartbeat (crashed API process not yet reaped) does not hold a slot.
    cid2 = c2.json()["conversation"]["id"]
    await db_session.execute(
        text("UPDATE agent_runs SET heartbeat_at = NOW() - INTERVAL '5 minutes' WHERE conversation_id = :id"),
        {"id": cid2},
    )
    await db_session.commit()

    c4 = await client.post("/agent/conversations", headers=auth_headers, json={"message": "chat 4"})
    assert c4.status_code == 201


@pytest.mark.asyncio
async def test_concurrent_requests_across_chats_cannot_bypass_limits(
    app, client, auth_headers, agent_app, db_session, monkeypatch
):
    import asyncio

    # Daily limit under concurrent sends across distinct conversations.
    monkeypatch.setenv("AGENT_DAILY_TURN_LIMIT", "2")
    monkeypatch.setenv("AGENT_BURST_TURN_LIMIT", "20")
    monkeypatch.setenv("AGENT_MAX_CONCURRENT_TURNS", "10")

    responses = await asyncio.gather(
        *(client.post("/agent/conversations", headers=auth_headers, json={"message": f"race {i}"}) for i in range(5))
    )
    statuses = sorted(r.status_code for r in responses)
    assert statuses == [201, 201, 429, 429, 429]
    recorded = (
        await db_session.execute(text("SELECT COUNT(*) FROM agent_turn_events WHERE user_id = 'user-1'"))
    ).scalar_one()
    assert recorded == 2

    # Pro trial limit under concurrent sends.
    await db_session.execute(text("DELETE FROM agent_conversations WHERE user_id = 'user-1'"))
    await db_session.execute(text("DELETE FROM agent_turn_events WHERE user_id = 'user-1'"))
    await db_session.commit()
    monkeypatch.setenv("AGENT_DAILY_TURN_LIMIT", "150")
    monkeypatch.setenv("AGENT_TRIAL_MESSAGES", "1")
    app.state.config.monetization_enabled = True
    app.state.config.self_host = False
    try:
        await _set_account(db_session, plan="pro")
        trial_responses = await asyncio.gather(
            *(client.post("/agent/conversations", headers=auth_headers, json={"message": f"trial {i}"}) for i in range(4))
        )
        assert sorted(r.status_code for r in trial_responses) == [201, 402, 402, 402]
        recorded = (
            await db_session.execute(text("SELECT COUNT(*) FROM agent_turn_events WHERE user_id = 'user-1'"))
        ).scalar_one()
        assert recorded == 1
    finally:
        app.state.config.monetization_enabled = False
        app.state.config.self_host = True


@pytest.mark.asyncio
async def test_rate_limited_message_preserves_pending_approval(
    client, auth_headers, agent_app, db_session, monkeypatch
):
    monkeypatch.setenv("AGENT_BURST_TURN_LIMIT", "1")
    monkeypatch.setenv("AGENT_BURST_WINDOW_SECONDS", "60")

    created = await client.post("/agent/conversations", headers=auth_headers, json={"message": "publish clip"})
    assert created.status_code == 201
    cid = created.json()["conversation"]["id"]
    run_id = created.json()["run"]["id"]
    await db_session.execute(
        text("UPDATE agent_runs SET status = 'awaiting_approval', pending = '{\"waiting\": []}'::jsonb WHERE id = :id"),
        {"id": run_id},
    )
    await db_session.commit()

    limited = await client.post(f"/agent/conversations/{cid}/messages", headers=auth_headers, json={"message": "actually wait"})
    assert limited.status_code == 429

    # The pending approval must stay intact so the user can still approve/decline it.
    status_row = (
        await db_session.execute(text("SELECT status FROM agent_runs WHERE id = :id"), {"id": run_id})
    ).scalar_one()
    assert status_row == "awaiting_approval"


@pytest.mark.asyncio
async def test_sse_stream_concurrency_cap_returns_429(client, auth_headers, agent_app, monkeypatch):
    monkeypatch.setenv("AGENT_MAX_SSE_STREAMS", "1")
    created = await client.post("/agent/conversations", headers=auth_headers, json={"message": "hi"})
    assert created.status_code == 201
    cid = created.json()["conversation"]["id"]

    slot = await rate_limit.acquire_sse_slot("user-1", queue_adapter=client._transport.app.state.queue_adapter)
    try:
        blocked = await client.get(f"/agent/conversations/{cid}/events", headers=auth_headers)
        assert blocked.status_code == 429
        assert blocked.json()["detail"]["code"] == "AGENT_TOO_MANY_STREAMS"
        assert int(blocked.headers["retry-after"]) >= 1
    finally:
        await rate_limit.release_sse_slot("user-1", slot, queue_adapter=client._transport.app.state.queue_adapter)


@pytest.mark.asyncio
async def test_endpoint_write_rate_limit_blocks_malformed_spam(app, client, auth_headers, agent_app, monkeypatch):
    counts: dict[str, int] = {}

    class _CounterRedis:
        async def incr(self, key: str):
            counts[key] = counts.get(key, 0) + 1
            return counts[key]

        async def expire(self, key: str, seconds: int):
            return True

    class _Adapter:
        @classmethod
        async def get_pool(cls):
            return _CounterRedis()

    monkeypatch.setattr(app.state, "queue_adapter", _Adapter)
    monkeypatch.setenv("AGENT_WRITE_RATE_LIMIT", "2")
    monkeypatch.setenv("AGENT_WRITE_WINDOW_SECONDS", "60")

    r1 = await client.post("/agent/conversations", headers=auth_headers, json={"message": "   "})
    r2 = await client.post("/agent/conversations", headers=auth_headers, json={"message": "   "})
    r3 = await client.post("/agent/conversations", headers=auth_headers, json={"message": "   "})
    assert r1.status_code == 422 and r2.status_code == 422
    assert r3.status_code == 429
    assert r3.json()["detail"]["code"] == "AGENT_RATE_LIMITED"
    assert int(r3.headers["retry-after"]) >= 1

    # Even when the write bucket is exhausted, stopping a running chat is still allowed.
    from src.agent import store

    conv = await store.create_conversation("user-1", None, "stop test")
    stopped = await client.post(f"/agent/conversations/{conv['id']}/stop", headers=auth_headers)
    assert stopped.status_code == 200


@pytest.mark.asyncio
async def test_db_trigger_records_raw_inserts_for_rolling_deploy_compatibility(
    agent_app, db_session
):
    from src.agent import store

    conv = await store.create_conversation("user-1", None, "legacy release")
    # Simulate an old-release API container inserting into agent_runs directly.
    await db_session.execute(
        text(
            "INSERT INTO agent_runs (id, conversation_id, user_id, status, trigger, heartbeat_at) "
            "VALUES ('legacy-user-run', :cid, 'user-1', 'completed', 'user', NOW()), "
            "       ('legacy-followup-run', :cid, 'user-1', 'completed', 'followup', NOW())"
        ),
        {"cid": conv["id"]},
    )
    await db_session.commit()

    ids = (
        await db_session.execute(
            text("SELECT run_id FROM agent_turn_events WHERE user_id = 'user-1'")
        )
    ).scalars().all()
    assert ids == ["legacy-user-run"]


@pytest.mark.asyncio
async def test_followup_preserves_pending_approval_and_respects_concurrency(
    client, auth_headers, agent_app, db_session, monkeypatch
):
    monkeypatch.setenv("AGENT_MAX_CONCURRENT_TURNS", "1")
    created = await client.post("/agent/conversations", headers=auth_headers, json={"message": "publish clip"})
    assert created.status_code == 201
    cid = created.json()["conversation"]["id"]
    run_id = created.json()["run"]["id"]

    await db_session.execute(
        text("UPDATE agent_runs SET status = 'awaiting_approval', pending = '{\"waiting\": []}'::jsonb WHERE id = :id"),
        {"id": run_id},
    )
    await db_session.commit()

    # Follow-up must defer while the conversation is awaiting approval (never decline it).
    job = {"conversation_id": cid, "user_id": "user-1"}
    woke = await runtime.start_followup_turn(
        job, "Project done", {"kind": "project_finished"}, timezone_name="UTC"
    )
    assert woke is False
    status_row = (
        await db_session.execute(text("SELECT status FROM agent_runs WHERE id = :id"), {"id": run_id})
    ).scalar_one()
    assert status_row == "awaiting_approval"

    # Once completed, follow-up still defers if another chat holds the user's concurrent slot.
    await db_session.execute(text("UPDATE agent_runs SET status = 'completed' WHERE id = :id"), {"id": run_id})
    await db_session.commit()
    other = await client.post("/agent/conversations", headers=auth_headers, json={"message": "other active chat"})
    assert other.status_code == 201

    woke_while_busy = await runtime.start_followup_turn(
        job, "Project done", {"kind": "project_finished"}, timezone_name="UTC"
    )
    assert woke_while_busy is False


@pytest.mark.asyncio
async def test_approval_resume_respects_concurrent_limit(
    client, auth_headers, agent_app, db_session, monkeypatch
):
    monkeypatch.setenv("AGENT_MAX_CONCURRENT_TURNS", "1")
    first = await client.post("/agent/conversations", headers=auth_headers, json={"message": "publish"})
    assert first.status_code == 201
    cid1 = first.json()["conversation"]["id"]
    run1 = first.json()["run"]["id"]
    await db_session.execute(
        text("UPDATE agent_runs SET status = 'awaiting_approval', pending = '{\"waiting\": []}'::jsonb WHERE id = :id"),
        {"id": run1},
    )
    await db_session.commit()

    # Start another active chat, filling the single concurrent slot.
    second = await client.post("/agent/conversations", headers=auth_headers, json={"message": "another"})
    assert second.status_code == 201

    blocked = await client.post(
        f"/agent/conversations/{cid1}/approval",
        headers=auth_headers,
        json={"approved": True, "run_id": run1},
    )
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "AGENT_CONCURRENT_LIMIT"
    # Approval remains intact so the user can approve once the other turn finishes.
    status_row = (
        await db_session.execute(text("SELECT status FROM agent_runs WHERE id = :id"), {"id": run1})
    ).scalar_one()
    assert status_row == "awaiting_approval"
