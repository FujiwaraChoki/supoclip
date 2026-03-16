from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

import resend

from ..config import Config
from ..models import User


@dataclass(frozen=True)
class EmailContent:
    subject: str
    html: str
    text: str


class SubscriptionEmailService:
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.api_key = self.config.resend_api_key
        self.from_email = self.config.resend_from_email

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.from_email)

    async def send_subscribed_email(self, user: User) -> dict:
        content = self._build_subscribed_email(user)
        return await self._send_email(user.email, content)

    async def send_unsubscribed_email(self, user: User) -> dict:
        content = self._build_unsubscribed_email(user)
        return await self._send_email(user.email, content)

    async def _send_email(self, recipient: str, content: EmailContent) -> dict:
        if not self.is_configured:
            raise RuntimeError(
                "Resend is not configured. Set RESEND_API_KEY and RESEND_FROM_EMAIL."
            )

        resend.api_key = self.api_key
        params: resend.Emails.SendParams = {
            "from": self.from_email,
            "to": [recipient],
            "subject": content.subject,
            "html": content.html,
            "text": content.text,
        }
        response: resend.Emails.SendResponse = await asyncio.to_thread(
            resend.Emails.send, params
        )
        return dict(response)

    def _build_subscribed_email(self, user: User) -> EmailContent:
        first_name = self._first_name_for(user)
        return EmailContent(
            subject="Thanks for subscribing to SupoClip",
            html=(
                f"<p>Hi {first_name},</p>"
                "<p>Thanks for subscribing to SupoClip.</p>"
                "<p>Your Pro plan is now active, and you can jump back in anytime to create more clips.</p>"
                "<p>We’re excited to have you with us.</p>"
                "<p>Team SupoClip</p>"
            ),
            text=(
                f"Hi {first_name},\n\n"
                "Thanks for subscribing to SupoClip.\n\n"
                "Your Pro plan is now active, and you can jump back in anytime to create more clips.\n\n"
                "We’re excited to have you with us.\n\n"
                "Team SupoClip"
            ),
        )

    def _build_unsubscribed_email(self, user: User) -> EmailContent:
        first_name = self._first_name_for(user)
        return EmailContent(
            subject="Sorry to see you go from SupoClip",
            html=(
                f"<p>Hi {first_name},</p>"
                "<p>Sorry to see you go, and thanks for trying SupoClip.</p>"
                "<p>Your subscription has been canceled. If you ever want to come back, we’d love to have you.</p>"
                "<p>Team SupoClip</p>"
            ),
            text=(
                f"Hi {first_name},\n\n"
                "Sorry to see you go, and thanks for trying SupoClip.\n\n"
                "Your subscription has been canceled. If you ever want to come back, we’d love to have you.\n\n"
                "Team SupoClip"
            ),
        )

    @staticmethod
    def _first_name_for(user: User) -> str:
        if user.first_name and user.first_name.strip():
            return user.first_name.strip()
        if user.name and user.name.strip():
            return user.name.strip().split()[0]
        return "there"
