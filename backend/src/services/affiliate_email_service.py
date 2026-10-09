from __future__ import annotations

from html import escape
from typing import Any, Optional

from ..config import Config
from ..models import User
from .email_service import EmailContent, SesEmailService, first_name_for

OFFER_TEXT = "20% off their first 3 months"


class AffiliateEmailService:
    """Emails for the creator program: new applications and review decisions."""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.email_service = SesEmailService(self.config)
        self.app_url = getattr(self.config, "app_base_url", "http://localhost:3107")

    async def send_applied_email(self, admin_email: str, applicant: User, application: dict[str, Any]) -> dict:
        return await self.email_service.send_email(
            admin_email, self._build_applied_email(applicant, application)
        )

    async def send_approved_email(self, user: User, slug: str) -> dict:
        return await self.email_service.send_email(user.email, self._build_approved_email(user, slug))

    async def send_declined_email(self, user: User, reason: Optional[str]) -> dict:
        return await self.email_service.send_email(user.email, self._build_declined_email(user, reason))

    def _build_applied_email(self, applicant: User, application: dict[str, Any]) -> EmailContent:
        rows = [
            ("Applicant", f"{applicant.name or ''} <{applicant.email}>".strip()),
            ("Code", str(application.get("slug") or "")),
            ("Platform", str(application.get("platform") or "")),
            ("Profile", str(application.get("profile_url") or "")),
            ("Audience", str(application.get("audience_size") or "")),
            ("Video", str(application.get("video_url") or "—")),
            ("Plan", str(application.get("promotion_plan") or "—")),
        ]
        review_url = f"{self.app_url}/admin#affiliates"
        return EmailContent(
            subject=f"New creator application: {application.get('slug')}",
            html=(
                "<p>A new creator program application is waiting for review.</p>"
                "<p>"
                + "<br>".join(f"<strong>{escape(label)}:</strong> {escape(value)}" for label, value in rows)
                + "</p>"
                f'<p><a href="{escape(review_url)}">Review it in the admin dashboard</a></p>'
            ),
            text=(
                "A new creator program application is waiting for review.\n\n"
                + "\n".join(f"{label}: {value}" for label, value in rows)
                + f"\n\nReview it: {review_url}\n"
            ),
        )

    def _build_approved_email(self, user: User, slug: str) -> EmailContent:
        first_name = first_name_for(first_name=user.first_name, full_name=user.name)
        link = f"{self.app_url}/?ref={slug}"
        code = slug.upper()
        return EmailContent(
            subject="You're in: welcome to the SupoClip creator program",
            html=(
                f"<p>Hi {escape(first_name)},</p>"
                "<p>Your creator program application was approved. Thanks for spreading the word about SupoClip!</p>"
                f'<p><strong>Your link:</strong> <a href="{escape(link)}">{escape(link)}</a><br>'
                f"<strong>Your code:</strong> {escape(code)}</p>"
                f"<p>Anyone who signs up through your link or enters your code gets {OFFER_TEXT}.</p>"
                "<p>Your iPhone link (for the SupoClip iOS app) will show up on your creator page once your code is live on the App Store.</p>"
                "<p>As a thank-you, your account now has SupoClip Pro for free while you're in the program.</p>"
                "<p>Please mark sponsored posts as such (for example #ad), and don't run ads on the SupoClip name "
                "or post your code on coupon sites.</p>"
                "<p>Team SupoClip</p>"
            ),
            text=(
                f"Hi {first_name},\n\n"
                "Your creator program application was approved. Thanks for spreading the word about SupoClip!\n\n"
                f"Your link: {link}\n"
                f"Your code: {code}\n\n"
                f"Anyone who signs up through your link or enters your code gets {OFFER_TEXT}.\n\n"
                "Your iPhone link (for the SupoClip iOS app) will show up on your creator page once your code is live on the App Store.\n\n"
                "As a thank-you, your account now has SupoClip Pro for free while you're in the program.\n\n"
                "Please mark sponsored posts as such (for example #ad), and don't run ads on the SupoClip name "
                "or post your code on coupon sites.\n\n"
                "Team SupoClip"
            ),
        )

    def _build_declined_email(self, user: User, reason: Optional[str]) -> EmailContent:
        first_name = first_name_for(first_name=user.first_name, full_name=user.name)
        reason = (reason or "").strip()
        return EmailContent(
            subject="Your SupoClip creator program application",
            html=(
                f"<p>Hi {escape(first_name)},</p>"
                "<p>Thanks for applying to the SupoClip creator program. We're not able to approve your application right now.</p>"
                + (f"<p><strong>Note from the team:</strong> {escape(reason)}</p>" if reason else "")
                + "<p>You're welcome to apply again in 30 days.</p>"
                "<p>Team SupoClip</p>"
            ),
            text=(
                f"Hi {first_name},\n\n"
                "Thanks for applying to the SupoClip creator program. We're not able to approve your application right now.\n\n"
                + (f"Note from the team: {reason}\n\n" if reason else "")
                + "You're welcome to apply again in 30 days.\n\n"
                "Team SupoClip"
            ),
        )
