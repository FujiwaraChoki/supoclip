from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlsplit

import pytest

from src.services import email_service
from src.services.email_service import EmailContent, SesEmailService
from src.services.product_update_email_service import InvalidUnsubscribeToken, ProductUpdateEmailService


@pytest.fixture
def service():
    config = SimpleNamespace(
        aws_region="us-east-1", aws_access_key_id="test", aws_secret_access_key="test",
        ses_from_email="support@example.com", backend_auth_secret="test-unsubscribe-secret",
        app_base_url="https://app.example.com",
    )
    db = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock())
    service = ProductUpdateEmailService(db, config)
    service._recipient = AsyncMock(return_value={
        "id": "user-1", "email": "owner@example.com", "first_name": "<Ada>",
        "name": "Ada Lovelace", "product_updates_unsubscribed_at": None,
    })
    service._recipient.side_effect = lambda user_id: (
        service._recipient.return_value if service._recipient.return_value
        and user_id == service._recipient.return_value["id"] else None
    )
    service.email_service.send_email = AsyncMock(return_value={"MessageId": "sent-1"})
    return service


def token_for(service, user_id="user-1", email="owner@example.com"):
    return parse_qs(urlsplit(service.unsubscribe_url(user_id, email)).query)["token"][0]


async def test_token_omits_email_and_is_bound_to_recipient_and_address(service):
    token = token_for(service)
    assert "owner" not in token and "@" not in token
    assert (await service.validate_token(token))["id"] == "user-1"
    for changed in [token.replace("user-1", "user-2"), token[:-1], token + "a", "", "v2." + token[3:]]:
        with pytest.raises(InvalidUnsubscribeToken):
            await service.validate_token(changed)
    service.config.backend_auth_secret = "rotated-secret"
    with pytest.raises(InvalidUnsubscribeToken):
        await service.validate_token(token)
    service.config.backend_auth_secret = "test-unsubscribe-secret"
    service._recipient.return_value = {**service._recipient.return_value, "email": "changed@example.com"}
    with pytest.raises(InvalidUnsubscribeToken):
        await service.validate_token(token)
    service._recipient.return_value = None
    with pytest.raises(InvalidUnsubscribeToken):
        await service.validate_token(token)


def test_requires_signing_secret_and_https(service):
    service.config.backend_auth_secret = None
    with pytest.raises(RuntimeError, match="BACKEND_AUTH_SECRET"):
        token_for(service)
    service.config.backend_auth_secret = "secret"
    service.config.app_base_url = "http://app.example.com"
    with pytest.raises(ValueError, match="HTTPS"):
        token_for(service)


async def test_unsubscribed_recipient_never_reaches_ses(service):
    service._recipient.return_value["product_updates_unsubscribed_at"] = datetime.now(timezone.utc)
    result = await service.send_product_update("user-1", EmailContent("News", "News", "News"))
    assert result == {"skipped": True, "reason": "unsubscribed"}
    service.email_service.send_email.assert_not_awaited()


async def test_renders_both_bodies_and_mailbox_unsubscribe_headers(service):
    source = EmailContent("News", '<p>Hi {{first_name}}</p><a href="{{unsubscribe_url}}">Unsubscribe</a>',
                          "Hi {{first_name}}\n{{unsubscribe_url}}",
                          (("List-Unsubscribe", "<https://wrong.example.com>"),))
    await service.send_product_update("user-1", source)
    recipient, content = service.email_service.send_email.call_args.args
    assert recipient == "owner@example.com"
    assert "&lt;Ada&gt;" in content.html and "Hi <Ada>" in content.text
    assert "{{" not in content.html + content.text
    headers = dict(content.headers)
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    url = headers["List-Unsubscribe"][1:-1]
    token = parse_qs(urlsplit(url).query)["token"][0]
    assert (await service.validate_token(token))["id"] == "user-1"
    assert url in content.html and url in content.text
    assert "wrong.example.com" not in str(content)


