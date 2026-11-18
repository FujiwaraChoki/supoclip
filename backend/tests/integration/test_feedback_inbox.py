"""Feedback inbox on PostgreSQL: storing submissions, the webhook outbox, admin API and env import."""

import asyncio
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from src.feedback import delivery, webhooks
from src.runtime_settings import encrypt_setting_value
from tests.fixtures.factories import create_user

MIGRATION = Path(__file__).parents[2] / "src/migrations/sql/20261117_0001_feedback_inbox.sql"
DISCORD = "https://discord.com/api/webhooks/987654321/SECRET-DISCORD-TOKEN"
JSON_URL = "https://hooks.example.com/supoclip/PRIVATE-PATH"
TABLES = ("feedback_webhook_attempts", "feedback_webhook_deliveries", "feedback_notes", "feedback_submissions",
          "feedback_webhooks", "feedback_webhook_env_imports", "cancellation_notes")


def headers_for(user_id: str, **extra: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    signature = hmac.new(
        b"test-backend-auth-secret", f"{user_id}:{timestamp}".encode(), hashlib.sha256
    ).hexdigest()
    return {"x-supoclip-user-id": user_id, "x-supoclip-ts": timestamp, "x-supoclip-signature": signature, **extra}


@pytest.fixture
async def clean(db_session, monkeypatch):
    monkeypatch.setenv("APP_SETTINGS_ENCRYPTION_KEY", "feedback-inbox-test-key-0123456789")

    async def resolve(_target):
        return "93.184.216.34"

    monkeypatch.setattr(webhooks, "resolve_public_address", resolve)

    async def wipe():
        for table in TABLES:
            await db_session.execute(text(f"DELETE FROM {table}"))
        await db_session.commit()

    await wipe()
    yield
    await wipe()


@pytest.fixture
def receiver(monkeypatch):
    """Answers each send with the next status in ``receiver.statuses`` (default 204)."""

    class Receiver:
        statuses: list[int] = []
        calls: list[dict] = []

    async def post(url, kind, body, headers):
        Receiver.calls.append({"url": url, "kind": kind, "body": json.loads(body), "headers": headers})
        return httpx.Response(Receiver.statuses.pop(0) if Receiver.statuses else 204)

    Receiver.statuses, Receiver.calls = [], []
    monkeypatch.setattr(delivery, "post_pinned", post)
    return Receiver


@pytest.fixture
def no_immediate(monkeypatch):
    scheduled = []
    monkeypatch.setattr("src.api.routes.feedback.schedule_immediate_delivery", scheduled.append)
    return scheduled


@pytest.fixture
async def admin(db_session):
    return await create_user(db_session, user_id="user-1", email="admin@example.com", is_admin=True)


async def make_webhook(db_session, *, kind="discord", url=DISCORD, categories=("bug",), enabled=True, name="Hook"):
    webhook, secret = await webhooks.create_webhook(
        db_session, name=name, kind=kind, url=url, categories=categories, enabled=enabled, admin_id=None
    )
    return webhook, secret


async def submit(client, user_id, **body):
    payload = {"category": "bug", "message": "Captions drift", **body}
    return await client.post(
        "/feedback",
        headers={**headers_for(user_id, **{"x-supoclip-client": "ios", "user-agent": "SupoClip/2.4 iOS"}),
                 "content-type": "application/json"},
        content=json.dumps(payload),
    )


async def deliveries(db_session, **where):
    clause = " AND ".join(f"{key} = :{key}" for key in where) or "TRUE"
    rows = await db_session.execute(text(f"SELECT * FROM feedback_webhook_deliveries WHERE {clause} ORDER BY created_at"), where)
    return [dict(row) for row in rows.mappings().all()]


async def make_due(db_session):
    await db_session.execute(text("UPDATE feedback_webhook_deliveries SET next_attempt_at = NOW() WHERE status = 'pending'"))
    await db_session.commit()


# --- Storing submissions -----------------------------------------------------------


async def test_submission_is_stored_and_queued_for_matching_enabled_webhooks(client, db_session, clean, no_immediate):
    user = await create_user(db_session, email="ana@example.com")
    bug_hook, _ = await make_webhook(db_session, categories=("bug", "feature"), name="Bugs")
    await make_webhook(db_session, categories=("sales",), name="Sales")
    await make_webhook(db_session, categories=("bug",), enabled=False, name="Off")

    response = await submit(client, user["id"], page_url="/tasks/abc", locale="de", app_version="2.4.0")

    assert response.status_code == 200
    submission_id = response.json()["id"]
    row = (await db_session.execute(text("SELECT * FROM feedback_submissions WHERE id = :id"), {"id": submission_id})).mappings().one()
    assert (row["user_id"], row["email"], row["category"], row["message"]) == (user["id"], "ana@example.com", "bug", "Captions drift")
    assert (row["platform"], row["page_url"], row["locale"], row["app_version"]) == ("ios", "/tasks/abc", "de", "2.4.0")
    assert row["user_agent"] == "SupoClip/2.4 iOS" and row["status"] == "open"
    queued = await deliveries(db_session, submission_id=submission_id)
    assert [d["webhook_id"] for d in queued] == [bug_hook["id"]]
    assert no_immediate == [submission_id]


async def test_submission_succeeds_without_any_webhook(client, db_session, clean, no_immediate):
    user = await create_user(db_session)
    response = await submit(client, user["id"], category="sales", message="Team plan?")
    assert response.status_code == 200
    assert no_immediate == []  # nothing to send


async def test_cancellation_note_shares_the_submission_id(client, db_session, clean, no_immediate):
    user = await create_user(db_session)
    response = await submit(client, user["id"], category="cancellation", message="Too slow")
    note = (await db_session.execute(text("SELECT id, message FROM cancellation_notes WHERE user_id = :u"), {"u": user["id"]})).one()
    assert tuple(note) == (response.json()["id"], "Too slow")


async def test_submissions_are_rate_limited_per_user(client, db_session, clean, no_immediate):
    user = await create_user(db_session)
    for _ in range(10):
        assert (await submit(client, user["id"])).status_code == 200
    assert (await submit(client, user["id"])).status_code == 429


async def test_invalid_submissions_are_rejected(client, db_session, clean, no_immediate):
    user = await create_user(db_session)
    invalid = await submit(client, user["id"], category="spam")
    assert invalid.status_code == 422
    assert isinstance(invalid.json()["detail"], str)
    assert (await submit(client, user["id"], message="   ")).status_code == 422
    assert (await submit(client, user["id"], message="x" * 2001)).status_code == 422
    assert (await client.post("/feedback", json={"category": "bug", "message": "hi"})).status_code == 401


async def test_immediate_delivery_sends_right_after_the_response(client, db_session, clean, receiver):
    user = await create_user(db_session)
    await make_webhook(db_session)
    response = await submit(client, user["id"])
    await asyncio.gather(*list(delivery._background))
    assert len(receiver.calls) == 1
    assert receiver.calls[0]["body"]["embeds"][0]["description"] == "Captions drift"
    [sent] = await deliveries(db_session, submission_id=response.json()["id"])
    assert sent["status"] == "delivered" and sent["attempts"] == 1


async def test_deleting_the_account_keeps_the_message(client, db_session, clean, no_immediate):
    user = await create_user(db_session)
    submission_id = (await submit(client, user["id"])).json()["id"]
    await db_session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user["id"]})
    await db_session.commit()
    row = (await db_session.execute(text("SELECT user_id, message FROM feedback_submissions WHERE id = :id"), {"id": submission_id})).one()
    assert tuple(row) == (None, "Captions drift")


