from copy import copy
from unittest.mock import AsyncMock
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy import delete, select

from src.config import get_config
from src.models import User
from src.services.email_service import EmailContent
from src.services.product_update_email_service import ProductUpdateEmailService
from tests.fixtures.factories import create_user


async def test_public_link_is_safe_to_prefetch_and_post_persists_only_product_opt_out(client, db_session):
    user_id = str(uuid4())
    user = await create_user(db_session, user_id=user_id, email=f"{user_id}@example.com",
                             plan="pro", subscription_status="active")
    # A copy: get_config() may return the shared test override.
    config = copy(get_config())
    config.app_base_url = "https://app.example.com"
    service = ProductUpdateEmailService(db_session, config)
    url = urlsplit(service.unsubscribe_url(user_id, user["email"]))
    path = url.path + "?" + url.query
    service.email_service.send_email = AsyncMock(return_value={"MessageId": "mock-only"})
    try:
        # A scanner/preview GET must leave this user eligible.
        response = await client.get(path)
        assert response.status_code == 200
        assert '<form method="post"' in response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert user["email"] not in response.text
        assert await db_session.scalar(select(User.product_updates_unsubscribed_at).where(User.id == user_id)) is None
        await service.send_product_update(user_id, EmailContent("News", "News", "News"))
        service.email_service.send_email.assert_awaited_once()
        service.email_service.send_email.reset_mock()

        # Mailbox one-click POSTs arrive without authentication or cookies.
        response = await client.post(path, content="List-Unsubscribe=One-Click",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        assert response.status_code == 200 and "You’re unsubscribed" in response.text
        row = (await db_session.execute(select(
            User.product_updates_unsubscribed_at, User.notify_on_completion, User.subscription_status,
        ).where(User.id == user_id))).one()
        assert row.product_updates_unsubscribed_at is not None
        assert row.notify_on_completion is True
        assert row.subscription_status == "active"
        # Repeating the form or mailbox request is harmless and preserves the timestamp.
        assert (await client.post(path)).status_code == 200
        assert await db_session.scalar(select(User.product_updates_unsubscribed_at).where(User.id == user_id)) == row.product_updates_unsubscribed_at
        assert "You’re unsubscribed" in (await client.get(path)).text
        assert (await service.send_product_update(user_id, EmailContent("News", "News", "News")))["skipped"] is True
        service.email_service.send_email.assert_not_awaited()
        for method in (client.get, client.post):
            assert (await method(url.path)).status_code == 400
            assert (await method(path + "corrupted")).status_code == 400
    finally:
        await db_session.execute(delete(User).where(User.id == user_id))
        await db_session.commit()