async def test_adds_missing_footer_inside_html_body_and_text(service):
    await service.send_product_update("user-1", EmailContent("News", "<html><body>News</body></html>", "News"))
    content = service.email_service.send_email.call_args.args[1]
    assert content.html.index("Unsubscribe from product updates") < content.html.index("</body>")
    assert "Unsubscribe from product updates:" in content.text


async def test_refuses_incomplete_draft_or_deleted_recipient(service):
    with pytest.raises(ValueError, match="unresolved"):
        await service.send_product_update("user-1", EmailContent("News", "{{sender_address}}", "News"))
    service.email_service.send_email.assert_not_awaited()
    service._recipient.return_value = None
    with pytest.raises(ValueError, match="not found"):
        await service.send_product_update("user-1", EmailContent("News", "News", "News"))
    service.email_service.send_email.assert_not_awaited()


def test_ses_receives_one_click_headers_only_for_product_mail(service, monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(email_service.boto3, "client", lambda *a, **kw: client)
    sender = SesEmailService(service.config)
    sender._send("owner@example.com", EmailContent("News", "News", "News", (
        ("List-Unsubscribe", "<https://app.example.com/unsubscribe>"),
        ("List-Unsubscribe-Post", "List-Unsubscribe=One-Click"),
    )))
    assert client.send_email.call_args.kwargs["Content"]["Simple"]["Headers"][1] == {
        "Name": "List-Unsubscribe-Post", "Value": "List-Unsubscribe=One-Click",
    }
    sender._send("owner@example.com", EmailContent("Reset password", "Reset", "Reset"))
    assert "Headers" not in client.send_email.call_args.kwargs["Content"]["Simple"]


async def test_fills_subject_and_spaced_fields_without_reparsing_the_name(service):
    service._recipient.return_value["first_name"] = "{{x}}"
    await service.send_product_update("user-1", EmailContent(
        "{{ first_name }}, meet the editor", "<p>Hi {{ first_name }}</p>{{ unsubscribe_url }}",
        "Hi {{first_name}}\n{{ unsubscribe_url }}",
    ))
    content = service.email_service.send_email.call_args.args[1]
    assert content.subject == "{{x}}, meet the editor"
    assert "Hi {{x}}" in content.html and "Hi {{x}}" in content.text
    assert "Unsubscribe from product updates" not in content.html + content.text
    assert content.category == email_service.PRODUCT_UPDATE


async def test_refuses_unresolved_subject_fields(service):
    for subject in ["{{sender_address}} news", "News {{unsubscribe_url}}"]:
        with pytest.raises(ValueError, match="unresolved"):
            await service.send_product_update("user-1", EmailContent(subject, "News", "News"))
    service.email_service.send_email.assert_not_awaited()


async def test_ses_refuses_product_updates_without_unsubscribe_headers(service, monkeypatch):
    created = []
    monkeypatch.setattr(email_service.boto3, "client", lambda *a, **kw: created.append(MagicMock()) or created[-1])
    sender = SesEmailService(service.config)
    with pytest.raises(ValueError, match="ProductUpdateEmailService"):
        await sender.send_email("owner@example.com", EmailContent(
            "News", "News", "News", category=email_service.PRODUCT_UPDATE,
        ))
    assert created == []
    sender._send("a@example.com", EmailContent("One", "One", "One"))
    sender._send("b@example.com", EmailContent("Two", "Two", "Two"))
    assert len(created) == 1 and created[0].send_email.call_count == 2


async def test_public_pages_report_missing_signing_secret_as_unavailable(service, monkeypatch):
    from src.api.routes import email_preferences
    from src.repositories.email_preference_repository import EmailPreferenceRepository

    token = token_for(service)
    service.config.backend_auth_secret = None
    monkeypatch.setattr(email_preferences, "ProductUpdateEmailService",
                        lambda db: ProductUpdateEmailService(db, service.config))
    monkeypatch.setattr(EmailPreferenceRepository, "get_product_update_recipient",
                        AsyncMock(return_value=service._recipient.return_value))
    for route in (email_preferences.confirm_unsubscribe, email_preferences.unsubscribe):
        response = await route(token=token, db=service.db)
        assert response.status_code == 503
        assert response.headers["cache-control"] == "no-store"
