"""Cancellation reviews on PostgreSQL: signed webhook -> fold -> investigate -> outbox -> delivery.

Only the model and the chat endpoint are stubbed. The clip is a real file made by
ffmpeg, so measuring and frame extraction run for real.
"""

import hashlib
import hmac
import json
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.cancellation_reviews import runner
from src.cancellation_reviews.delivery import DeliveryResult
from src.cancellation_reviews.judging import (
    CaseAssessment,
    ClipJudgment,
    JudgeUnavailable,
)
from tests.fixtures.factories import create_source, create_task, create_user

ACCOUNT = "acct_testsource"
SECRET = "whsec_integration"
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")


def signed(payload: dict) -> tuple[bytes, dict]:
    body = json.dumps(payload).encode()
    timestamp = int(time.time())
    signature = hmac.new(SECRET.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return body, {"stripe-signature": f"t={timestamp},v1={signature}", "content-type": "application/json"}


def subscription_event(sub_id, customer, *, event_type="customer.subscription.updated", previous=None,
                       created=None, feedback=None, comment=None, **fields):
    sub = {
        "id": sub_id, "object": "subscription", "customer": customer, "status": "active",
        "cancel_at": int((datetime.now(timezone.utc) + timedelta(days=20)).timestamp()),
        "cancel_at_period_end": False, "canceled_at": int(time.time()), "start_date": int(time.time()) - 86400 * 30,
        "cancellation_details": {"reason": "cancellation_requested", "feedback": feedback, "comment": comment},
        "metadata": {}, "items": {"data": [{"price": {"id": "price_x", "unit_amount": 999, "currency": "usd",
                                                       "recurring": {"interval": "month"}}}]},
    }
    sub.update(fields)
    data = {"object": sub}
    if previous is not None:
        data["previous_attributes"] = previous
    return {"id": f"evt_{uuid4().hex}", "object": "event", "type": event_type, "livemode": True,
            "created": created or int(time.time()), "data": data}


@pytest.fixture
def review_config(app, monkeypatch):
    config = app.state.config
    config.stripe_cancellation_webhook_secrets = f"{ACCOUNT}:{SECRET}"
    config.stripe_source_account_id = ACCOUNT
    config.cancellation_reviews_enabled = True
    config.cancellation_review_settle_seconds = 60
    config.cancellation_report_webhook_url = "https://chat.invalid/hook"
    config.cancellation_report_webhook_secret = "route-secret"
    monkeypatch.setattr("src.cancellation_reviews.runner.get_config", lambda: config)
    monkeypatch.setattr("src.api.routes.stripe_cancellations.get_config", lambda: config)
    return config


@pytest.fixture
async def clean_tables(db_session):
    async def wipe():
        for table in ("cancellation_report_deliveries", "cancellation_notes", "cancellation_reviews",
                      "stripe_cancellation_events"):
            await db_session.execute(text(f"DELETE FROM {table}"))
        # Reconciliation would pick up scheduled cancellations seeded by another test.
        await db_session.execute(text("UPDATE users SET subscription_cancel_at = NULL WHERE subscription_cancel_at IS NOT NULL"))
        await db_session.commit()
    await wipe()
    yield
    await wipe()


@pytest.fixture
def stub_model(monkeypatch):
    calls = {"clips": 0, "case": 0, "fail": False}

    async def judge(item):
        calls["clips"] += 1
        if calls["fail"]:
            raise JudgeUnavailable("model API returned HTTP 529")
        assert item.available and len(item.frames) >= 4
        return ClipJudgment(hook="Opens on a question", framing="Centered speaker", captions="Readable",
                            story="Complete", problems=[], strengths=["clear"], score=4, verdict="good",
                            confidence="high")

    async def assess(case):
        calls["case"] += 1
        if calls["fail"]:
            raise JudgeUnavailable("model API returned HTTP 529")
        assert case["customer_words"]["comment"] == "Too pricey for me"
        return CaseAssessment(clip_quality="good", quality_explains_cancellation="unlikely",
                              likely_drivers=["price: the survey answer"], summary="Price, not quality.",
                              follow_up="Consider a cheaper tier.", evidence_gaps=[])

    monkeypatch.setattr(runner, "judge_clip", judge)
    monkeypatch.setattr(runner, "assess_case", assess)
    return calls


@pytest.fixture
def chat(monkeypatch):
    sent = []

    async def deliver(url, secret, delivery_id, message, kind):
        sent.append({"id": delivery_id, "message": message, "kind": kind})
        return DeliveryResult(True, "accepted")

    monkeypatch.setattr(runner, "deliver", deliver)
    return sent


class LocalStorage:
    def object_size(self, reference):
        return None


async def seed_customer(db_session, tmp_path, *, with_clip=True):
    user_id = str(uuid4())
    customer = f"cus_{uuid4().hex[:10]}"
    await create_user(db_session, user_id=user_id, plan="pro", subscription_status="active")
    await db_session.execute(
        text("UPDATE users SET stripe_customer_id = :c, subscription_provider = 'stripe' WHERE id = :u"),
        {"c": customer, "u": user_id},
    )
    source = await create_source(db_session)
    task = await create_task(db_session, user_id=user_id, source_id=source["id"], source_duration_seconds=600)
    if with_clip:
        clip_path = tmp_path / f"{uuid4().hex}.mp4"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=720x1280:rate=30:duration=20",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=20", "-shortest", "-c:v", "libx264",
             "-pix_fmt", "yuv420p", "-c:a", "aac", str(clip_path)],
            check=True,
        )
        await db_session.execute(
            text(
                """
                INSERT INTO generated_clips (id, task_id, filename, file_path, start_time, end_time, duration, text,
                                             relevance_score, reasoning, clip_order, virality_score, hook_title)
                VALUES (:id, :task, 'c.mp4', :path, '00:10', '00:30', 20, 'So here is the point. It works.',
                        0.9, 'r', 1, 72, 'The point that works')
                """
            ),
            {"id": str(uuid4()), "task": task["id"], "path": str(clip_path)},
        )
    await db_session.commit()
    return user_id, customer