# --- Outbox ------------------------------------------------------------------------


async def _one_submission(client, db_session, **webhook):
    user = await create_user(db_session)
    hook, secret = await make_webhook(db_session, **webhook)
    response = await submit(client, user["id"])
    return response.json()["id"], hook, secret


async def test_failed_sends_are_retried_with_backoff_until_delivered(client, db_session, clean, receiver, no_immediate):
    submission_id, _hook, _ = await _one_submission(client, db_session)
    receiver.statuses = [500, 503]

    assert await delivery.deliver_due(db_session) == {"pending": 1}
    [row] = await deliveries(db_session, submission_id=submission_id)
    assert (row["status"], row["attempts"], row["last_status_code"], row["last_error"]) == ("pending", 1, 500, "HTTP 500")
    wait = (row["next_attempt_at"] - datetime.now(timezone.utc)).total_seconds()
    assert 25 <= wait <= 30
    assert await delivery.deliver_due(db_session) == {}  # not due yet

    await make_due(db_session)
    await delivery.deliver_due(db_session)
    [row] = await deliveries(db_session, submission_id=submission_id)
    wait = (row["next_attempt_at"] - datetime.now(timezone.utc)).total_seconds()
    assert row["attempts"] == 2 and 115 <= wait <= 120

    await make_due(db_session)
    assert await delivery.deliver_due(db_session) == {"delivered": 1}
    [row] = await deliveries(db_session, submission_id=submission_id)
    assert row["status"] == "delivered" and row["delivered_at"] is not None
    attempts = (await db_session.execute(
        text("SELECT attempt, status_code FROM feedback_webhook_attempts WHERE delivery_id = :id ORDER BY attempt"),
        {"id": row["id"]})).all()
    assert [tuple(a) for a in attempts] == [(1, 500), (2, 503), (3, 204)]
    # Every attempt carried the same delivery's payload.
    assert len({json.dumps(call["body"]) for call in receiver.calls}) == 1


async def test_delivery_fails_after_the_last_attempt(client, db_session, clean, receiver, no_immediate):
    submission_id, _hook, _ = await _one_submission(client, db_session)
    receiver.statuses = [502] * delivery.MAX_ATTEMPTS
    for _ in range(delivery.MAX_ATTEMPTS):
        await make_due(db_session)
        await delivery.deliver_due(db_session)
    [row] = await deliveries(db_session, submission_id=submission_id)
    assert (row["status"], row["attempts"]) == ("failed", delivery.MAX_ATTEMPTS)


async def test_a_permanent_error_fails_at_once_and_an_admin_can_retry(client, db_session, clean, receiver, admin, auth_headers, no_immediate):
    submission_id, _hook, _ = await _one_submission(client, db_session)
    receiver.statuses = [404]
    assert await delivery.deliver_due(db_session) == {"failed": 1}
    [row] = await deliveries(db_session, submission_id=submission_id)

    response = await client.post(f"/admin/feedback/deliveries/{row['id']}/retry", headers=auth_headers)
    assert response.status_code == 200
    assert no_immediate[-1] == submission_id
    assert await delivery.deliver_due(db_session) == {"delivered": 1}
    # A delivered one can't be retried again.
    assert (await client.post(f"/admin/feedback/deliveries/{row['id']}/retry", headers=auth_headers)).status_code == 409


async def test_a_claimed_delivery_is_not_sent_twice(client, db_session, clean, receiver, no_immediate, initialized_database):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    await _one_submission(client, db_session)
    other = async_sessionmaker(initialized_database, class_=AsyncSession, expire_on_commit=False)
    async with other() as second:
        claimed = await delivery._claim(second, submission_id=None, limit=10)
    assert len(claimed) == 1
    assert await delivery.deliver_due(db_session) == {}  # leased by the other sender
    assert receiver.calls == []


async def test_disabling_a_webhook_cancels_what_is_queued(client, db_session, clean, receiver, no_immediate):
    submission_id, hook, _ = await _one_submission(client, db_session)
    await webhooks.update_webhook(db_session, hook["id"], admin_id=None, enabled=False)
    [row] = await deliveries(db_session, submission_id=submission_id)
    assert row["status"] == "cancelled"
    assert await delivery.deliver_due(db_session) == {}


