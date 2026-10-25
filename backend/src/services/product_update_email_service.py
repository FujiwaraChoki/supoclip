"""Preference-aware SES delivery for product announcements (never transactional mail)."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from html import escape
from urllib.parse import urlencode, urlsplit

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Config, get_config
from ..models import User
from .email_service import EmailContent, SesEmailService, first_name_for

UNSUBSCRIBE_PATH = "/email/product-updates/unsubscribe"
_TOKEN = re.compile(r"v1\.([A-Za-z0-9_-]{1,36})\.([0-9a-f]{64})")


class InvalidUnsubscribeToken(ValueError):
    pass


class ProductUpdateEmailService:
    def __init__(self, db: AsyncSession, config: Config | None = None):
        self.db = db
        self.config = config or get_config()
        self.email_service = SesEmailService(self.config)

    async def _recipient(self, user_id: str):
        result = await self.db.execute(select(
            User.id, User.email, User.first_name, User.name,
            User.product_updates_unsubscribed_at,
        ).where(User.id == user_id))
        return result.mappings().first()

    def _signature(self, user_id: str, email: str) -> str:
        secret = self.config.backend_auth_secret
        if not secret:
            raise RuntimeError("BACKEND_AUTH_SECRET is required for product-update emails")
        # Bind the grant to the current email without putting the address in a URL.
        # Domain separation prevents a token being reused as another auth signature.
        payload = json.dumps(["product-updates", "v1", user_id, email.strip().lower()])
        return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

    def unsubscribe_url(self, user_id: str, email: str) -> str:
        token = f"v1.{user_id}.{self._signature(user_id, email)}"
        if not _TOKEN.fullmatch(token):
            raise ValueError("Invalid product-update recipient ID")
        base = self.config.app_base_url.rstrip("/")
        parsed = urlsplit(base)
        if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("Product-update emails require an HTTPS app_base_url")
        return f"{base}{UNSUBSCRIBE_PATH}?{urlencode({'token': token})}"

    async def validate_token(self, token: str):
        match = _TOKEN.fullmatch(token)
        if not match:
            raise InvalidUnsubscribeToken()
        recipient = await self._recipient(match[1])
        if not recipient or not hmac.compare_digest(
            match[2], self._signature(recipient["id"], recipient["email"])
        ):
            raise InvalidUnsubscribeToken()
        return recipient

    async def unsubscribe(self, token: str) -> None:
        recipient = await self.validate_token(token)
        result = await self.db.execute(update(User).where(
            User.id == recipient["id"], User.email == recipient["email"],
        ).values(product_updates_unsubscribed_at=func.coalesce(
            User.product_updates_unsubscribed_at, func.now(),
        )))
        if result.rowcount != 1:
            raise InvalidUnsubscribeToken()
        await self.db.commit()

    async def send_product_update(self, user_id: str, content: EmailContent) -> dict:
        """Re-read preferences for each recipient immediately before sending via SES."""
        recipient = await self._recipient(user_id)
        if not recipient:
            raise ValueError("Product-update recipient not found")
        if recipient["product_updates_unsubscribed_at"] is not None:
            return {"skipped": True, "reason": "unsubscribed"}
        url = self.unsubscribe_url(recipient["id"], recipient["email"])
        name = first_name_for(first_name=recipient["first_name"], full_name=recipient["name"])
        html = content.html.replace("{{first_name}}", escape(name))
        text = content.text.replace("{{first_name}}", name)
        if "{{unsubscribe_url}}" in html:
            html = html.replace("{{unsubscribe_url}}", escape(url, quote=True))
        else:
            footer = f'<p><a href="{escape(url, quote=True)}">Unsubscribe from product updates</a></p>'
            closing = re.search(r"</body\s*>", html, re.IGNORECASE)
            html = html[:closing.start()] + footer + html[closing.start():] if closing else html + footer
        text = (text.replace("{{unsubscribe_url}}", url) if "{{unsubscribe_url}}" in text
                else text + f"\n\nUnsubscribe from product updates: {url}\n")
        if re.search(r"{{\s*[\w.]+\s*}}", html + text):
            raise ValueError("Product-update email contains unresolved template fields")
        # RFC 8058: mailbox clients POST to this URL without cookies or sign-in.
        headers = tuple((key, value) for key, value in content.headers
                        if key.lower() not in {"list-unsubscribe", "list-unsubscribe-post"})
        rendered = EmailContent(content.subject, html, text, headers + (
            ("List-Unsubscribe", f"<{url}>"),
            ("List-Unsubscribe-Post", "List-Unsubscribe=One-Click"),
        ))
        return await self.email_service.send_email(recipient["email"], rendered)