async def run_until_idle(monkeypatch, review_config):
    """Run the cron job with the settle window already elapsed."""
    monkeypatch.setattr(runner, "_now", lambda: datetime.now(timezone.utc) + timedelta(minutes=2))
    monkeypatch.setattr("src.media_storage.MediaStorage", LocalStorage)
    return await runner.process_cancellation_reviews({})


async def test_portal_cancellation_is_investigated_and_reported_once(
    client, db_session, review_config, clean_tables, stub_model, chat, monkeypatch, tmp_path
):
    user_id, customer = await seed_customer(db_session, tmp_path)
    sub_id = f"sub_{uuid4().hex[:10]}"
    schedule = subscription_event(sub_id, customer, previous={"cancel_at": None, "canceled_at": None})
    survey = subscription_event(sub_id, customer, previous={"cancellation_details": {"feedback": None}},
                                created=schedule["created"] + 3, feedback="too_expensive", comment="Too pricey for me")

    for payload in (schedule, survey, survey):  # Stripe redelivers the second one
        body, headers = signed(payload)
        response = await client.post(f"/webhooks/stripe/cancellations/{ACCOUNT}", content=body, headers=headers)
        assert response.status_code == 200
    assert response.json() == {"received": True, "stored": False}

    bad_body, bad_headers = signed(schedule)
    bad_headers["stripe-signature"] = bad_headers["stripe-signature"][:-2] + "00"
    assert (await client.post(f"/webhooks/stripe/cancellations/{ACCOUNT}", content=bad_body,
                              headers=bad_headers)).status_code == 400
    assert (await client.post("/webhooks/stripe/cancellations/acct_unknown", content=bad_body,
                              headers=bad_headers)).status_code == 404

    result = await run_until_idle(monkeypatch, review_config)
    assert result["folded"] == 2 and result["reviews"] == ["reported"] and result["delivered"] == 1
    assert stub_model == {"clips": 1, "case": 1, "fail": False}

    report = chat[0]["message"]
    assert "End-of-period cancellation" in report
    assert "Survey answer: too expensive" in report
    assert "“Too pricey for me”" in report
    assert "Clips reviewed: 1 of 1" in report and "Judged 4/5" in report
    assert "720×1280" in report and "LUFS" in report
    assert user_id in report and "@example.com" in report and "s3://" not in report

    again = await run_until_idle(monkeypatch, review_config)
    assert again["reviews"] == [] and again["delivered"] == 0 and len(chat) == 1