async def test_json_webhooks_get_a_signed_payload(client, db_session, clean, receiver, no_immediate):
    submission_id, _hook, secret = await _one_submission(client, db_session, kind="json", url=JSON_URL)
    await delivery.deliver_due(db_session)
    [call] = receiver.calls
    assert call["url"] == JSON_URL and call["kind"] == "json"
    assert call["body"]["type"] == "feedback.created" and call["body"]["submission"]["id"] == submission_id
    timestamp, signature = (part.split("=", 1)[1] for part in call["headers"]["X-SupoClip-Signature"].split(","))
    body = json.dumps(call["body"], separators=(",", ":"), ensure_ascii=False).encode()
    assert signature == hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


async def test_cron_job_drains_due_deliveries(client, db_session, clean, receiver, no_immediate):
    await _one_submission(client, db_session)
    assert await delivery.deliver_feedback_webhooks({}) == {"delivered": 1}


# --- Admin API ---------------------------------------------------------------------


ADMIN_ROUTES = [
    ("GET", "/admin/feedback", None),
    ("GET", "/admin/feedback/some-id", None),
    ("PATCH", "/admin/feedback/some-id", {"status": "resolved"}),
    ("POST", "/admin/feedback/some-id/notes", {"body": "x"}),
    ("POST", "/admin/feedback/deliveries/some-id/retry", None),
    ("GET", "/admin/feedback-webhooks", None),
    ("POST", "/admin/feedback-webhooks", {"name": "x", "kind": "json", "url": JSON_URL, "categories": ["bug"]}),
    ("PATCH", "/admin/feedback-webhooks/some-id", {"enabled": False}),
    ("POST", "/admin/feedback-webhooks/some-id/rotate-secret", None),
    ("POST", "/admin/feedback-webhooks/some-id/test", None),
    ("GET", "/admin/feedback-webhooks/some-id/deliveries", None),
    ("DELETE", "/admin/feedback-webhooks/some-id", None),
]


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ROUTES)
async def test_every_admin_route_requires_an_admin(client, db_session, clean, method, path, body):
    await create_user(db_session, user_id="user-1", is_admin=False)
    signed = headers_for("user-1")
    assert (await client.request(method, path, json=body)).status_code == 401
    assert (await client.request(method, path, json=body, headers=signed)).status_code == 403
    assert (await db_session.execute(text("SELECT COUNT(*) FROM feedback_webhooks"))).scalar() == 0


async def test_admin_lists_filters_and_triages_submissions(client, db_session, clean, admin, auth_headers, no_immediate):
    ana = await create_user(db_session, email="ana@example.com", name="Ana")
    bo = await create_user(db_session, email="bo@example.com", name="Bo")
    bug = (await submit(client, ana["id"], category="bug", message="Crash on export")).json()["id"]
    await submit(client, bo["id"], category="sales", message="Team plan?")

    listed = (await client.get("/admin/feedback", headers=auth_headers)).json()
    assert listed["total"] == 2 and listed["status_counts"] == {"open": 2, "in_progress": 0, "resolved": 0}
    by_category = (await client.get("/admin/feedback?category=sales", headers=auth_headers)).json()
    assert [i["email"] for i in by_category["items"]] == ["bo@example.com"]
    by_search = (await client.get("/admin/feedback?q=ANA@", headers=auth_headers)).json()
    assert [i["id"] for i in by_search["items"]] == [bug]
    assert (await client.get(f"/admin/feedback?q={ana['id']}", headers=auth_headers)).json()["total"] == 1
    assert (await client.get("/admin/feedback?q=%25", headers=auth_headers)).json()["total"] == 0
    assert (await client.get("/admin/feedback?from=2099-01-01T00:00:00Z", headers=auth_headers)).json()["total"] == 0

    updated = (await client.patch(f"/admin/feedback/{bug}", json={"status": "resolved"}, headers=auth_headers)).json()
    assert updated["submission"]["status"] == "resolved" and updated["submission"]["resolved_by"] == "user-1"
    assert updated["submission"]["resolved_at"] is not None
    note = await client.post(f"/admin/feedback/{bug}/notes", json={"body": "Fixed in 2.5"}, headers=auth_headers)
    assert note.status_code == 201
    detail = (await client.get(f"/admin/feedback/{bug}", headers=auth_headers)).json()["submission"]
    assert detail["user_name"] == "Ana" and [n["body"] for n in detail["notes"]] == ["Fixed in 2.5"]
    reopened = (await client.patch(f"/admin/feedback/{bug}", json={"status": "open"}, headers=auth_headers)).json()
    assert reopened["submission"]["resolved_at"] is None
    assert (await client.patch(f"/admin/feedback/{bug}", json={"status": "done"}, headers=auth_headers)).status_code == 422
    assert (await client.get("/admin/feedback/missing", headers=auth_headers)).status_code == 404


