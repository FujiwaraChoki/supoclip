"""Preference-aware SES delivery for product announcements (never transactional mail)."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import replace
from html import escape
from urllib.parse import urlencode, urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Config, get_config
from ..repositories.email_preference_repository import EmailPreferenceRepository
from .email_service import PRODUCT_UPDATE, EmailContent, SesEmailService, first_name_for

UNSUBSCRIBE_PATH = "/email/product-updates/unsubscribe"
_TOKEN = re.compile(r"v1\.([A-Za-z0-9_-]{1,36})\.([0-9a-f]{64})")
_FIELD = re.compile(r"{{\s*([\w.]+)\s*}}")


class InvalidUnsubscribeToken(ValueError):
    pass


class ProductUpdateEmailsNotConfigured(RuntimeError):
    pass


def _fields(template: str) -> set[str]:
    return {match[1] for match in _FIELD.finditer(template)}


def _fill(template: str, values: dict[str, str]) -> str:
    # One pass over the template, so a filled value (a user's name) is never re-parsed.
    return _FIELD.sub(lambda match: values[match[1]], template)


class ProductUpdateEmailService:
    def __init__(self, db: AsyncSession, config: Config | None = None):
        self.db = db
        self.config = config or get_config()
        self.email_service = SesEmailService(self.config)

    async def _recipient(self, user_id: str):
        return await EmailPreferenceRepository.get_product_update_recipient(self.db, user_id)

    def _signature(self, user_id: str, email: str) -> str:
        secret = self.config.backend_auth_secret
        if not secret:
            raise ProductUpdateEmailsNotConfigured("BACKEND_AUTH_SECRET is required for product-update emails")
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
        if not await EmailPreferenceRepository.unsubscribe_from_product_updates(
            self.db, recipient["id"], recipient["email"],
        ):
            raise InvalidUnsubscribeToken()

    async def send_product_update(self, user_id: str, content: EmailContent) -> dict:
        """Re-read preferences for each recipient immediately before sending via SES."""
        unresolved = (_fields(content.subject) - {"first_name"}) | (
            _fields(content.html) | _fields(content.text)
        ) - {"first_name", "unsubscribe_url"}
        if unresolved:
            raise ValueError(
                f"Product-update email contains unresolved template fields: {', '.join(sorted(unresolved))}"
            )
        recipient = await self._recipient(user_id)
        if not recipient:
            raise ValueError("Product-update recipient not found")
        if recipient["product_updates_unsubscribed_at"] is not None:
            return {"skipped": True, "reason": "unsubscribed"}
        url = self.unsubscribe_url(recipient["id"], recipient["email"])
        name = first_name_for(first_name=recipient["first_name"], full_name=recipient["name"])
        subject = _fill(content.subject, {"first_name": name})
        html = _fill(content.html, {"first_name": escape(name), "unsubscribe_url": escape(url, quote=True)})
        text = _fill(content.text, {"first_name": name, "unsubscribe_url": url})
        if "unsubscribe_url" not in _fields(content.html):
            footer = f'<p><a href="{escape(url, quote=True)}">Unsubscribe from product updates</a></p>'
            closing = re.search(r"</body\s*>", html, re.IGNORECASE)
            html = html[:closing.start()] + footer + html[closing.start():] if closing else html + footer
        if "unsubscribe_url" not in _fields(content.text):
            text += f"\n\nUnsubscribe from product updates: {url}\n"
        # RFC 8058: mailbox clients POST to this URL without cookies or sign-in.
        headers = tuple((key, value) for key, value in content.headers
                        if key.lower() not in {"list-unsubscribe", "list-unsubscribe-post"})
        rendered = replace(
            content, subject=subject, html=html, text=text, category=PRODUCT_UPDATE,
            headers=headers + (
                ("List-Unsubscribe", f"<{url}>"),
                ("List-Unsubscribe-Post", "List-Unsubscribe=One-Click"),
            ),
        )
        return await self.email_service.send_email(recipient["email"], rendered)