async def test_customer_without_clips_or_feedback_and_resume_after_report(
    client, db_session, review_config, clean_tables, stub_model, chat, monkeypatch, tmp_path
):
    _user_id, customer = await seed_customer(db_session, tmp_path, with_clip=False)
    sub_id = f"sub_{uuid4().hex[:10]}"
    monkeypatch.setattr(runner, "assess_case", _assess_no_words)
    for payload in (subscription_event(sub_id, customer, previous={"cancel_at": None}),):
        body, headers = signed(payload)
        await client.post(f"/webhooks/stripe/cancellations/{ACCOUNT}", content=body, headers=headers)
    await run_until_idle(monkeypatch, review_config)
    report = chat[0]["message"]
    assert "none to review" in report and "No reason given" in report
    assert stub_model["clips"] == 0

    resume = subscription_event(sub_id, customer, previous={"cancel_at": 123}, cancel_at=None,
                                created=int(time.time()) + 60)
    body, headers = signed(resume)
    await client.post(f"/webhooks/stripe/cancellations/{ACCOUNT}", content=body, headers=headers)
    await run_until_idle(monkeypatch, review_config)
    assert chat[1]["kind"] == "update" and "withdrawn" in chat[1]["message"]


async def _assess_no_words(case):
    assert case["customer_words"]["comment"] is None
    return CaseAssessment(clip_quality="not_assessed", quality_explains_cancellation="unknown", likely_drivers=[],
                          summary="Nothing to judge.", follow_up="None.", evidence_gaps=["no clips"])


async def test_withdrawn_before_settling_means_no_report(
    client, db_session, review_config, clean_tables, stub_model, chat, monkeypatch, tmp_path
):
    _user_id, customer = await seed_customer(db_session, tmp_path, with_clip=False)
    sub_id = f"sub_{uuid4().hex[:10]}"
    now = int(time.time())
    for payload in (subscription_event(sub_id, customer, previous={"cancel_at": None}, created=now),
                    subscription_event(sub_id, customer, previous={"cancel_at": 1}, cancel_at=None, created=now + 5)):
        body, headers = signed(payload)
        await client.post(f"/webhooks/stripe/cancellations/{ACCOUNT}", content=body, headers=headers)
    result = await run_until_idle(monkeypatch, review_config)
    assert result["reviews"] == [] and chat == []
    status = (await db_session.execute(text("SELECT status FROM cancellation_reviews WHERE subscription_id = :s"),
                                       {"s": sub_id})).scalar()
    assert status == "withdrawn"


async def test_model_outage_retries_then_reports_with_the_gap(
    client, db_session, review_config, clean_tables, stub_model, chat, monkeypatch, tmp_path
):
    _user_id, customer = await seed_customer(db_session, tmp_path)
    stub_model["fail"] = True
    sub_id = f"sub_{uuid4().hex[:10]}"
    body, headers = signed(subscription_event(sub_id, customer,
                                              event_type="customer.subscription.deleted", status="canceled",
                                              cancel_at=None, feedback="low_quality"))
    await client.post(f"/webhooks/stripe/cancellations/{ACCOUNT}", content=body, headers=headers)
    first = await run_until_idle(monkeypatch, review_config)
    assert first["reviews"] == ["retry later"] and chat == []
    await db_session.execute(
        text("UPDATE cancellation_reviews SET attempts = :n, not_before = NOW() WHERE subscription_id = :s"),
        {"n": runner.MAX_ATTEMPTS - 1, "s": sub_id},
    )
    await db_session.commit()
    last = await run_until_idle(monkeypatch, review_config)
    assert last["reviews"] == ["reported"]
    report = chat[0]["message"]
    assert "Immediate cancellation" in report
    assert "Visual judgment unavailable: model API returned HTTP 529" in report
    assert "Assessment unavailable" in report and "failed after" in report
    assert "Measured:" in report  # the measurements still made it


