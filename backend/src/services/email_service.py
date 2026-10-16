from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

import boto3

from ..config import Config
from ..database import get_session_maker
from ..models import EmailDelivery

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmailContent:
    subject: str
    html: str
    text: str


class SesEmailService:
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.region = self.config.aws_region
        self.access_key_id = self.config.aws_access_key_id
        self.secret_access_key = self.config.aws_secret_access_key
        self.from_email = self.config.ses_from_email

    @property
    def is_configured(self) -> bool:
        return bool(
            self.region
            and self.access_key_id
            and self.secret_access_key
            and self.from_email
        )

    def _send(self, recipient: str, content: EmailContent) -> dict:
        client = boto3.client(
            "sesv2",
            region_name=self.region,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
        )
        return client.send_email(
            FromEmailAddress=self.from_email,
            Destination={"ToAddresses": [recipient]},
            Content={
                "Simple": {
                    "Subject": {"Data": content.subject, "Charset": "UTF-8"},
                    "Body": {
                        "Html": {"Data": content.html, "Charset": "UTF-8"},
                        "Text": {"Data": content.text, "Charset": "UTF-8"},
                    },
                }
            },
        )

    async def send_email(self, recipient: str, content: EmailContent) -> dict:
        if not self.is_configured:
            raise RuntimeError(
                "Amazon SES is not configured. Set AWS_REGION, AWS_ACCESS_KEY_ID, "
                "AWS_SECRET_ACCESS_KEY and SES_FROM_EMAIL."
            )

        response = await asyncio.to_thread(self._send, recipient, content)
        # This is acceptance by SES, not proof of inbox delivery. Record only
        # metadata: bodies can contain password reset or verification tokens.
        message_id = response["MessageId"]
        try:
            async with get_session_maker()() as db:
                db.add(EmailDelivery(
                    message_id=message_id,
                    recipient=recipient,
                    subject=content.subject,
                    region=self.region,
                ))
                await db.commit()
        except Exception:
            # SES has already accepted the message. Preserve a correlation
            # fallback without making callers retry and send duplicate emails.
            logger.exception(
                "SES audit persistence failed: message_id=%s recipient=%s region=%s subject=%r",
                message_id, recipient, self.region, content.subject,
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
