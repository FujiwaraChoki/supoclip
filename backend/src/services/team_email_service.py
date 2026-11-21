"""Transactional invitation email for Team workspaces."""

from __future__ import annotations

from html import escape

from ..config import Config
from .email_layout import email_button
from .email_service import EmailContent, SesEmailService


def build_team_invitation_email(
    *,
    team_name: str,
    inviter_name: str,
    role: str,
    invite_url: str,
) -> EmailContent:
    safe_team = team_name.strip() or "a SupoClip team"
    safe_inviter = inviter_name.strip() or "A teammate"
    role_label = "an admin" if role == "admin" else "a member"
    subject = f"You're invited to join {safe_team} on SupoClip"

    text = (
        f"Hi there,\n\n"
        f"{safe_inviter} invited you to join {safe_team} on SupoClip as {role_label}.\n\n"
        f"Accept your invitation:\n{invite_url}\n\n"
        f"This link expires in 7 days. If you didn't expect this invitation, you can ignore this email.\n"
    )
    html = (
        f"<p>Hi there,</p>"
        f"<p><strong>{escape(safe_inviter)}</strong> invited you to join "
        f"<strong>{escape(safe_team)}</strong> on SupoClip as {escape(role_label)}.</p>"
        f"{email_button(invite_url, f'Join {safe_team}')}"
        f"<p>This link expires in 7 days. If you weren't expecting this invitation, "
        f"you can safely ignore this email.</p>"
    )
    return EmailContent(subject=subject, html=html, text=text)


class TeamEmailService:
    def __init__(self, config: Config | None = None):
        self.config = config or Config()
        self.email_service = SesEmailService(self.config)

    @property
    def is_configured(self) -> bool:
        return self.email_service.is_configured

    async def send_invitation_email(
        self,
        *,
        recipient: str,
        team_name: str,
        inviter_name: str,
        role: str,
        invite_url: str,
    ) -> dict:
        content = build_team_invitation_email(
            team_name=team_name,
            inviter_name=inviter_name,
            role=role,
            invite_url=invite_url,
        )
        return await self.email_service.send_email(recipient, content)