async def test_webhook_urls_and_secrets_never_come_back(client, db_session, clean, admin, auth_headers, receiver):
    created = await client.post(
        "/admin/feedback-webhooks",
        json={"name": "Zapier", "kind": "json", "url": JSON_URL, "categories": ["bug", "sales"]},
        headers=auth_headers,
    )
    assert created.status_code == 201
    secret = created.json()["secret"]
    assert secret.startswith("whsec_")
    webhook = created.json()["webhook"]
    assert webhook["url_hint"] == "hooks.example.com/…PATH" and webhook["categories"] == ["bug", "sales"]
    discord = await client.post(
        "/admin/feedback-webhooks",
        json={"name": "Discord", "kind": "discord", "url": DISCORD, "categories": ["general"]},
        headers=auth_headers,
    )
    assert discord.json()["secret"] is None

    hook_id = webhook["id"]
    responses = [
        await client.get("/admin/feedback-webhooks", headers=auth_headers),
        await client.patch(f"/admin/feedback-webhooks/{hook_id}", json={"name": "Renamed", "url": ""}, headers=auth_headers),
        await client.post(f"/admin/feedback-webhooks/{hook_id}/test", headers=auth_headers),
        await client.get(f"/admin/feedback-webhooks/{hook_id}/deliveries", headers=auth_headers),
    ]
    for response in responses:
        assert response.status_code == 200, response.text
        assert "PRIVATE-PATH" not in response.text and "SECRET-DISCORD-TOKEN" not in response.text
        assert secret not in response.text
    stored = (await db_session.execute(text("SELECT url_encrypted, secret_encrypted FROM feedback_webhooks WHERE id = :id"),
                                       {"id": hook_id})).one()
    assert "hooks.example.com" not in stored[0] and secret not in stored[1]
    # An empty URL kept the old one: the test went to it.
    assert receiver.calls[0]["url"] == JSON_URL and receiver.calls[0]["body"]["type"] == "feedback.test"
    assert responses[3].json()["deliveries"][0]["is_test"] is True
    assert responses[3].json()["deliveries"][0]["attempt_log"][0]["attempt"] == 1

    rotated = (await client.post(f"/admin/feedback-webhooks/{hook_id}/rotate-secret", headers=auth_headers)).json()["secret"]
    assert rotated != secret
    assert (await client.post(f"/admin/feedback-webhooks/{discord.json()['webhook']['id']}/rotate-secret",
                              headers=auth_headers)).status_code == 404
    assert (await client.delete(f"/admin/feedback-webhooks/{hook_id}", headers=auth_headers)).status_code == 200
    assert (await client.delete(f"/admin/feedback-webhooks/{hook_id}", headers=auth_headers)).status_code == 404


@pytest.mark.parametrize(
    ("kind", "url", "code"),
    [
        ("json", "http://hooks.example.com/x", "url_not_https"),
        ("json", "https://169.254.169.254/latest/meta-data", "url_private_address"),
        ("json", "https://localhost:8000/admin", "url_private_address"),
        ("json", "https://10.1.2.3/x", "url_private_address"),
        ("discord", "https://hooks.example.com/api/webhooks/1/x", "url_not_discord"),
    ],
)
async def test_unsafe_webhook_urls_are_refused(client, db_session, clean, admin, auth_headers, kind, url, code):
    response = await client.post(
        "/admin/feedback-webhooks", json={"name": "x", "kind": kind, "url": url, "categories": ["bug"]}, headers=auth_headers
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == code
    assert (await db_session.execute(text("SELECT COUNT(*) FROM feedback_webhooks"))).scalar() == 0


async def test_a_host_resolving_to_a_private_address_is_refused(client, db_session, clean, admin, auth_headers, monkeypatch):
    monkeypatch.undo()  # the real check, with a resolver that answers with an internal address
    monkeypatch.setenv("APP_SETTINGS_ENCRYPTION_KEY", "feedback-inbox-test-key-0123456789")
    loop = asyncio.get_running_loop()

    async def getaddrinfo(host, port, type=0):  # noqa: A002
        return [(2, 1, 6, "", ("10.0.0.9", port))]

    monkeypatch.setattr(loop, "getaddrinfo", getaddrinfo)
    response = await client.post(
        "/admin/feedback-webhooks",
        json={"name": "x", "kind": "json", "url": "https://internal.example.com/x", "categories": ["bug"]},
        headers=auth_headers,
    )
    assert response.status_code == 422 and response.json()["detail"]["code"] == "url_private_address"


async def test_webhook_input_is_validated(client, db_session, clean, admin, auth_headers):
    base = {"name": "x", "kind": "json", "url": JSON_URL, "categories": ["bug"]}
    for override in ({"categories": []}, {"categories": ["spam"]}, {"kind": "slack"}, {"name": ""}):
        response = await client.post("/admin/feedback-webhooks", json={**base, **override}, headers=auth_headers)
        assert response.status_code == 422, override


# --- Environment import and migration ------------------------------------------------


async def test_env_webhooks_are_imported_once(db_session, clean, app):
    config = app.state.config
    config.discord_feedback_webhook_url = DISCORD
    config.discord_sales_webhook_url = "https://discord.com/api/webhooks/1/SALES-TOKEN"

    assert await webhooks.import_env_webhooks(db_session, config) == [
        "DISCORD_FEEDBACK_WEBHOOK_URL", "DISCORD_SALES_WEBHOOK_URL"]
    rows = {w["env_source"]: w for w in await webhooks.list_webhooks(db_session)}
    assert rows["DISCORD_FEEDBACK_WEBHOOK_URL"]["categories"] == ["bug", "feature", "general", "cancellation"]
    assert rows["DISCORD_SALES_WEBHOOK_URL"]["categories"] == ["sales"]
    assert all(w["kind"] == "discord" and w["enabled"] for w in rows.values())
    destination = await webhooks.load_destination(db_session, rows["DISCORD_SALES_WEBHOOK_URL"]["id"])
    assert destination["url"].endswith("SALES-TOKEN")

    assert await webhooks.import_env_webhooks(db_session, config) == []
    await webhooks.delete_webhook(db_session, rows["DISCORD_SALES_WEBHOOK_URL"]["id"])
    assert await webhooks.import_env_webhooks(db_session, config) == []  # deleting is final
    assert len(await webhooks.list_webhooks(db_session)) == 1


async def test_an_invalid_env_webhook_is_skipped_and_retried_later(db_session, clean, app):
    config = app.state.config
    config.discord_feedback_webhook_url = "http://discord.com/api/webhooks/1/x"
    config.discord_sales_webhook_url = None
    assert await webhooks.import_env_webhooks(db_session, config) == []
    config.discord_feedback_webhook_url = DISCORD
    assert await webhooks.import_env_webhooks(db_session, config) == ["DISCORD_FEEDBACK_WEBHOOK_URL"]


async def test_migration_is_idempotent_and_copies_cancellation_notes(db_session, clean):
    user = await create_user(db_session, email="leaving@example.com")
    note_id = str(uuid4())
    await db_session.execute(
        text("INSERT INTO cancellation_notes (id, user_id, message) VALUES (:id, :u, 'Too pricey')"),
        {"id": note_id, "u": user["id"]},
    )
    await db_session.commit()
    from src.sql_statements import split_sql_statements

    safety = MIGRATION.with_name("20261118_0001_feedback_delivery_safety.sql")
    for _ in range(2):
        for statement in split_sql_statements(MIGRATION.read_text() + safety.read_text()):
            if statement.strip():
                await db_session.execute(text(statement))
        await db_session.commit()
    rows = (await db_session.execute(
        text("SELECT email, category, message, status FROM feedback_submissions WHERE id = :id"), {"id": note_id})).all()
    assert [tuple(r) for r in rows] == [("leaving@example.com", "cancellation", "Too pricey", "resolved")]
    assert await deliveries(db_session, submission_id=note_id) == []


async def test_slow_send_releases_the_only_database_connection(
    client, db_session, clean, no_immediate, monkeypatch, test_database_url
):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    await _one_submission(client, db_session)
    engine = create_async_engine(test_database_url, pool_size=1, max_overflow=0, pool_timeout=0.2)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_send(*args):
        entered.set()
        await release.wait()
        return delivery.SendResult(True, 204, None, False)

    monkeypatch.setattr(delivery, "send", slow_send)
    try:
        async with sessions() as sender:
            task = asyncio.create_task(delivery.deliver_due(sender))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                async with sessions() as unrelated:
                    assert (await unrelated.execute(text("SELECT 1"))).scalar() == 1
            finally:
                release.set()
                await task
    finally:
        await engine.dispose()


async def test_only_the_delivery_about_to_send_is_leased(
    client, db_session, clean, no_immediate, monkeypatch, initialized_database
):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    await _one_submission(client, db_session)
    await _one_submission(client, db_session)
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)

    async def check_leases(*args):
        async with sessions() as observer:
            assert (await observer.execute(text(
                "SELECT count(*) FROM feedback_webhook_deliveries WHERE lease_token IS NOT NULL"
            ))).scalar() == 1
        return delivery.SendResult(True, 204, None, False)

    monkeypatch.setattr(delivery, "send", check_leases)
    assert await delivery.deliver_due(db_session) == {"delivered": 3}


