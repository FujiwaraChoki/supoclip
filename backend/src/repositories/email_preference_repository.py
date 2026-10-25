"""
Email preference repository - product-update opt-outs on the users table.
"""

from typing import Any, Dict, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class EmailPreferenceRepository:
    """Repository for email preference database operations."""

    @staticmethod
    async def get_product_update_recipient(db: AsyncSession, user_id: str) -> Optional[Dict[str, Any]]:
        """The recipient's current address, names and product-update opt-out."""
        result = await db.execute(
            text(
                """
                SELECT id, email, first_name, name, product_updates_unsubscribed_at
                FROM users
                WHERE id = :user_id
                """
            ),
            {"user_id": user_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    @staticmethod
    async def unsubscribe_from_product_updates(db: AsyncSession, user_id: str, email: str) -> bool:
        """Record the opt-out once (repeats keep the first timestamp). False if the user or address changed."""
        result = await db.execute(
            text(
                """
                UPDATE users
                SET product_updates_unsubscribed_at = COALESCE(product_updates_unsubscribed_at, NOW())
                WHERE id = :user_id AND email = :email
                """
            ),
            {"user_id": user_id, "email": email},
        )
        if result.rowcount != 1:
            await db.rollback()
            return False
        await db.commit()
        return True
