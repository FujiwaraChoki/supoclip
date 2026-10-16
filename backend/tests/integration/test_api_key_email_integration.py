from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from sqlalchemy import select, text

from src.models import EmailDelivery
from src.services.api_key_email_service import ApiKeyEmailService
from src.services.email_service import EmailContent, SesEmailService
from tests.fixtures.factories import create_user


async def test_database_verification_controls_api_key_mint(client, db_session, auth_headers, monkeypatch):
    await create_user(db_session, user_id="user-1")
    monkeypatch.setattr(ApiKeyEmailService, "is_configured", property(lambda self: False))
    response = await client.post("/api-keys/", headers=auth_headers, json={"emailVerified": True})
    assert response.status_code == 403
    assert await db_session.scalar(text("SELECT count(*) FROM api_keys WHERE user_id='user-1'")) == 0

    await db_session.execute(text('UPDATE users SET "emailVerified"=true WHERE id=\'user-1\''))
    await db_session.commit()
    created = await client.post("/api-keys/", headers=auth_headers, json={"name": "Integration"})
    assert created.status_code == 200
    assert created.json()["api_key"]["key"].startswith("sk_")
    key_id = created.json()["api_key"]["id"]

    # Existing key management stays available if verification is later false.
    await db_session.execute(text('UPDATE users SET "emailVerified"=false WHERE id=\'user-1\''))
    await db_session.commit()
    assert (await client.get("/api-keys/", headers=auth_headers)).status_code == 200
    assert (await client.delete(f"/api-keys/{key_id}", headers=auth_headers)).status_code == 200


async def test_ses_acceptance_metadata_is_committed_for_complaint_lookup(db_session):
    service = SesEmailService(SimpleNamespace(
        aws_region="us-east-1", aws_access_key_id="test", aws_secret_access_key="test",
        ses_from_email="support@example.com",
    ))
    message_id = f"ses-{uuid4()}"
    service._send = MagicMock(return_value={"MessageId": message_id})
    await service.send_email("owner@example.com", EmailContent("Security notice", "secret-html", "secret-token"))
    delivery = (await db_session.execute(select(EmailDelivery).where(EmailDelivery.message_id == message_id))).scalar_one()
    assert delivery.recipient == "owner@example.com"
    assert delivery.region == "us-east-1"
    assert delivery.subject == "Security notice"
    assert delivery.sent_at is not None
    assert "secret-token" not in repr(vars(delivery))
    await db_session.delete(delivery)
    await db_session.commit()
