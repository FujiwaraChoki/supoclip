from html import escape
from typing import Optional

from ..config import Config
from ..models import User
from .email_service import EmailContent, SesEmailService, first_name_for

CODE_MINUTES = 10


class EmailChangeEmailService:
    """Emails for a self-serve email change (frontend server/email-change.ts).

    The frontend generates the code and keeps only its HMAC; this service only
    delivers it. Bodies hold a live code, so they are never logged.
    """

    def __init__(self, config: Optional[Config] = None):
        self.email_service = SesEmailService(config)

    async def send_code(self, user: User, recipient: str, code: str, *, new_address: bool) -> dict:
        # The subject stays code-free: email_deliveries records subjects.
        if new_address:
            # Whoever typed this address chose the account name, so it stays out of the mail.
            greeting = "Hi"
            subject = "Confirm your new SupoClip email"
            reason = "You asked to use this address for a SupoClip account. Enter this code to confirm it:"
        else:
            greeting = f"Hi {first_name_for(first_name=user.first_name, full_name=user.name)}"
            subject = "Your code to change your SupoClip email"
            reason = "Someone, hopefully you, asked to change the email address on your SupoClip account. Enter this code to continue:"
        footer = (
            f"The code expires in {CODE_MINUTES} minutes. If you didn't ask for this, "
            "ignore this email. Nothing changes without the code."
        )
        content = EmailContent(
            subject=subject,
            html=(
                f"<p>{escape(greeting)},</p><p>{escape(reason)}</p>"
                '<p style="font-size:28px;font-weight:700;letter-spacing:6px;margin:24px 0">'
                f"{escape(code)}</p>"
                f"<p>{escape(footer)}</p><p>Team SupoClip</p>"
            ),
            text=f"{greeting},\n\n{reason}\n\n{code}\n\n{footer}\n\nTeam SupoClip",
        )
        return await self.email_service.send_email(recipient, content)

    async def send_changed_notice(self, user: User, old_email: str, new_email: str) -> dict:
        name = first_name_for(first_name=user.first_name, full_name=user.name)
        body = (
            f"The email address on your SupoClip account was changed from {old_email} "
            f"to {new_email}. Sign-in, receipts and notifications now go to the new address, "
            "and other devices were signed out."
        )
        warning = (
            "If you didn't make this change, reply to this email right away so we can "
            "secure your account."
        )
        content = EmailContent(
            subject="Your SupoClip email address was changed",
            html=(
                f"<p>Hi {escape(name)},</p><p>{escape(body)}</p>"
                f"<p>{escape(warning)}</p><p>Team SupoClip</p>"
            ),
            text=f"Hi {name},\n\n{body}\n\n{warning}\n\nTeam SupoClip",
            reply_to=("support@supoclip.com",),
        )
        return await self.email_service.send_email(old_email, content)
