from unittest.mock import AsyncMock

from sqlalchemy import text

from src.services.email_change_email_service import EmailChangeEmailService
from tests.fixtures.factories import create_user


async def _open_request(db_session, stage: str, new_email: str | None = None):
    await db_session.execute(
        text(
            "INSERT INTO email_change_requests (user_id, session_id, stage, current_email, new_email, expires_at) "
            "VALUES ('user-1', 'session-1', :stage, 'user-1@example.com', :new_email, now() + interval '30 minutes') "
            "ON CONFLICT (user_id) DO UPDATE SET stage = EXCLUDED.stage, new_email = EXCLUDED.new_email"
        ),
        {"stage": stage, "new_email": new_email},
    )
    await db_session.commit()


async def test_codes_follow_the_open_request(client, db_session, auth_headers, monkeypatch):
    await create_user(db_session, user_id="user-1")
    send_code = AsyncMock(return_value={"MessageId": "m-1"})
    monkeypatch.setattr(EmailChangeEmailService, "send_code", send_code)
    current = {"kind": "current_code", "code": "123456"}
    new = {"kind": "new_code", "code": "654321", "new_email": "fresh@example.com"}

    try:
        # No request yet: nothing is sent.
        assert (await client.post("/account/email-change-email", headers=auth_headers, json=current)).status_code == 409

        await _open_request(db_session, "verify_current")
        assert (await client.post("/account/email-change-email", headers=auth_headers, json=current)).status_code == 200
        assert (await client.post("/account/email-change-email", headers=auth_headers, json=new)).status_code == 409

        await _open_request(db_session, "verify_new", "fresh@example.com")
        assert (await client.post("/account/email-change-email", headers=auth_headers, json=new)).status_code == 200
        other = {**new, "new_email": "someone@example.com"}
        assert (await client.post("/account/email-change-email", headers=auth_headers, json=other)).status_code == 409

        assert [call.args[1:] for call in send_code.await_args_list] == [
            ("user-1@example.com", "123456"),
            ("fresh@example.com", "654321"),
        ]
    finally:
        await db_session.execute(text("DELETE FROM email_change_requests WHERE user_id = 'user-1'"))
        await db_session.commit()


async def test_changed_notice_once_per_logged_change(client, db_session, auth_headers, monkeypatch):
    await create_user(db_session, user_id="user-1")
    send_notice = AsyncMock(side_effect=[RuntimeError("SES is not configured"), {"MessageId": "m-1"}])
    monkeypatch.setattr(EmailChangeEmailService, "send_changed_notice", send_notice)
    body = {"kind": "changed", "old_email": "before@example.com", "new_email": "user-1@example.com"}

    try:
        # Nothing logged: the backend won't mail an address it was only told about.
        assert (await client.post("/account/email-change-email", headers=auth_headers, json=body)).status_code == 409

        await db_session.execute(text(
            "INSERT INTO email_change_log (user_id, old_email, new_email, session_id, changed_at) "
            "VALUES ('user-1', 'before@example.com', 'user-1@example.com', 'session-1', now())"
        ))
        await db_session.commit()
        # A failed send leaves the notice unsent, so it can be tried again.
        assert (await client.post("/account/email-change-email", headers=auth_headers, json=body)).status_code == 503
        assert (await client.post("/account/email-change-email", headers=auth_headers, json=body)).status_code == 200
        assert (await client.post("/account/email-change-email", headers=auth_headers, json=body)).status_code == 409
        assert send_notice.await_count == 2

        notified = (await db_session.execute(text("SELECT notified_at FROM email_change_log"))).scalar_one()
        assert notified is not None
    finally:
        await db_session.execute(text("DELETE FROM email_change_log WHERE user_id = 'user-1'"))
        await db_session.commit()
