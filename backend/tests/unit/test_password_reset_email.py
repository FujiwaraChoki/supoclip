from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from src.api.routes import account
from src.database import get_db
from src.services.password_reset_email_service import PasswordResetEmailService


def configured_service() -> PasswordResetEmailService:
    config = SimpleNamespace(
        aws_region="eu-central-1",
        aws_access_key_id="access-key",
        aws_secret_access_key="secret-key",
        ses_from_email="SupoClip <noreply@example.com>",
    )
    return PasswordResetEmailService(config)


def test_reset_email_contains_link_and_escapes_html():
    service = configured_service()
    user = SimpleNamespace(email="user@example.com", first_name="<Ada>", name=None)
    url = 'https://app.example.com/api/auth/reset-password/tok?callbackURL=/reset-password&x="y"'

    content = service._build_reset_email(user, url)

    assert content.subject == "Reset your SupoClip password"
    assert url in content.text
    assert "&lt;Ada&gt;" in content.html
    assert '&quot;y&quot;' in content.html
    assert '"y"' not in content.html


@pytest.fixture
async def client(monkeypatch):
    user = SimpleNamespace(
        id="user-1", email="user@example.com", first_name="Ada", name="Ada"
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    send = AsyncMock(return_value={"MessageId": "m-1"})
    monkeypatch.setattr(
        account, "get_authenticated_user_id", lambda request, config: "user-1"
    )
    monkeypatch.setattr(PasswordResetEmailService, "send_reset_email", send)

    app = FastAPI()
    app.include_router(account.router)
    app.dependency_overrides[get_db] = lambda: db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http, send, user


async def test_sends_reset_email_to_authenticated_user(client):
    http, send, user = client
    url = "https://app.example.com/api/auth/reset-password/tok"

    response = await http.post("/account/password-reset-email", json={"url": url})

    assert response.status_code == 200
    send.assert_awaited_once_with(user, url)


@pytest.mark.parametrize("url", ["javascript:alert(1)", "/relative", "ftp://x/y"])
async def test_rejects_non_http_urls(client, url):
    http, send, _ = client

    response = await http.post("/account/password-reset-email", json={"url": url})

    assert response.status_code == 422
    send.assert_not_awaited()