async def test_failed_delivery_is_retried_and_late_note_becomes_an_update(
    client, db_session, review_config, clean_tables, stub_model, monkeypatch, tmp_path
):
    user_id, customer = await seed_customer(db_session, tmp_path, with_clip=False)
    monkeypatch.setattr(runner, "assess_case", _assess_no_words)
    outcomes = iter([DeliveryResult(False, "HTTP 502"), DeliveryResult(True, "accepted"),
                     DeliveryResult(True, "accepted")])
    sent = []

    async def flaky(url, secret, delivery_id, message, kind):
        result = next(outcomes)
        sent.append((delivery_id, kind, result.delivered))
        return result

    monkeypatch.setattr(runner, "deliver", flaky)
    body, headers = signed(subscription_event(f"sub_{uuid4().hex[:10]}", customer, previous={"cancel_at": None}))
    await client.post(f"/webhooks/stripe/cancellations/{ACCOUNT}", content=body, headers=headers)
    await run_until_idle(monkeypatch, review_config)
    assert sent == [(sent[0][0], "report", False)]
    await db_session.execute(text("UPDATE cancellation_report_deliveries SET next_attempt_at = NOW()"))
    await db_session.commit()
    await run_until_idle(monkeypatch, review_config)
    assert sent[1] == (sent[0][0], "report", True)  # same delivery id: the chat side dedupes on it

    from src.cancellation_reviews.store import insert_note
    from src.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        await insert_note(db, user_id, "Clips cut people off mid-sentence")
    await run_until_idle(monkeypatch, review_config)
    assert sent[2][1] == "update"  # the API path that stores notes: test_cancellation_note_is_stored


def _session_headers(user_id):
    timestamp = str(int(time.time()))
    signature = hmac.new(b"test-backend-auth-secret", f"{user_id}:{timestamp}".encode(), hashlib.sha256).hexdigest()
    return {"x-supoclip-user-id": user_id, "x-supoclip-ts": timestamp, "x-supoclip-signature": signature}


async def test_cancellation_note_is_stored(client, db_session, app, clean_tables, monkeypatch):
    user_id = str(uuid4())
    await create_user(db_session, user_id=user_id)
    app.state.config.discord_feedback_webhook_url = "https://discord.invalid/webhook"

    class Response:
        def raise_for_status(self):
            return None

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr("src.api.routes.feedback.httpx.AsyncClient", lambda: Client())
    monkeypatch.setattr("src.api.routes.feedback.get_config", lambda: app.state.config)
    response = await client.post("/feedback", headers={**_session_headers(user_id), "content-type": "application/json"},
                                 content=json.dumps({"category": "cancellation", "message": "Too slow"}))
    assert response.status_code == 200
    stored = (await db_session.execute(text("SELECT message FROM cancellation_notes WHERE user_id = :u"),
                                       {"u": user_id})).scalar()
    assert stored == "Too slow"


async def test_reconciliation_catches_a_cancellation_without_an_event(
    db_session, review_config, clean_tables, stub_model, chat, monkeypatch, tmp_path
):
    user_id, _customer = await seed_customer(db_session, tmp_path, with_clip=False)
    monkeypatch.setattr(runner, "assess_case", _assess_no_words)
    await db_session.execute(
        text("""UPDATE users SET stripe_subscription_id = :s, subscription_cancel_at = NOW() + INTERVAL '9 days',
                "updatedAt" = NOW() - INTERVAL '3 hours' WHERE id = :u"""),
        {"u": user_id, "s": f"sub_missed_{uuid4().hex[:8]}"},
    )
    await db_session.commit()
    result = await run_until_idle(monkeypatch, review_config)
    assert result["reconciled"] == 1 and result["reviews"] == ["reported"]
    assert "survey answer and comment are not available" in chat[0]["message"]


