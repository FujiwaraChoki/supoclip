from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services import email_service
from src.services.email_service import EmailContent, SesEmailService


@pytest.fixture
def sender(monkeypatch):
    service = SesEmailService(SimpleNamespace(
        aws_region="us-east-1", aws_access_key_id="test", aws_secret_access_key="test",
        ses_from_email="SupoClip <support@example.com>",
    ))
    service._send = MagicMock(return_value={"MessageId": "ses-message-1"})
    db = MagicMock()
    db.commit = AsyncMock()
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=db)
    context.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(email_service, "get_session_maker", lambda: lambda: context)
    return service, db


async def test_ses_id_persisted_with_recipient_without_body_or_tokens(sender):
    service, db = sender
    content = EmailContent("Verify your email", "secret-token-in-html", "secret-token-in-text")
    result = await service.send_email("user@example.com", content)
    record = db.add.call_args.args[0]
    assert result["MessageId"] == record.message_id == "ses-message-1"
    assert record.recipient == "user@example.com"
    assert record.region == "us-east-1"
    assert record.subject == content.subject
    assert "secret-token" not in repr(vars(record))
    db.commit.assert_awaited_once()


async def test_audit_failure_logs_mapping_without_retry_or_secret(sender, caplog):
    service, db = sender
    db.commit.side_effect = RuntimeError("database unavailable")
    result = await service.send_email("user@example.com", EmailContent("Security notice", "secret-html", "secret-text"))
    assert result["MessageId"] == "ses-message-1"
    service._send.assert_called_once()
    assert "ses-message-1" in caplog.text
    assert "user@example.com" in caplog.text
    assert "secret-html" not in caplog.text
    assert "secret-text" not in caplog.text


async def test_rejected_send_has_no_acceptance_record(sender):
    service, db = sender
    service._send.side_effect = RuntimeError("rejected")
    with pytest.raises(RuntimeError, match="rejected"):
        await service.send_email("user@example.com", EmailContent("Subject", "html", "text"))
    db.add.assert_not_called()
