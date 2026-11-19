from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from src.api.routes import account
from src.database import get_db
from src.services.email_change_email_service import EmailChangeEmailService


@pytest.fixture
async def email_change_client(monkeypatch):
    user = SimpleNamespace(id="user-1", email="owner@example.com", first_name="<Ada>", name="Ada")
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = user
    request_result = MagicMock()
    request_result.first.return_value = (1,)
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=lambda *args, **kwargs: (
            user_result if db.execute.await_count == 1 else request_result
        )),
        commit=AsyncMock(),
    )
    send_code = AsyncMock(return_value={"MessageId": "m-1"})
    send_notice = AsyncMock(return_value={"MessageId": "m-2"})
    monkeypatch.setattr(account, "get_authenticated_user_id", lambda request, config: user.id)
    monkeypatch.setattr(EmailChangeEmailService, "send_code", send_code)
    monkeypatch.setattr(EmailChangeEmailService, "send_changed_notice", send_notice)
    app = FastAPI()
    app.include_router(account.router)
    app.dependency_overrides[get_db] = lambda: db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield SimpleNamespace(http=http, user=user, db=db, request_result=request_result, send_code=send_code, send_notice=send_notice)


async def test_current_code_goes_to_the_address_on_file(email_change_client):
    c = email_change_client
    response = await c.http.post("/account/email-change-email", json={
        "kind": "current_code", "code": "123456", "new_email": "attacker@example.com",
    })
    assert response.status_code == 200
    c.send_code.assert_awaited_once_with(c.user, "owner@example.com", "123456", new_address=False)
    query, params = c.db.execute.await_args_list[1].args
    assert params == {"user_id": "user-1", "stage": "verify_current", "address": None}


async def test_new_code_goes_to_the_requested_new_address(email_change_client):
    c = email_change_client
    response = await c.http.post("/account/email-change-email", json={
        "kind": "new_code", "code": "654321", "new_email": "new@example.com",
    })
    assert response.status_code == 200
    c.send_code.assert_awaited_once_with(c.user, "new@example.com", "654321", new_address=True)
    _, params = c.db.execute.await_args_list[1].args
    assert params == {"user_id": "user-1", "stage": "verify_new", "address": "new@example.com"}


async def test_codes_need_an_open_request(email_change_client):
    c = email_change_client
    c.request_result.first.return_value = None
    response = await c.http.post("/account/email-change-email", json={
        "kind": "new_code", "code": "654321", "new_email": "someone@example.com",
    })
    assert response.status_code == 409
    c.send_code.assert_not_awaited()


async def test_changed_notice_only_after_the_switch(email_change_client):
    c = email_change_client
    body = {"kind": "changed", "old_email": "before@example.com", "new_email": "owner@example.com"}
    assert (await c.http.post("/account/email-change-email", json=body)).status_code == 200
    c.send_notice.assert_awaited_once_with(c.user, "before@example.com", "owner@example.com")
    query, params = c.db.execute.await_args_list[1].args
    assert "email_change_log" in str(query)
    assert params == {"user_id": "user-1", "old_email": "before@example.com", "new_email": "owner@example.com"}

    body["new_email"] = "elsewhere@example.com"
    assert (await c.http.post("/account/email-change-email", json=body)).status_code == 409
    assert c.send_notice.await_count == 1


async def test_changed_notice_needs_a_logged_change(email_change_client):
    c = email_change_client
    # No unsent change from that address in email_change_log: the notice would go to any old_email given.
    c.request_result.first.return_value = None
    body = {"kind": "changed", "old_email": "anyone@example.com", "new_email": "owner@example.com"}
    assert (await c.http.post("/account/email-change-email", json=body)).status_code == 409
    c.send_notice.assert_not_awaited()


@pytest.mark.parametrize("body", [
    {"kind": "current_code"},
    {"kind": "current_code", "code": "12345"},
    {"kind": "current_code", "code": "abcdef"},
    {"kind": "new_code", "code": "123456"},
    {"kind": "new_code", "code": "123456", "new_email": "Name <a@example.com>"},
    {"kind": "new_code", "code": "123456", "new_email": "a@example.com, b@example.com"},
    {"kind": "changed", "new_email": "owner@example.com"},
    {"kind": "other", "code": "123456"},
])
async def test_malformed_requests_are_rejected(email_change_client, body):
    c = email_change_client
    assert (await c.http.post("/account/email-change-email", json=body)).status_code == 422
    c.send_code.assert_not_awaited()


@pytest.mark.parametrize("error,status", [(RuntimeError("SES is not configured"), 503), (ValueError("SES failed"), 502)])
async def test_delivery_failure_is_visible(email_change_client, error, status):
    c = email_change_client
    c.send_code.side_effect = error
    response = await c.http.post("/account/email-change-email", json={"kind": "current_code", "code": "123456"})
    assert response.status_code == status


def _service():
    service = EmailChangeEmailService(SimpleNamespace(
        aws_region="us-east-1", aws_access_key_id="test", aws_secret_access_key="test", ses_from_email="support@example.com",
    ))
    service.email_service.send_email = AsyncMock()
    return service


async def test_code_email_escapes_name_and_shows_code():
    service = _service()
    user = SimpleNamespace(first_name="<Ada>", name="Ada")
    await service.send_code(user, "owner@example.com", "042917", new_address=False)
    recipient, content = service.email_service.send_email.call_args.args
    assert recipient == "owner@example.com"
    assert "&lt;Ada&gt;" in content.html and "042917" in content.html and "042917" in content.text
    assert "10 minutes" in content.text


async def test_code_never_in_the_subject():
    # email_deliveries stores subjects in plain text.
    service = _service()
    user = SimpleNamespace(first_name="Ada", name="Ada")
    for new_address in (False, True):
        await service.send_code(user, "x@example.com", "042917", new_address=new_address)
        _, content = service.email_service.send_email.call_args.args
        assert "042917" not in content.subject


async def test_new_address_code_leaves_out_the_account_name():
    # The name is whatever the account holder typed; the new address may belong to anyone.
    service = _service()
    user = SimpleNamespace(first_name="Visit spam.example now", name="Visit spam.example now")
    await service.send_code(user, "new@example.com", "042917", new_address=True)
    recipient, content = service.email_service.send_email.call_args.args
    assert recipient == "new@example.com"
    assert "spam.example" not in content.html and "spam.example" not in content.text
    assert "042917" in content.text


async def test_changed_notice_goes_to_the_old_address():
    service = _service()
    user = SimpleNamespace(first_name=None, name="Ada Lovelace")
    await service.send_changed_notice(user, "before@example.com", "after@example.com")
    recipient, content = service.email_service.send_email.call_args.args
    assert recipient == "before@example.com"
    assert "before@example.com" in content.text and "after@example.com" in content.text
    assert content.reply_to
