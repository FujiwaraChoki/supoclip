from html import escape
from typing import Optional

from ..config import Config
from ..models import User
from .email_service import EmailContent, SesEmailService, first_name_for


class VerificationEmailService:
    """Deliver Better Auth's signed, one-hour email ownership link."""

    def __init__(self, config: Optional[Config] = None):
        self.email_service = SesEmailService(config)

    async def send_verification_email(self, user: User, url: str) -> dict:
        name = first_name_for(first_name=user.first_name, full_name=user.name)
        content = EmailContent(
            subject="Verify your SupoClip email address",
            html=(
                f"<p>Hi {escape(name)},</p>"
                "<p>Verify your email address to create SupoClip API keys.</p>"
                f'<p><a href="{escape(url, quote=True)}">Verify email address</a></p>'
                "<p>This link expires in 1 hour. If you did not request this, "
                "you can ignore this email.</p><p>Team SupoClip</p>"
            ),
            text=(
                f"Hi {name},\n\nVerify your email address to create SupoClip API keys.\n\n"
                f"Verify email address: {url}\n\n"
                "This link expires in 1 hour. If you did not request this, "
                "you can ignore this email.\n\nTeam SupoClip"
            ),
        )
        return await self.email_service.send_email(user.email, content)