async def test_a_claim_never_leases_more_than_its_limit(client, db_session, clean, no_immediate):
    user = await create_user(db_session)
    await make_webhook(db_session)
    for _ in range(3):
        await submit(client, user["id"])
    # Picking the rows in an IN subquery leased all three when the planner chose an
    # unmaterialized nested loop: the LIMIT subquery reran per row and skipped the rows
    # already updated. Steer toward that plan (statistics still decide, so this catches
    # the old query most runs, not every run); distinct due times keep the scan in order.
    for _ in range(3):
        for setting in ("enable_hashjoin", "enable_mergejoin", "enable_material", "enable_hashagg",
                        "enable_indexscan", "enable_bitmapscan"):
            await db_session.execute(text(f"SET LOCAL {setting} = off"))
        claimed = await delivery._claim(db_session, submission_id=None, limit=1)
        leased = (await db_session.execute(text(
            "SELECT count(*) FROM feedback_webhook_deliveries WHERE lease_token IS NOT NULL"
        ))).scalar()
        assert (len(claimed), leased) == (1, 1)
        await db_session.execute(text(
            "UPDATE feedback_webhook_deliveries SET lease_token = NULL, lease_expires_at = NULL, attempts = 0"
        ))
        await db_session.commit()


async def test_expired_sender_cannot_overwrite_newer_result(client, db_session, clean, no_immediate):
    await _one_submission(client, db_session)
    [old] = await delivery._claim(db_session, submission_id=None, limit=1)
    await db_session.execute(text("UPDATE feedback_webhook_deliveries SET lease_expires_at = NOW() - INTERVAL '1 second'"))
    await db_session.commit()
    [new] = await delivery._claim(db_session, submission_id=None, limit=1)
    assert old["lease_token"] != new["lease_token"]
    assert await delivery.record_attempt(db_session, new, delivery.SendResult(True, 204, None, False)) == "delivered"
    assert await delivery.record_attempt(db_session, old, delivery.SendResult(False, 500, "HTTP 500", True)) == "stale"
    [row] = await deliveries(db_session)
    assert (row["status"], row["attempts"], row["last_status_code"]) == ("delivered", 2, 204)
    assert (await db_session.execute(text("SELECT count(*) FROM feedback_webhook_attempts"))).scalar() == 1


async def test_disable_and_retry_fences_inflight_sender(client, db_session, clean, no_immediate):
    _, hook, _ = await _one_submission(client, db_session)
    [old] = await delivery._claim(db_session, submission_id=None, limit=1)
    await webhooks.update_webhook(db_session, hook["id"], admin_id=None, enabled=False)
    assert await delivery.retry_delivery(db_session, old["id"])
    await webhooks.update_webhook(db_session, hook["id"], admin_id=None, enabled=True)
    [new] = await delivery._claim(db_session, submission_id=None, limit=1)
    assert old["attempts"] == new["attempts"] == 1  # attempt number alone cannot fence retries
    assert await delivery.record_attempt(db_session, old, delivery.SendResult(True, 204, None, False)) == "stale"
    assert await delivery.record_attempt(db_session, new, delivery.SendResult(False, 400, "HTTP 400", False)) == "failed"


async def test_sender_exceptions_exhaust_retries_without_secret_leaks(
    client, db_session, clean, no_immediate, monkeypatch, caplog
):
    await _one_submission(client, db_session)

    async def broken(*args):
        raise ValueError(DISCORD)

    monkeypatch.setattr(delivery, "load_destination", broken)
    for _ in range(delivery.MAX_ATTEMPTS):
        await make_due(db_session)
        await delivery.deliver_due(db_session)
    [row] = await deliveries(db_session)
    assert (row["status"], row["attempts"]) == ("failed", delivery.MAX_ATTEMPTS)
    assert row["last_error"] == "sender error (ValueError)"
    assert DISCORD not in caplog.text


