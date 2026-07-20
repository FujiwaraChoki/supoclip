from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.api_key_email_service import ApiKeyEmailService


def configured_service() -> ApiKeyEmailService:
    config = SimpleNamespace(
        aws_region="eu-central-1",
        aws_access_key_id="access-key",
        aws_secret_access_key="secret-key",
        ses_from_email="SupoClip <noreply@example.com>",
    )
    return ApiKeyEmailService(config)


def test_created_email_contains_metadata_but_not_secret():
    service = configured_service()
    user = SimpleNamespace(
        email="user@example.com", first_name="Ada", name="Ada Lovelace"
    )
    api_key = {
        "name": "Automation",
        "key_prefix": "sk_abc12345",
        "key": "sk_abc12345_super-secret-value",
    }

    content = service._build_created_email(user, api_key)

    assert content.subject == "A new SupoClip API key was created"
    assert "Automation" in content.html
    assert "sk_abc12345" in content.text
    assert api_key["key"] not in content.html
    assert api_key["key"] not in content.text


def test_created_email_escapes_user_controlled_html():
    service = configured_service()
    user = SimpleNamespace(
        email="user@example.com", first_name="<Ada>", name="Ada Lovelace"
    )

    content = service._build_created_email(
        user, {"name": "<script>alert(1)</script>", "key_prefix": "sk_safe"}
    )

    assert "<script>" not in content.html
    assert "&lt;script&gt;" in content.html
    assert "&lt;Ada&gt;" in content.html


@pytest.mark.asyncio
async def test_send_created_email_uses_users_email():
    service = configured_service()
    service.email_service.send_email = AsyncMock(return_value={"MessageId": "email-1"})
    user = SimpleNamespace(
        email="user@example.com", first_name=None, name="Ada Lovelace"
    )

    result = await service.send_created_email(
        user, {"name": "Automation", "key_prefix": "sk_abc12345"}
    )

    assert result == {"MessageId": "email-1"}
    service.email_service.send_email.assert_awaited_once()
    assert service.email_service.send_email.await_args.args[0] == "user@example.com"