async def test_account_migration_transfer_is_excluded_until_the_customer_cancels(
    client, db_session, review_config, clean_tables, stub_model, chat, monkeypatch, tmp_path
):
    user_id, customer = await seed_customer(db_session, tmp_path, with_clip=False)
    monkeypatch.setattr(runner, "assess_case", _assess_no_words)
    sub_id = f"sub_{uuid4().hex[:10]}"
    created_table = not (await db_session.execute(text("SELECT to_regclass('public.stripe_account_migrations')"))).scalar()
    if created_table:
        await db_session.execute(text(
            """CREATE TABLE stripe_account_migrations (id VARCHAR(255) PRIMARY KEY, user_id VARCHAR(255),
               source_account VARCHAR(255), source_customer VARCHAR(255), source_subscription VARCHAR(255),
               destination_account VARCHAR(255), destination_customer VARCHAR(255),
               destination_subscription VARCHAR(255), stage VARCHAR(32), cancellation_requested BOOLEAN,
               review_reason TEXT)"""))
    await db_session.execute(
        text("""INSERT INTO stripe_account_migrations VALUES (:id, :u, :acct, :cus, :sub, 'acct_dest', 'cus_dest',
                NULL, 'destination_staged', FALSE, NULL)"""),
        {"id": str(uuid4()), "u": user_id, "acct": ACCOUNT, "cus": customer, "sub": sub_id},
    )
    await db_session.commit()
    try:
        transfer = subscription_event(sub_id, customer, previous={"cancel_at_period_end": False},
                                      cancel_at_period_end=True, metadata={"billing_migration_operation": "op-1"})
        body, headers = signed(transfer)
        await client.post(f"/webhooks/stripe/cancellations/{ACCOUNT}", content=body, headers=headers)
        result = await run_until_idle(monkeypatch, review_config)
        assert result["reviews"] == ["excluded (account_migration_transfer)"] and chat == []

        await db_session.execute(text("UPDATE stripe_account_migrations SET cancellation_requested = TRUE "
                                      "WHERE source_subscription = :s"), {"s": sub_id})
        await db_session.commit()
        result = await run_until_idle(monkeypatch, review_config)
        assert result["reviews"] == ["reported"]
        assert "cancelled during the move between Stripe accounts" in chat[0]["message"]
    finally:
        if created_table:
            await db_session.execute(text("DROP TABLE stripe_account_migrations"))
        else:
            await db_session.execute(text("DELETE FROM stripe_account_migrations WHERE source_subscription = :s"),
                                     {"s": sub_id})
        await db_session.commit()


async def test_a_baseline_reopened_by_a_late_event_takes_the_event_account(
    client, db_session, review_config, clean_tables, stub_model, chat, monkeypatch, tmp_path
):
    _user_id, customer = await seed_customer(db_session, tmp_path, with_clip=False)
    monkeypatch.setattr(runner, "assess_case", _assess_no_words)
    sub_id = f"sub_{uuid4().hex[:10]}"
    await db_session.execute(
        text("""INSERT INTO cancellation_reviews (id, account_id, livemode, subscription_id, customer_id, kind, status,
                origin) VALUES (:id, '', TRUE, :s, :c, 'scheduled', 'baseline', 'baseline')"""),
        {"id": str(uuid4()), "s": sub_id, "c": customer},
    )
    await db_session.commit()
    body, headers = signed(subscription_event(sub_id, customer, previous={"cancel_at": None},
                                              created=int(time.time()) - 3600))
    await client.post(f"/webhooks/stripe/cancellations/{ACCOUNT}", content=body, headers=headers)
    result = await run_until_idle(monkeypatch, review_config)
    assert result["reviews"] == ["reported"]
    assert f"account {ACCOUNT}" in chat[0]["message"]
