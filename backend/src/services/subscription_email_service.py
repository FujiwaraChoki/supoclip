from __future__ import annotations

from datetime import timezone
from html import escape
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Config
from ..models import User
from .email_service import PRODUCT_UPDATE, TRANSACTIONAL, EmailContent, SesEmailService, first_name_for
from .product_update_email_service import ProductUpdateEmailService


class SubscriptionEmailService:
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.email_service = SesEmailService(self.config)

    @property
    def is_configured(self) -> bool:
        return self.email_service.is_configured

    async def send_subscribed_email(self, user: User) -> dict:
        content = self._build_subscribed_email(user)
        return await self.email_service.send_email(user.email, content)

    async def send_unsubscribed_email(self, user: User) -> dict:
        content = self._build_unsubscribed_email(user)
        return await self.email_service.send_email(user.email, content)

    async def send_cancellation_scheduled_email(self, user: User, db: AsyncSession) -> dict:
        content = self._build_cancellation_scheduled_email(user, include_retention_offer=True)
        result = await ProductUpdateEmailService(db, self.config).send_product_update(user.id, content)
        if result.get("skipped"):
            # Confirm the cancellation even when the customer has opted out of
            # promotional emails. The discount belongs only in the optional mail.
            confirmation = self._build_cancellation_scheduled_email(user)
            return await self.email_service.send_email(user.email, confirmation)
        return result

    def _build_cancellation_scheduled_email(
        self, user: User, *, include_retention_offer: bool = False,
    ) -> EmailContent:
        if user.subscription_cancel_at is None:
            raise ValueError("A scheduled cancellation date is required")
        # Promotional mail personalizes names through ProductUpdateEmailService,
        # which escapes HTML and adds the signed unsubscribe link and headers.
        first_name = "{{first_name}}" if include_retention_offer else self._first_name_for(user)
        end_date = user.subscription_cancel_at.astimezone(timezone.utc).strftime(
            "%B %d, %Y at %H:%M UTC"
        )
        paragraphs = [
            "Your SupoClip cancellation is scheduled. Thanks for giving us a try.",
            "What would have made SupoClip more useful to you? Just hit reply—a quick note "
            "about what was missing or what got in your way would mean a lot.",
            "If a clip didn't turn out as expected or you got stuck somewhere, tell us "
            "what happened. We'd be happy to take a look.",
        ]
        if include_retention_offer:
            paragraphs.append(
                "If you decide to give SupoClip another try, we'd love to welcome you back "
                "with 30% off for 3 months. Just reply to this email and we'll get the "
                "discount set up for you."
            )
        closing = (
            f"P.S. Your subscription won't renew, and you'll keep access to your current plan "
            f"until {end_date}. You can undo the cancellation in Settings before then."
        )
        return EmailContent(
            subject="A quick question about your SupoClip experience",
            reply_to=("support@supoclip.com",),
            category=PRODUCT_UPDATE if include_retention_offer else TRANSACTIONAL,
            html=(
                f"<p>Hey {escape(first_name)},</p>"
                + "".join(f"<p>{escape(paragraph)}</p>" for paragraph in paragraphs)
                + "<p>Best,<br>Team SupoClip</p>"
                + f"<p>{escape(closing)}</p>"
            ),
            text=(
                f"Hey {first_name},\n\n"
                + "\n\n".join(paragraphs)
                + f"\n\nBest,\nTeam SupoClip\n\n{closing}"
            ),
        )

    def _build_subscribed_email(self, user: User) -> EmailContent:
        first_name = self._first_name_for(user)
        return EmailContent(
            subject="Thanks for subscribing to SupoClip",
            html=(
                f"<p>Hi {first_name},</p>"
                "<p>Thanks for subscribing to SupoClip.</p>"
                "<p>Your paid plan is now active, and you can jump back in anytime to create more clips.</p>"
                "<p>We’re excited to have you with us.</p>"
                "<p>Team SupoClip</p>"
            ),
            text=(
                f"Hi {first_name},\n\n"
                "Thanks for subscribing to SupoClip.\n\n"
                "Your paid plan is now active, and you can jump back in anytime to create more clips.\n\n"
                "We’re excited to have you with us.\n\n"
                "Team SupoClip"
            ),
        )

    def _build_unsubscribed_email(self, user: User) -> EmailContent:
        first_name = self._first_name_for(user)
        return EmailContent(
            subject="Sorry to see you go from SupoClip",
            html=(
                f"<p>Hi {first_name},</p>"
                "<p>Sorry to see you go, and thanks for trying SupoClip.</p>"
                "<p>Your subscription has been canceled. If you ever want to come back, we’d love to have you.</p>"
                "<p>Team SupoClip</p>"
            ),
            text=(
                f"Hi {first_name},\n\n"
                "Sorry to see you go, and thanks for trying SupoClip.\n\n"
                "Your subscription has been canceled. If you ever want to come back, we’d love to have you.\n\n"
                "Team SupoClip"
            ),
        )

    @staticmethod
    def _first_name_for(user: User) -> str:
        return first_name_for(first_name=user.first_name, full_name=user.name)