async def test_expired_last_attempt_and_test_are_recovered(client, db_session, clean, no_immediate):
    _, hook, _ = await _one_submission(client, db_session)
    await db_session.execute(text(
        "UPDATE feedback_webhook_deliveries SET attempts = :max, lease_expires_at = NOW() - INTERVAL '1 second'"
    ), {"max": delivery.MAX_ATTEMPTS})
    await db_session.execute(text(
        "INSERT INTO feedback_webhook_deliveries (id, webhook_id, is_test, attempts, lease_expires_at) "
        "VALUES (:id, :hook, true, 1, NOW() - INTERVAL '1 second')"
    ), {"id": str(uuid4()), "hook": hook["id"]})
    await db_session.commit()
    assert await delivery.deliver_due(db_session) == {}
    rows = await deliveries(db_session)
    assert len(rows) == 2 and all(row["status"] == "failed" for row in rows)


async def test_concurrent_submissions_enforce_limit(db_session, clean, initialized_database):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from src.feedback.service import FeedbackRateLimited, SubmissionInput, submit_feedback

    user = await create_user(db_session)
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)

    async def submit_one():
        async with sessions() as session:
            try:
                await submit_feedback(session, SubmissionInput(category="bug", message="Hello", user_id=user["id"]))
                return True
            except FeedbackRateLimited:
                return False

    outcomes = await asyncio.gather(*(submit_one() for _ in range(15)))
    assert sum(outcomes) == 10


async def test_deleted_user_cannot_bypass_rate_limit(client, db_session, clean, no_immediate):
    user = await create_user(db_session)
    await db_session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user["id"]})
    await db_session.commit()
    assert (await submit(client, user["id"])).status_code == 401
    assert (await db_session.execute(text("SELECT count(*) FROM feedback_submissions"))).scalar() == 0


async def test_anonymization_is_atomic_and_rolls_back_with_deletion(client, db_session, clean, no_immediate):
    user = await create_user(db_session, email="private@example.com")
    submission_id = (await submit(client, user["id"])).json()["id"]
    await db_session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user["id"]})
    row = (await db_session.execute(text("SELECT user_id, email FROM feedback_submissions WHERE id = :id"),
                                    {"id": submission_id})).one()
    assert tuple(row) == (None, None)
    await db_session.rollback()
    row = (await db_session.execute(text("SELECT user_id, email FROM feedback_submissions WHERE id = :id"),
                                    {"id": submission_id})).one()
    assert tuple(row) == (user["id"], "private@example.com")


async def test_two_api_slots_import_only_one_destination(db_session, clean, app, initialized_database):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    config = app.state.config
    config.discord_feedback_webhook_url = DISCORD
    config.discord_sales_webhook_url = None
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)

    async def import_one():
        async with sessions() as session:
            return await webhooks.import_env_webhooks(session, config)

    results = await asyncio.gather(import_one(), import_one())
    assert sorted(map(len, results)) == [0, 1]
    assert len(await webhooks.list_webhooks(db_session)) == 1


@pytest.mark.parametrize("body", [
    {"kind": "json", "url": DISCORD, "categories": ["bug"]},
    {"name": "Hook", "kind": "json", "url": DISCORD},
    {"name": "Hook", "kind": "json", "url": {"secret": DISCORD}, "categories": ["bug"]},
])
async def test_invalid_webhook_bodies_never_echo_secrets(client, clean, admin, auth_headers, caplog, body):
    response = await client.post("/admin/feedback-webhooks", json=body, headers=auth_headers)
    assert response.status_code == 422
    assert "SECRET-DISCORD-TOKEN" not in response.text + caplog.text


async def test_invalid_webhook_json_never_echoes_secrets(client, clean, admin, auth_headers, caplog):
    response = await client.post("/admin/feedback-webhooks", content='{"url":"' + DISCORD + '" BROKEN}',
                                headers={**auth_headers, "content-type": "application/json"})
    assert response.status_code == 422
    assert "SECRET-DISCORD-TOKEN" not in response.text + caplog.text


async def test_disabling_waits_for_uncommitted_fanout(db_session, clean, initialized_database):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from src.feedback.service import SubmissionInput, submit_feedback

    user = await create_user(db_session)
    hook, _ = await make_webhook(db_session)
    await db_session.commit()
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)
    ready, release = asyncio.Event(), asyncio.Event()
    async with sessions() as sender, sessions() as disabler:
        commit = sender.commit

        async def pause_before_commit():
            ready.set()
            await release.wait()
            await commit()

        sender.commit = pause_before_commit
        submission = asyncio.create_task(submit_feedback(sender, SubmissionInput(
            category="bug", message="Race", user_id=user["id"]
        )))
        await asyncio.wait_for(ready.wait(), 2)
        disable = asyncio.create_task(webhooks.update_webhook(disabler, hook["id"], admin_id=None, enabled=False))
        try:
            await asyncio.sleep(0.1)
            assert not disable.done(), "Disable must wait until fan-out commits"
        finally:
            release.set()
            await submission
            await disable
    await webhooks.update_webhook(db_session, hook["id"], admin_id=None, enabled=True)
    [row] = await deliveries(db_session)
    assert row["status"] == "cancelled"


async def test_deletion_waits_for_submission_and_scrubs_snapshot(db_session, clean, initialized_database):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from src.feedback.service import SubmissionInput, submit_feedback

    user = await create_user(db_session)
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)
    ready, release = asyncio.Event(), asyncio.Event()
    async with sessions() as sender, sessions() as deleter:
        commit = sender.commit

        async def pause_before_commit():
            ready.set()
            await release.wait()
            await commit()

        async def delete_user():
            await deleter.execute(text("DELETE FROM users WHERE id = :id"), {"id": user["id"]})
            await deleter.commit()

        sender.commit = pause_before_commit
        submission = asyncio.create_task(submit_feedback(sender, SubmissionInput(
            category="bug", message="Race", user_id=user["id"]
        )))
        await asyncio.wait_for(ready.wait(), 2)
        deletion = asyncio.create_task(delete_user())
        try:
            await asyncio.sleep(0.1)
            assert not deletion.done()
        finally:
            release.set()
            result = await submission
            await deletion
    row = (await db_session.execute(text("SELECT user_id, email FROM feedback_submissions WHERE id = :id"),
                                    {"id": result["id"]})).one()
    assert tuple(row) == (None, None)


