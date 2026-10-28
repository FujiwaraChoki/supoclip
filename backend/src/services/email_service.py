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

TRANSACTIONAL = "transactional"
# Product announcements must honour the opt-out: send them only through
# ProductUpdateEmailService, which checks it and adds the unsubscribe headers.
PRODUCT_UPDATE = "product_update"


@dataclass(frozen=True)
class EmailContent:
    subject: str
    html: str
    text: str
    headers: tuple[tuple[str, str], ...] = ()
    category: str = TRANSACTIONAL
    reply_to: tuple[str, ...] = ()


class SesEmailService:
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.region = self.config.aws_region
        self.access_key_id = self.config.aws_access_key_id
        self.secret_access_key = self.config.aws_secret_access_key
        self.from_email = self.config.ses_from_email
        self._client = None

    @property
    def is_configured(self) -> bool:
        return bool(
            self.region
            and self.access_key_id
            and self.secret_access_key
            and self.from_email
        )

    def _send(self, recipient: str, content: EmailContent) -> dict:
        headers = [{"Name": name, "Value": value} for name, value in content.headers]
        # boto3 clients are thread-safe; reuse one across a campaign's sends.
        if self._client is None:
            self._client = boto3.client(
                "sesv2",
                region_name=self.region,
                aws_access_key_id=self.access_key_id,
                aws_secret_access_key=self.secret_access_key,
            )
        return self._client.send_email(
            FromEmailAddress=self.from_email,
            Destination={"ToAddresses": [recipient]},
            **({"ReplyToAddresses": list(content.reply_to)} if content.reply_to else {}),
            Content={
                "Simple": {
                    **({"Headers": headers} if headers else {}),
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
        if content.category == PRODUCT_UPDATE and not any(
            name.lower() == "list-unsubscribe-post" for name, _ in content.headers
        ):
            raise ValueError("Send product updates through ProductUpdateEmailService")

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
