from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from src.api.routes import account
from src.database import get_db
from src.services.affiliate_email_service import AffiliateEmailService


def configured_service() -> AffiliateEmailService:
    config = SimpleNamespace(
        aws_region="eu-central-1",
        aws_access_key_id="access-key",
        aws_secret_access_key="secret-key",
        ses_from_email="SupoClip <noreply@example.com>",
        app_base_url="https://supoclip.com",
    )
    return AffiliateEmailService(config)


def test_approved_email_contains_link_and_code():
    user = SimpleNamespace(email="maya@example.com", first_name="Maya", name="Maya")

    content = configured_service()._build_approved_email(user, "maya")

    assert "https://supoclip.com/?ref=maya" in content.text
    assert "MAYA" in content.html
    assert "first month free, then 20% off forever" in content.text


def test_declined_email_includes_escaped_reason_only_when_given():
    service = configured_service()
    user = SimpleNamespace(email="x@example.com", first_name=None, name="<Bob>")

    with_reason = service._build_declined_email(user, "<b>Profile is private</b>")
    without_reason = service._build_declined_email(user, None)

    assert "&lt;b&gt;Profile is private&lt;/b&gt;" in with_reason.html
    assert "&lt;Bob&gt;" in with_reason.html
    assert "Note from the team" not in without_reason.text


def test_applied_email_escapes_applicant_fields():
    applicant = SimpleNamespace(email="a@example.com", name="<script>", first_name=None)

    content = configured_service()._build_applied_email(
        applicant, {"slug": "maya", "profile_url": "https://tiktok.com/@maya\"><x>"}
    )

    assert "<script>" not in content.html
    assert "https://supoclip.com/admin#affiliates" in content.text


@pytest.fixture
async def client(monkeypatch):
    applicant = SimpleNamespace(id="user-1", email="maya@example.com", first_name="Maya", name="Maya")
    admins = [SimpleNamespace(email="admin1@example.com"), SimpleNamespace(email="admin2@example.com")]

    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = applicant
    admin_result = MagicMock()
    admin_result.scalars.return_value.all.return_value = admins
    db = SimpleNamespace(execute=AsyncMock(side_effect=[user_result, admin_result]))

    sent = {
        "applied": AsyncMock(return_value={}),
        "approved": AsyncMock(return_value={}),
        "declined": AsyncMock(return_value={}),
    }
    monkeypatch.setattr(account, "get_authenticated_user_id", lambda request, config: "user-1")
    monkeypatch.setattr(AffiliateEmailService, "send_applied_email", sent["applied"])
    monkeypatch.setattr(AffiliateEmailService, "send_approved_email", sent["approved"])
    monkeypatch.setattr(AffiliateEmailService, "send_declined_email", sent["declined"])

    app = FastAPI()
    app.include_router(account.router)
    app.dependency_overrides[get_db] = lambda: db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http, sent, applicant


async def test_applied_notifies_every_admin(client):
    http, sent, applicant = client

    response = await http.post("/account/affiliate-email", json={"event": "applied", "slug": "maya"})

    assert response.status_code == 200
    recipients = [call.args[0] for call in sent["applied"].await_args_list]
    assert recipients == ["admin1@example.com", "admin2@example.com"]
    assert sent["applied"].await_args_list[0].args[1] is applicant


async def test_decisions_go_to_the_applicant(client):
    http, sent, applicant = client

    approved = await http.post("/account/affiliate-email", json={"event": "approved", "slug": "maya"})

    assert approved.status_code == 200
    sent["approved"].assert_awaited_once_with(applicant, "maya")
    sent["applied"].assert_not_awaited()


async def test_rejects_unknown_events(client):
    http, sent, _ = client

    response = await http.post("/account/affiliate-email", json={"event": "paid", "slug": "maya"})

    assert response.status_code == 422
