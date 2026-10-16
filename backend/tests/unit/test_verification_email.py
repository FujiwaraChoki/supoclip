from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from src.api.routes import account
from src.database import get_db
from src.services.verification_email_service import VerificationEmailService


@pytest.fixture
async def verification_client(monkeypatch):
    user = SimpleNamespace(id="user-1", email="owner@example.com", emailVerified=False, first_name="<Ada>", name="Ada")
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    send = AsyncMock(return_value={"MessageId": "m-1"})
    monkeypatch.setattr(account, "get_authenticated_user_id", lambda request, config: user.id)
    monkeypatch.setattr(VerificationEmailService, "send_verification_email", send)
    app = FastAPI()
    app.include_router(account.router)
    app.dependency_overrides[get_db] = lambda: db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http, send, user, result


async def test_verification_delivers_to_database_user_not_body_recipient(verification_client):
    http, send, user, _ = verification_client
    url = "https://app.example.com/api/auth/verify-email?token=test"
    response = await http.post("/account/verification-email", json={"url": url, "email": "victim@example.com"})
    assert response.status_code == 200
    send.assert_awaited_once_with(user, url)


@pytest.mark.parametrize("url", ["javascript:alert(1)", "/relative", "ftp://example.com/token"])
async def test_invalid_verification_url_is_rejected(verification_client, url):
    http, send, _, _ = verification_client
    assert (await http.post("/account/verification-email", json={"url": url})).status_code == 422
    send.assert_not_awaited()


async def test_verified_user_does_not_receive_another_email(verification_client):
    http, send, user, _ = verification_client
    user.emailVerified = True
    assert (await http.post("/account/verification-email", json={"url": "https://example.com/verify"})).status_code == 200
    send.assert_not_awaited()


@pytest.mark.parametrize("error,status", [(RuntimeError("SES is not configured"), 503), (ValueError("SES failed"), 502)])
async def test_delivery_failure_is_visible(verification_client, error, status):
    http, send, _, _ = verification_client
    send.side_effect = error
    response = await http.post("/account/verification-email", json={"url": "https://example.com/verify"})
    assert response.status_code == status


async def test_missing_verification_user(verification_client):
    http, send, _, result = verification_client
    result.scalar_one_or_none.return_value = None
    assert (await http.post("/account/verification-email", json={"url": "https://example.com/verify"})).status_code == 404
    send.assert_not_awaited()


async def test_template_escapes_html_and_preserves_link():
    service = VerificationEmailService(SimpleNamespace(aws_region="us-east-1", aws_access_key_id="test", aws_secret_access_key="test", ses_from_email="support@example.com"))
    service.email_service.send_email = AsyncMock()
    user = SimpleNamespace(email="owner@example.com", first_name="<Ada>", name="Ada")
    url = 'https://example.com/verify?token=test&x="quoted"'
    await service.send_verification_email(user, url)
    recipient, content = service.email_service.send_email.call_args.args
    assert recipient == user.email
    assert "&lt;Ada&gt;" in content.html
    assert "&quot;quoted&quot;" in content.html
    assert url in content.text
