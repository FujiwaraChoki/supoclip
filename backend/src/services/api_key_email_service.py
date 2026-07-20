from __future__ import annotations

from html import escape
from typing import Any, Mapping, Optional

from ..config import Config
from ..models import User
from .email_service import EmailContent, SesEmailService, first_name_for


class ApiKeyEmailService:
    """Send security notifications for API-key lifecycle events."""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.email_service = SesEmailService(self.config)

    @property
    def is_configured(self) -> bool:
        return self.email_service.is_configured

    async def send_created_email(
        self, user: User, api_key: Mapping[str, Any]
    ) -> dict:
        content = self._build_created_email(user, api_key)
        return await self.email_service.send_email(user.email, content)

    def _build_created_email(
        self, user: User, api_key: Mapping[str, Any]
    ) -> EmailContent:
        first_name = first_name_for(first_name=user.first_name, full_name=user.name)
        key_name = str(api_key.get("name") or "API Key")
        key_prefix = str(api_key.get("key_prefix") or "unknown")

        return EmailContent(
            subject="A new SupoClip API key was created",
            html=(
                f"<p>Hi {escape(first_name)},</p>"
                "<p>A new API key was created for your SupoClip account.</p>"
                f"<p><strong>Name:</strong> {escape(key_name)}<br>"
                f"<strong>Prefix:</strong> {escape(key_prefix)}</p>"
                "<p>If you created this key, no action is needed. If you did not, "
                "revoke it immediately from your SupoClip API key settings and secure your account.</p>"
                "<p>For your security, the API key secret is not included in this email.</p>"
                "<p>Team SupoClip</p>"
            ),
            text=(
                f"Hi {first_name},\n\n"
                "A new API key was created for your SupoClip account.\n\n"
                f"Name: {key_name}\n"
                f"Prefix: {key_prefix}\n\n"
                "If you created this key, no action is needed. If you did not, revoke it "
                "immediately from your SupoClip API key settings and secure your account.\n\n"
                "For your security, the API key secret is not included in this email.\n\n"
                "Team SupoClip"
            ),
        )