# --- Hermes assistant (server-configured) ----------------------------------------------

HERMES_URL = "http://127.0.0.1:8644/p/supoclip/webhooks/feedback-supoclip"
HERMES_SECRET = "HERMES-ROUTE-SECRET"


@pytest.fixture
def hermes(monkeypatch):
    """The gateway: answers each send with the next (status, body) in ``hermes.answers``."""

    class Gateway:
        answers: list[tuple[int, dict]] = []
        calls: list[dict] = []

    async def post_configured(url, body, headers):
        Gateway.calls.append({"url": url, "raw": body, "body": json.loads(body), "headers": headers})
        status, answer = Gateway.answers.pop(0) if Gateway.answers else (202, {"status": "accepted"})
        return httpx.Response(status), json.dumps(answer).encode()

    Gateway.answers, Gateway.calls = [], []
    monkeypatch.setattr(delivery, "post_configured", post_configured)
    return Gateway


def hermes_config(app, url=HERMES_URL, secret=HERMES_SECRET):
    config = app.state.config
    config.hermes_feedback_webhook_url = url
    config.hermes_feedback_webhook_secret = secret
    return config


async def hermes_row(db_session):
    return (await db_session.execute(text("SELECT * FROM feedback_webhooks WHERE kind = 'hermes'"))).mappings().first()


async def test_without_the_hermes_env_there_is_no_row_and_nothing_is_sent(client, db_session, clean, app, hermes, no_immediate):
    for url, secret in ((None, None), (HERMES_URL, None), (None, HERMES_SECRET), ("", "")):
        assert await webhooks.sync_hermes_webhook(db_session, hermes_config(app, url, secret)) == "absent"
    user = await create_user(db_session)
    assert (await submit(client, user["id"])).status_code in (200, 201)
    assert await hermes_row(db_session) is None
    assert await deliveries(db_session) == []
    await delivery.deliver_due(db_session)
    assert hermes.calls == []

    # Unsetting the variables later removes the destination.
    assert await webhooks.sync_hermes_webhook(db_session, hermes_config(app)) == "created"
    assert await webhooks.sync_hermes_webhook(db_session, hermes_config(app, None, None)) == "removed"
    assert await hermes_row(db_session) is None


async def test_hermes_receives_every_category_signed_and_filed(client, db_session, clean, app, hermes, no_immediate):
    assert await webhooks.sync_hermes_webhook(db_session, hermes_config(app)) == "created"
    row = await hermes_row(db_session)
    # Neither value is stored, and no categories, so an older release never queues for it.
    assert row["enabled"] and row["categories"] == [] and row["url_hint"] == "" and row["secret_encrypted"] is None
    [listed] = await webhooks.list_webhooks(db_session)
    assert listed["categories"] == ["bug", "feature", "general", "sales", "cancellation"]

    user = await create_user(db_session)
    for category in ("bug", "sales", "cancellation"):
        assert (await submit(client, user["id"], category=category)).status_code in (200, 201)
    assert await delivery.deliver_due(db_session) == {"delivered": 3}
    assert {call["body"]["submission"]["category"] for call in hermes.calls} == {"bug", "sales", "cancellation"}
    call = hermes.calls[0]
    assert call["url"] == HERMES_URL
    [stored] = await deliveries(db_session, submission_id=call["body"]["submission"]["id"])
    assert call["body"]["delivery_id"] == stored["id"] == call["headers"]["X-Request-ID"]
    assert set(call["body"]) == {"type", "version", "delivery_id", "submission"} and call["body"]["type"] == "supoclip.feedback"
    assert set(call["body"]["submission"]) == {
        "id", "category", "message", "user_id", "email", "page_url", "platform", "app_version", "created_at"}
    assert call["body"]["submission"]["email"] == user["email"] and call["body"]["submission"]["platform"] == "ios"
    timestamp = call["headers"]["X-Webhook-Timestamp"]
    assert call["headers"]["X-Webhook-Signature-V2"] == hmac.new(
        HERMES_SECRET.encode(), f"{timestamp}.".encode() + call["raw"], hashlib.sha256).hexdigest()


async def test_an_ignored_hermes_delivery_is_retried_with_the_same_id(client, db_session, clean, app, hermes, no_immediate):
    await webhooks.sync_hermes_webhook(db_session, hermes_config(app))
    user = await create_user(db_session)
    submission_id = (await submit(client, user["id"])).json()["id"]
    hermes.answers = [(200, {"status": "ignored", "reason": "script"}), (202, {"status": "accepted"})]

    assert await delivery.deliver_due(db_session) == {"pending": 1}
    [row] = await deliveries(db_session, submission_id=submission_id)
    assert (row["status"], row["last_status_code"], row["last_error"]) == ("pending", 200, "HTTP 200 ignored")
    await make_due(db_session)
    assert await delivery.deliver_due(db_session) == {"delivered": 1}
    assert hermes.calls[0]["body"]["delivery_id"] == hermes.calls[1]["body"]["delivery_id"] == row["id"]


