from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

import resend

from ..config import Config


@dataclass(frozen=True)
class EmailContent:
    subject: str
    html: str
    text: str


class ResendEmailService:
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.api_key = self.config.resend_api_key
        self.from_email = self.config.resend_from_email

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.from_email)

    async def send_email(self, recipient: str, content: EmailContent) -> dict:
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


def first_name_for(
    *,
    first_name: Optional[str] = None,
    full_name: Optional[str] = None,
    default: str = "there",
) -> str:
    if first_name and first_name.strip():
        return first_name.strip()
    if full_name and full_name.strip():
        return full_name.strip().split()[0]
    return default
