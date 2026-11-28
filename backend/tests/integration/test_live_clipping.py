"""Live clipping through the API and the live-session cron, against a real database.

Stream metadata is faked (``resolve_live_stream``); everything else is real:
admission, billing rows, task metadata, the session tables and the cron step.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text

from src import live_sessions
from src.live_source import LiveSourceError, LiveStream
from tests.fixtures.factories import create_user

CHANNEL = "https://www.twitch.tv/somestreamer"
VOD = "https://www.twitch.tv/videos/1000000001"


def headers_for(user_id: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    signature = hmac.new(
        b"test-backend-auth-secret", f"{user_id}:{timestamp}".encode(), hashlib.sha256
    ).hexdigest()
    return {"x-supoclip-user-id": user_id, "x-supoclip-ts": timestamp, "x-supoclip-signature": signature}


def _stream(recorded: float, **kwargs) -> LiveStream:
    return LiveStream(
        platform="twitch", channel_url=CHANNEL, channel="SomeStreamer", recording_url=VOD,
        recording_id="1000000001", recorded_seconds=recorded, title="Sunday stream",
        started_at=time.time() - recorded, **kwargs,
    )


@pytest.fixture
async def live_user(db_session, feature_flag, app):
    await feature_flag("live_clipping", web_enabled=True)
    user = await create_user(db_session, user_id=str(uuid4()), plan="scale", subscription_status="active")
    app.state.queue_adapter.enqueued_jobs.clear()
    yield user
    await db_session.execute(text("DELETE FROM live_sessions WHERE user_id = :u"), {"u": user["id"]})
    await db_session.execute(text("DELETE FROM tasks WHERE user_id = :u"), {"u": user["id"]})
    await db_session.commit()


def _resolve(monkeypatch, value):
    def resolve(_url, **_kwargs):
        if isinstance(value, Exception):
            raise value
        return value() if callable(value) else value

    monkeypatch.setattr("src.api.routes.tasks.resolve_live_stream", resolve)
    monkeypatch.setattr("src.live_sessions.resolve_live_stream", resolve)


async def _metadata(db, task_id: str) -> dict:
    row = (await db.execute(text("SELECT source_metadata FROM tasks WHERE id = :id"), {"id": task_id})).fetchone()
    value = row.source_metadata
    if isinstance(value, str):
        import json

        value = json.loads(value)
    return value


@pytest.mark.asyncio
async def test_a_live_channel_asks_what_to_clip_before_anything_is_charged(client, db_session, live_user, monkeypatch):
    _resolve(monkeypatch, _stream(5400.0))
    response = await client.post("/tasks/", json={"source": {"url": CHANNEL}}, headers=headers_for(live_user["id"]))
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "LIVE_STREAM_DETECTED"
    assert detail["can_clip_live"] is True
    assert detail["live"]["elapsed_seconds"] == 5400
    assert detail["default_window_seconds"] == 1800
    count = (await db_session.execute(text("SELECT COUNT(*) FROM tasks WHERE user_id = :u"), {"u": live_user["id"]})).scalar_one()
    assert count == 0


@pytest.mark.asyncio
async def test_last_30_minutes_becomes_a_part_of_the_recording(client, app, db_session, live_user, monkeypatch):
    _resolve(monkeypatch, _stream(5400.0))
    response = await client.post(
        "/tasks/",
        json={"source": {"url": CHANNEL, "live": {"window_seconds": 1800}}, "caption_template": "default"},
        headers=headers_for(live_user["id"]),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["live"]["start_seconds"] == 3590 and body["live"]["end_seconds"] == 5390.0
    assert "live_session_id" not in body
    metadata = await _metadata(db_session, body["task_id"])
    assert metadata["url"] == VOD
    assert metadata["source_range"] == {
        "start_seconds": 3590, "end_seconds": 5390.0, "source_duration_seconds": 5400.0, "live": True,
    }
    assert metadata["live"]["platform"] == "twitch"
    task = (await db_session.execute(
        text("SELECT source_duration_seconds, status FROM tasks WHERE id = :id"), {"id": body["task_id"]},
    )).fetchone()
    assert task.source_duration_seconds == 1800
    job = app.state.queue_adapter.enqueued_jobs[-1]
    assert job["args"][1] == VOD and job["args"][2] == "twitch"

    detail = await client.get(f"/tasks/{body['task_id']}", headers=headers_for(live_user["id"]))
    assert detail.json()["live"]["platform"] == "twitch"
    assert detail.json()["live_session"] is None


@pytest.mark.asyncio
async def test_pro_accounts_are_told_live_clipping_is_creator(client, db_session, feature_flag, monkeypatch, app):
    await feature_flag("live_clipping", web_enabled=True)
    app.state.config.monetization_enabled = True
    user = await create_user(db_session, user_id=str(uuid4()), plan="pro", subscription_status="active")
    _resolve(monkeypatch, _stream(5400.0))
    monkeypatch.setattr("src.live_sessions.get_config", lambda: app.state.config)
    try:
        detected = await client.post("/tasks/", json={"source": {"url": CHANNEL}}, headers=headers_for(user["id"]))
        assert detected.json()["detail"]["can_clip_live"] is False
        refused = await client.post(
            "/tasks/", json={"source": {"url": CHANNEL, "live": True}}, headers=headers_for(user["id"]),
        )
        assert refused.status_code == 402
        assert refused.json()["detail"]["code"] == "LIVE_REQUIRES_CREATOR"
    finally:
        app.state.config.monetization_enabled = False


@pytest.mark.asyncio
async def test_with_the_flag_off_a_channel_url_is_refused_as_before(client, db_session, feature_flag, monkeypatch):
    await feature_flag("live_clipping", web_enabled=False, admins_bypass=False)
    user = await create_user(db_session, user_id=str(uuid4()))
    _resolve(monkeypatch, AssertionError("must not resolve"))
    response = await client.post("/tasks/", json={"source": {"url": CHANNEL}}, headers=headers_for(user["id"]))
    assert response.status_code == 422
    assert "Live streams aren't supported" in str(response.json()["detail"])


@pytest.mark.asyncio
async def test_continuous_session_adds_parts_and_a_final_one(client, db_session, live_user, monkeypatch, initialized_database):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    maker = async_sessionmaker(initialized_database, class_=AsyncSession, expire_on_commit=False)
    _resolve(monkeypatch, _stream(5400.0))
    started = await client.post(
        "/tasks/",
        json={"source": {"url": CHANNEL, "live": {"window_seconds": 1800, "follow": True}}, "clip_length": "auto"},
        headers=headers_for(live_user["id"]),
    )
    assert started.status_code == 200, started.text
    session_id = started.json()["live_session_id"]
    first_task = started.json()["task_id"]

    again = await client.post(
        "/tasks/", json={"source": {"url": CHANNEL, "live": {"follow": True}}}, headers=headers_for(live_user["id"]),
    )
    assert again.json() == {
        "task_id": first_task, "live_session_id": session_id, "already_following": True,
        "message": "Already clipping this stream",
    }

    # Not due yet: nothing happens.
    assert (await live_sessions.advance_live_sessions(maker))["due"] == 0

    # Twenty minutes later there isn't a full interval of new stream: it waits.
    await db_session.execute(text("UPDATE live_sessions SET next_check_at = NOW() WHERE id = :id"), {"id": session_id})
    await db_session.commit()
    _resolve(monkeypatch, _stream(5400.0 + 1200))
    assert await live_sessions.advance_live_sessions(maker) == {"due": 1, "waiting": 1}

    # Thirty-one minutes of new stream: part 2, overlapping by 90 s, billed for the new 30 min.
    await db_session.execute(text("UPDATE live_sessions SET next_check_at = NOW() WHERE id = :id"), {"id": session_id})
    await db_session.commit()
    _resolve(monkeypatch, _stream(5400.0 + 1860))
    assert await live_sessions.advance_live_sessions(maker) == {"due": 1, "part_created": 1}
    parts = await live_sessions.session_parts(db_session, session_id)
    assert [p["part_number"] for p in parts] == [1, 2]
    second = parts[1]["task_id"]
    metadata = await _metadata(db_session, second)
    assert metadata["source_range"]["start_seconds"] == 5300  # 5390 - 90 s overlap
    assert metadata["source_range"]["fresh_after_seconds"] == 5390.0
    assert metadata["live"]["part_number"] == 2
    billed = (await db_session.execute(
        text("SELECT source_duration_seconds, title FROM tasks t LEFT JOIN sources s ON s.id = t.source_id WHERE t.id = :id"),
        {"id": second},
    )).fetchone()
    assert billed.source_duration_seconds == 1860  # 7250 (live edge) - 5390 (end of part 1)
    assert billed.title == "Sunday stream · Part 2"

    # The stream ends: one last part for the rest of the recording, then the session ends.
    await db_session.execute(text("UPDATE live_sessions SET next_check_at = NOW() WHERE id = :id"), {"id": session_id})
    await db_session.commit()
    _resolve(monkeypatch, LiveSourceError("STREAM_NOT_LIVE", "offline"))
    monkeypatch.setattr("src.live_sessions.resolve_recording_length", lambda *_a: 5400.0 + 1860 + 600)
    assert await live_sessions.advance_live_sessions(maker) == {"due": 1, "ended": 1}
    session = (await client.get(f"/live-sessions/{session_id}", headers=headers_for(live_user["id"]))).json()
    assert session["status"] == "ended" and session["stop_reason"] == "ended"
    assert [p["part_number"] for p in session["parts"]] == [1, 2, 3]
    assert session["parts"][2]["end_seconds"] == 5400.0 + 1860 + 600

    page = (await client.get(f"/tasks/{first_task}", headers=headers_for(live_user["id"]))).json()
    assert page["live_session"]["id"] == session_id and page["live_session"]["part_number"] == 1


@pytest.mark.asyncio
async def test_stopping_a_session_keeps_its_parts(client, db_session, live_user, monkeypatch):
    _resolve(monkeypatch, _stream(3000.0))
    started = await client.post(
        "/tasks/", json={"source": {"url": CHANNEL, "live": {"follow": True}}}, headers=headers_for(live_user["id"]),
    )
    session_id = started.json()["live_session_id"]
    stopped = await client.post(f"/live-sessions/{session_id}/stop", headers=headers_for(live_user["id"]))
    assert stopped.json()["status"] == "stopped" and stopped.json()["stop_reason"] == "user"
    assert len(stopped.json()["parts"]) == 1
    listed = await client.get("/live-sessions/", headers=headers_for(live_user["id"]))
    assert listed.json()["sessions"][0]["id"] == session_id
    other = await client.get(f"/live-sessions/{session_id}", headers=headers_for(str(uuid4())))
    assert other.status_code in (401, 404)


@pytest.mark.asyncio
async def test_running_out_of_minutes_stops_the_session(db_session, live_user, monkeypatch, initialized_database):
    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    maker = async_sessionmaker(initialized_database, class_=AsyncSession, expire_on_commit=False)
    session_id = await live_sessions.start_session(
        db_session, user_id=live_user["id"], team_id=None, stream=_stream(3000.0), covered_until=2990.0,
        options={}, first_task_id=None, first_start=1190,
    )
    await db_session.execute(text("UPDATE live_sessions SET next_check_at = NOW() WHERE id = :id"), {"id": session_id})
    await db_session.commit()
    _resolve(monkeypatch, _stream(3000.0 + 1900))
    monkeypatch.setattr(
        live_sessions, "_create_part",
        AsyncMock(side_effect=HTTPException(status_code=402, detail={"code": "SUBSCRIPTION_REQUIRED", "message": "You've used this month's video minutes."})),
    )
    assert await live_sessions.advance_live_sessions(maker) == {"due": 1, "stopped_minutes": 1}
    row = (await db_session.execute(text("SELECT status, stop_reason, last_error FROM live_sessions WHERE id = :id"), {"id": session_id})).fetchone()
    assert (row.status, row.stop_reason) == ("stopped", "minutes")
    assert "minutes" in row.last_error
