"""Tests for email service layer (``email_service``, ``task_completion_email_service``,
``subscription_email_service``). The Resend HTTP call is mocked."""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.services import email_service as es
from src.services.email_service import (
    EmailContent,
    ResendEmailService,
    first_name_for,
)
from src.services.subscription_email_service import SubscriptionEmailService
from src.services.task_completion_email_service import (
    TaskCompletionEmailService,
    TaskCompletionRecipient,
)


class TestFirstNameFor:
    def test_uses_explicit_first_name(self):
        assert first_name_for(first_name="Jin", full_name="Jin Kim") == "Jin"

    def test_trims_whitespace(self):
        assert first_name_for(first_name="  Jin  ") == "Jin"

    def test_falls_back_to_first_token_of_full_name(self):
        assert first_name_for(first_name=None, full_name="Jin Kim") == "Jin"

    def test_default_when_both_missing(self):
        assert first_name_for() == "there"
        assert first_name_for(first_name="", full_name="") == "there"

    def test_custom_default(self):
        assert first_name_for(first_name=None, full_name=None, default="friend") == "friend"

    def test_whitespace_only_falls_through(self):
        assert first_name_for(first_name="   ", full_name="   ") == "there"


def _fake_config(api_key="key", from_email="SupoClip <no@reply>"):
    return SimpleNamespace(
        resend_api_key=api_key,
        resend_from_email=from_email,
        app_base_url="https://supoclip.test",
    )


class TestResendEmailService:
    def test_is_configured_when_both_set(self):
        svc = ResendEmailService(_fake_config())
        assert svc.is_configured is True

    def test_not_configured_without_api_key(self):
        svc = ResendEmailService(_fake_config(api_key=""))
        assert svc.is_configured is False

    def test_not_configured_without_from_email(self):
        svc = ResendEmailService(_fake_config(from_email=""))
        assert svc.is_configured is False

    @pytest.mark.asyncio
    async def test_send_email_raises_when_not_configured(self):
        svc = ResendEmailService(_fake_config(api_key=""))
        with pytest.raises(RuntimeError, match="Resend is not configured"):
            await svc.send_email(
                "user@example.com",
                EmailContent(subject="x", html="<p>h</p>", text="t"),
            )

    @pytest.mark.asyncio
    async def test_send_email_calls_resend_with_params(self, monkeypatch):
        svc = ResendEmailService(_fake_config())
        captured = {}

        def fake_send(params):
            captured["params"] = dict(params)
            return {"id": "email-id-1"}

        monkeypatch.setattr(es.resend.Emails, "send", fake_send, raising=False)
        result = await svc.send_email(
            "user@example.com",
            EmailContent(subject="Hi", html="<p>hi</p>", text="hi"),
        )
        assert result == {"id": "email-id-1"}
        params = captured["params"]
        assert params["to"] == ["user@example.com"]
        assert params["subject"] == "Hi"
        assert params["from"] == "SupoClip <no@reply>"


class TestTaskCompletionEmailService:
    def test_is_configured_delegates(self):
        svc = TaskCompletionEmailService(_fake_config(api_key=""))
        assert svc.is_configured is False
        svc2 = TaskCompletionEmailService(_fake_config())
        assert svc2.is_configured is True

    def test_build_email_content_escapes_and_includes_url(self):
        svc = TaskCompletionEmailService(_fake_config())
        recipient = TaskCompletionRecipient(email="u@x.com", first_name="Jin", name="Jin Kim")
        content = svc._build_task_completed_email(
            recipient=recipient,
            task_id="task-42",
            source_title="<My> Video",
            clips_count=3,
        )
        assert "Hi Jin" in content.html
        assert "Hi Jin" in content.text
        # source_title is HTML-escaped in html view
        assert "&lt;My&gt; Video" in content.html
        # Plaintext keeps the original
        assert "<My> Video" in content.text
        assert "https://supoclip.test/tasks/task-42" in content.html
        assert "3 clips" in content.text

    def test_build_singular_clip_label(self):
        svc = TaskCompletionEmailService(_fake_config())
        recipient = TaskCompletionRecipient(email="u@x.com", first_name=None, name=None)
        content = svc._build_task_completed_email(
            recipient=recipient,
            task_id="t",
            source_title=None,
            clips_count=1,
        )
        assert "1 clip" in content.text
        assert "1 clips" not in content.text
        # Default greeting "there" when no names provided
        assert "Hi there" in content.text
        # Fallback for missing title
        assert "your video" in content.text

    @pytest.mark.asyncio
    async def test_send_task_completed_email_calls_resend(self, monkeypatch):
        svc = TaskCompletionEmailService(_fake_config())
        captured = {}

        def fake_send(params):
            captured["params"] = dict(params)
            return {"id": "ok"}

        monkeypatch.setattr(es.resend.Emails, "send", fake_send, raising=False)
        result = await svc.send_task_completed_email(
            recipient=TaskCompletionRecipient(email="a@b.com", first_name="Jin"),
            task_id="t1",
            source_title="My Source",
            clips_count=2,
        )
        assert result == {"id": "ok"}
        assert captured["params"]["to"] == ["a@b.com"]
        assert "ready" in captured["params"]["subject"].lower()


class TestSubscriptionEmailService:
    def _user(self, **overrides):
        defaults = dict(
            email="u@x.com",
            name="Jin Kim",
            first_name=None,
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_subscribed_content(self):
        svc = SubscriptionEmailService(_fake_config())
        content = svc._build_subscribed_email(self._user())
        assert "Hi Jin" in content.text
        assert "subscribing" in content.subject.lower() or "thanks" in content.subject.lower()

    def test_unsubscribed_content(self):
        svc = SubscriptionEmailService(_fake_config())
        content = svc._build_unsubscribed_email(self._user())
        assert "Hi Jin" in content.text
        assert "sorry" in content.subject.lower() or "cancel" in content.html.lower()

    def test_first_name_fallback_default(self):
        svc = SubscriptionEmailService(_fake_config())
        user = self._user(name=None, first_name=None)
        content = svc._build_subscribed_email(user)
        assert "Hi there" in content.text

    @pytest.mark.asyncio
    async def test_send_subscribed_email_calls_resend(self, monkeypatch):
        svc = SubscriptionEmailService(_fake_config())
        sent = {}

        def fake_send(params):
            sent["p"] = dict(params)
            return {"id": "s1"}

        monkeypatch.setattr(es.resend.Emails, "send", fake_send, raising=False)
        await svc.send_subscribed_email(self._user())
        assert sent["p"]["to"] == ["u@x.com"]

    @pytest.mark.asyncio
    async def test_send_unsubscribed_email_calls_resend(self, monkeypatch):
        svc = SubscriptionEmailService(_fake_config())
        sent = {}

        def fake_send(params):
            sent["p"] = dict(params)
            return {"id": "u1"}

        monkeypatch.setattr(es.resend.Emails, "send", fake_send, raising=False)
        await svc.send_unsubscribed_email(self._user())
        assert sent["p"]["to"] == ["u@x.com"]
