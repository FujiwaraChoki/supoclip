from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Optional
from urllib.parse import urlparse

from ..config import Config
from .email_layout import email_button
from .email_service import EmailContent, SesEmailService, first_name_for

_PROVIDER_NAMES = {
    "youtube": "YouTube Shorts",
    "tiktok": "TikTok",
    "instagram": "Instagram Reels",
}


@dataclass(frozen=True)
class SocialPublishRecipient:
    email: str
    name: Optional[str] = None
    first_name: Optional[str] = None


def _provider_name(provider: str) -> str:
    normalized = (provider or "").strip().lower()
    return _PROVIDER_NAMES.get(normalized, normalized.capitalize() or "your social account")


def _account_label(
    display_name: Optional[str], username: Optional[str]
) -> Optional[str]:
    clean_name = (display_name or "").strip() or None
    clean_handle = (username or "").strip().lstrip("@")
    handle = f"@{clean_handle}" if clean_handle else None
    if clean_name and handle and clean_name.lower() != handle.lower():
        return f"{clean_name} ({handle})"
    return clean_name or handle


def _safe_http_url(url: Optional[str]) -> Optional[str]:
    candidate = (url or "").strip()
    if not candidate:
        return None
    parsed = urlparse(candidate)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return candidate
    return None


class SocialPublishEmailService:
    """Send notifications when a clip finishes publishing to a social platform."""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.email_service = SesEmailService(self.config)
        self.app_url = self.config.app_base_url

    @property
    def is_configured(self) -> bool:
        return self.email_service.is_configured

    async def send_clip_published_email(
        self,
        *,
        recipient: SocialPublishRecipient,
        provider: str,
        title: Optional[str] = None,
        external_url: Optional[str] = None,
        task_id: Optional[str] = None,
        account_display_name: Optional[str] = None,
        account_username: Optional[str] = None,
    ) -> dict:
        content = self._build_clip_published_email(
            recipient=recipient,
            provider=provider,
            title=title,
            external_url=external_url,
            task_id=task_id,
            account_display_name=account_display_name,
            account_username=account_username,
        )
        return await self.email_service.send_email(recipient.email, content)

    def _build_clip_published_email(
        self,
        *,
        recipient: SocialPublishRecipient,
        provider: str,
        title: Optional[str] = None,
        external_url: Optional[str] = None,
        task_id: Optional[str] = None,
        account_display_name: Optional[str] = None,
        account_username: Optional[str] = None,
    ) -> EmailContent:
        first_name = first_name_for(
            first_name=recipient.first_name,
            full_name=recipient.name,
        )
        provider_label = _provider_name(provider)
        account = _account_label(account_display_name, account_username)
        clean_title = (title or "").strip() or None
        live_url = _safe_http_url(external_url)
        task_url = f"{self.app_url}/tasks/{task_id}" if task_id else None
        analytics_url = f"{self.app_url}/analytics"
        settings_url = f"{self.app_url}/settings"

        account_suffix_html = f" ({escape(account)})" if account else ""
        account_suffix_text = f" ({account})" if account else ""

        if clean_title:
            headline_html = (
                f"Your clip <strong>{escape(clean_title)}</strong> has been published to "
                f"<strong>{escape(provider_label)}</strong>{account_suffix_html}."
            )
            headline_text = (
                f'Your clip "{clean_title}" has been published to '
                f"{provider_label}{account_suffix_text}."
            )
        else:
            headline_html = (
                f"Your clip has been published to "
                f"<strong>{escape(provider_label)}</strong>{account_suffix_html}."
            )
            headline_text = (
                f"Your clip has been published to "
                f"{provider_label}{account_suffix_text}."
            )

        if live_url:
            button_html = email_button(live_url, f"View on {provider_label}")
            secondary_url = task_url or analytics_url
            secondary_label = (
                "open the clip in SupoClip" if task_url else "view your posts in SupoClip"
            )
            footer_html = (
                f'<p>You can also <a href="{escape(secondary_url, quote=True)}">'
                f"{secondary_label}</a> or "
                f'<a href="{escape(settings_url, quote=True)}">'
                "manage your notification settings</a>.</p>"
            )
            links_text = (
                f"View on {provider_label}: {live_url}\n"
                + (
                    f"Open in SupoClip: {task_url}\n"
                    if task_url
                    else f"View your posts in SupoClip: {analytics_url}\n"
                )
                + f"Notification settings: {settings_url}"
            )
        else:
            primary_url = task_url or analytics_url
            button_html = email_button(primary_url, "Open in SupoClip")
            footer_html = (
                f'<p>You can <a href="{escape(settings_url, quote=True)}">'
                "manage your notification settings</a> anytime.</p>"
            )
            links_text = (
                f"Open in SupoClip: {primary_url}\n"
                f"Notification settings: {settings_url}"
            )

        return EmailContent(
            subject=f"Your clip is live on {provider_label}",
            html=(
                f"<p>Hi {escape(first_name)},</p>"
                f"<p>{headline_html}</p>"
                f"{button_html}"
                f"{footer_html}"
                "<p>Team SupoClip</p>"
            ),
            text=(
                f"Hi {first_name},\n\n"
                f"{headline_text}\n\n"
                f"{links_text}\n\n"
                "Team SupoClip"
            ),
        )
