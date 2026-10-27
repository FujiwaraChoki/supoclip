from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from src.api.routes import billing
from src.database import get_db
from src.repositories.email_preference_repository import EmailPreferenceRepository
from src.services.email_service import PRODUCT_UPDATE, TRANSACTIONAL, SesEmailService
from src.services.subscription_email_service import SubscriptionEmailService


def configured_service():
    return SubscriptionEmailService(SimpleNamespace(
        aws_region="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
        ses_from_email="support@example.com",
        app_base_url="https://app.example.com",
        backend_auth_secret="test-email-secret",
    ))


@pytest.mark.parametrize("opted_out", [False, True])
async def test_cancellation_email_respects_preferences_and_shows_access_end_in_utc(monkeypatch, opted_out):
    service = configured_service()
    send = AsyncMock(return_value={"MessageId": "m-1"})
    monkeypatch.setattr(SesEmailService, "send_email", send)
    user = SimpleNamespace(
        id="user-1",
        email="owner@example.com", first_name="<Ada>", name="Ada Lovelace",
        subscription_cancel_at=datetime(2026, 10, 31, 23, 30, tzinfo=timezone(timedelta(hours=-5))),
    )
    recipient_lookup = AsyncMock(return_value={
        "id": user.id, "email": user.email, "first_name": user.first_name, "name": user.name,
        "product_updates_unsubscribed_at": datetime.now(timezone.utc) if opted_out else None,
    })
    monkeypatch.setattr(EmailPreferenceRepository, "get_product_update_recipient", recipient_lookup)
    db = MagicMock()

    await service.send_cancellation_scheduled_email(user, db)

    recipient_lookup.assert_awaited_once_with(db, user.id)
    send.assert_awaited_once()
    recipient, content = send.call_args.args
    assert recipient == "owner@example.com"
    assert content.subject == "A quick question about your SupoClip experience"
    assert content.reply_to == ("support@supoclip.com",)
    assert "&lt;Ada&gt;" in content.html
    assert "Hey <Ada>" in content.text
    for body in (content.html, content.text):
        assert "Just hit reply" in body
        assert "November 01, 2026 at 04:30 UTC" in body
        assert "Settings" in body
        assert "Team SupoClip" in body
        assert "has been canceled" not in body
        if opted_out:
            assert "30% off" not in body
            assert "Unsubscribe" not in body
        else:
            assert "30% off for 3 months" in body
            assert "Just reply to this email" in body
            assert "/email/product-updates/unsubscribe?token=" in body
            assert "{{first_name}}" not in body
    if opted_out:
        assert content.category == TRANSACTIONAL
        assert content.headers == ()
    else:
        assert content.category == PRODUCT_UPDATE
        assert dict(content.headers)["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


async def test_cancellation_email_requires_a_date_before_sending():
    service = configured_service()
    service.email_service.send_email = AsyncMock()
    user = SimpleNamespace(subscription_cancel_at=None)

    with pytest.raises(ValueError, match="scheduled cancellation date"):
        await service.send_cancellation_scheduled_email(user, MagicMock())

    service.email_service.send_email.assert_not_awaited()


@pytest.fixture
async def cancellation_client(monkeypatch):
    user = SimpleNamespace(
        id="user-1", email="owner@example.com", first_name="Ada", name="Ada",
        subscription_cancel_at=datetime(2026, 10, 31, tzinfo=timezone.utc),
        subscription_provider="stripe", subscription_status="active",
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    send = AsyncMock(return_value={"MessageId": "m-1"})
    monkeypatch.setattr(billing, "get_authenticated_user_id", lambda request, config: user.id)
    monkeypatch.setattr(SubscriptionEmailService, "send_cancellation_scheduled_email", send)
    app = FastAPI()
    app.include_router(billing.router)
    app.dependency_overrides[get_db] = lambda: db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http, send, user, db


async def test_sends_scheduled_email_to_database_user(cancellation_client):
    http, send, user, db = cancellation_client
    response = await http.post("/billing/subscription-email", json={
        "event": "cancellation_scheduled", "email": "other@example.com",
    })

    assert response.status_code == 200
    send.assert_awaited_once_with(user, db)


@pytest.mark.parametrize("change", [
    {"subscription_cancel_at": None},
    {"subscription_status": "canceled"},
    {"subscription_provider": "apple"},
    {"subscription_provider": "affiliate"},
])
async def test_skips_when_no_current_stripe_cancellation(cancellation_client, change):
    http, send, user, _ = cancellation_client
    for key, value in change.items():
        setattr(user, key, value)

    response = await http.post("/billing/subscription-email", json={"event": "cancellation_scheduled"})

    assert response.status_code == 200
    assert response.json()["status"] == "skipped"
    send.assert_not_awaited()


@pytest.mark.parametrize("error,status", [
    (RuntimeError("SES is not configured"), 503),
    (ValueError("Delivery failed"), 502),
])
async def test_delivery_failure_is_visible_for_webhook_retry(cancellation_client, error, status):
    http, send, _, _ = cancellation_client
    send.side_effect = error

    response = await http.post("/billing/subscription-email", json={"event": "cancellation_scheduled"})

    assert response.status_code == status