async def test_admins_can_only_switch_test_and_read_the_hermes_webhook(
    client, db_session, clean, app, admin, auth_headers, hermes, no_immediate
):
    await webhooks.sync_hermes_webhook(db_session, hermes_config(app))
    listed = await client.get("/admin/feedback-webhooks", headers=auth_headers)
    [webhook] = [w for w in listed.json()["webhooks"] if w["kind"] == "hermes"]
    assert webhook["name"] == "Hermes assistant" and webhook["url_hint"] == ""
    assert "8644" not in listed.text and HERMES_SECRET not in listed.text
    hook = f"/admin/feedback-webhooks/{webhook['id']}"

    for change in ({"name": "Mine"}, {"url": "https://hooks.example.com/x"}, {"categories": ["bug"]}):
        response = await client.patch(hook, json=change, headers=auth_headers)
        assert response.status_code == 422 and response.json()["detail"]["code"] == "webhook_managed", change
    assert (await client.delete(hook, headers=auth_headers)).status_code == 409
    assert (await client.post(f"{hook}/rotate-secret", headers=auth_headers)).status_code == 404
    created = await client.post("/admin/feedback-webhooks", headers=auth_headers,
                                json={"name": "x", "kind": "hermes", "url": HERMES_URL, "categories": ["bug"]})
    assert created.status_code == 422

    tested = await client.post(f"{hook}/test", headers=auth_headers)
    assert tested.status_code == 200 and tested.json()["delivery"]["status"] == "delivered"
    sample = hermes.calls[-1]["body"]
    assert sample["submission"]["category"] == "test" and sample["submission"]["message"] == "Test from SupoClip admin"
    assert sample["delivery_id"] == tested.json()["delivery"]["id"]
    log = (await client.get(f"{hook}/deliveries", headers=auth_headers)).json()["deliveries"]
    assert log[0]["is_test"] and log[0]["status"] == "delivered"

    # Off stays off across restarts; the URL and secret follow the environment.
    assert (await client.patch(hook, json={"enabled": False}, headers=auth_headers)).status_code == 200
    assert await webhooks.sync_hermes_webhook(db_session, hermes_config(app, secret="ROTATED")) == "updated"
    destination = await webhooks.load_destination(db_session, webhook["id"])
    assert destination["enabled"] is False and destination["secret"] == "ROTATED" and destination["url"] == HERMES_URL
    user = await create_user(db_session)
    await submit(client, user["id"])
    assert [d for d in await deliveries(db_session) if not d["is_test"]] == []


async def test_a_failed_hermes_test_is_reported(client, db_session, clean, app, admin, auth_headers, hermes):
    await webhooks.sync_hermes_webhook(db_session, hermes_config(app))
    row = await hermes_row(db_session)
    hermes.answers = [(200, {"status": "ignored"})]
    tested = (await client.post(f"/admin/feedback-webhooks/{row['id']}/test", headers=auth_headers)).json()["delivery"]
    assert tested["status"] == "failed" and tested["error"] == "HTTP 200 ignored"


async def test_admin_entered_webhooks_still_cannot_point_at_the_gateway(client, db_session, clean, app, admin, auth_headers):
    await webhooks.sync_hermes_webhook(db_session, hermes_config(app))
    for url in (HERMES_URL, "https://127.0.0.1:8644/p/supoclip/webhooks/feedback-supoclip"):
        response = await client.post("/admin/feedback-webhooks", headers=auth_headers,
                                      json={"name": "x", "kind": "json", "url": url, "categories": ["bug"]})
        assert response.status_code == 422 and response.json()["detail"]["code"] in ("url_not_https", "url_private_address")
    hook = (await make_webhook(db_session, kind="json", url=JSON_URL))[0]
    response = await client.patch(f"/admin/feedback-webhooks/{hook['id']}", headers=auth_headers,
                                  json={"url": "https://127.0.0.1:8644/p/supoclip/webhooks/feedback-supoclip"})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "url_private_address"


async def test_older_releases_cannot_queue_for_or_redirect_hermes(client, db_session, clean, app, hermes, no_immediate):
    """During a release switch the previous API shares the database with this one."""
    await webhooks.sync_hermes_webhook(db_session, hermes_config(app))
    row = await hermes_row(db_session)
    # The previous release's fan-out query matches on categories only.
    previous_fanout = await db_session.execute(
        text("SELECT id FROM feedback_webhooks w WHERE w.enabled AND :category = ANY(w.categories)"), {"category": "bug"})
    assert previous_fanout.all() == []
    # Its admin API could still rewrite the row's URL: the sender never reads it.
    await db_session.execute(
        text("UPDATE feedback_webhooks SET url_encrypted = :url, secret_encrypted = :secret WHERE id = :id"),
        {"id": row["id"], "url": encrypt_setting_value("https://rebind.example.com/x"), "secret": encrypt_setting_value("x")})
    await db_session.commit()
    user = await create_user(db_session)
    await submit(client, user["id"])
    assert await delivery.deliver_due(db_session) == {"delivered": 1}
    assert hermes.calls[0]["url"] == HERMES_URL
    timestamp = hermes.calls[0]["headers"]["X-Webhook-Timestamp"]
    assert hermes.calls[0]["headers"]["X-Webhook-Signature-V2"] == hmac.new(
        HERMES_SECRET.encode(), f"{timestamp}.".encode() + hermes.calls[0]["raw"], hashlib.sha256).hexdigest()


async def test_a_sender_without_the_hermes_env_leaves_it_for_one_that_has_it(client, db_session, clean, app, hermes, no_immediate):
    await webhooks.sync_hermes_webhook(db_session, hermes_config(app))
    user = await create_user(db_session)
    submission_id = (await submit(client, user["id"])).json()["id"]
    hermes_config(app, None, None)  # say, a cron worker started without the variables
    assert await delivery.deliver_due(db_session) == {"pending": 1}
    [row] = await deliveries(db_session, submission_id=submission_id)
    assert row["last_error"] == "not configured on this server" and hermes.calls == []
    hermes_config(app)
    await make_due(db_session)
    assert await delivery.deliver_due(db_session) == {"delivered": 1}


async def test_hermes_url_must_be_http(db_session, clean, app):
    with pytest.raises(webhooks.InvalidWebhook):
        await webhooks.sync_hermes_webhook(db_session, hermes_config(app, url="file:///etc/passwd"))
    assert await hermes_row(db_session) is None
