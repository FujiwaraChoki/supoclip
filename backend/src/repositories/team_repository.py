"""Team membership and lookup helpers for multi-tenant backend authorization."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


TEAM_ROLES = frozenset({"owner", "admin", "member"})
TEAM_MANAGER_ROLES = frozenset({"owner", "admin"})


class TeamRepository:
    """Read-focused team queries used for backend auth, billing, and isolation."""

    @staticmethod
    async def get_member_role(
        db: AsyncSession, team_id: str, user_id: str
    ) -> Optional[str]:
        """Return the user's role in `team_id` ('owner'|'admin'|'member'), or None."""
        if not team_id or not user_id:
            return None
        result = await db.execute(
            text(
                """
                SELECT role
                FROM team_members
                WHERE team_id = :team_id AND user_id = :user_id
                LIMIT 1
                """
            ),
            {"team_id": team_id, "user_id": user_id},
        )
        row = result.fetchone()
        return str(row.role).lower() if row and row.role else None

    @staticmethod
    async def is_member(db: AsyncSession, team_id: str, user_id: str) -> bool:
        return (await TeamRepository.get_member_role(db, team_id, user_id)) is not None

    @staticmethod
    async def get_team_by_id(
        db: AsyncSession, team_id: str
    ) -> Optional[Dict[str, Any]]:
        result = await db.execute(
            text(
                """
                SELECT
                    id, name, slug, owner_id, plan, subscription_status,
                    subscription_provider, stripe_customer_id, stripe_account_id,
                    stripe_subscription_id, seat_limit, billing_period_start,
                    billing_period_end, subscription_cancel_at, pending_plan,
                    pending_plan_invoice_id, created_at, updated_at
                FROM teams
                WHERE id = :team_id
                LIMIT 1
                """
            ),
            {"team_id": team_id},
        )
        row = result.fetchone()
        if not row:
            return None
        return {
            "id": row.id,
            "name": row.name,
            "slug": row.slug,
            "owner_id": row.owner_id,
            "plan": (row.plan or "free").lower(),
            "subscription_status": (row.subscription_status or "inactive").lower(),
            "subscription_provider": (
                row.subscription_provider.lower() if row.subscription_provider else None
            ),
            "stripe_customer_id": row.stripe_customer_id,
            "stripe_account_id": row.stripe_account_id,
            "stripe_subscription_id": row.stripe_subscription_id,
            "seat_limit": int(row.seat_limit or 3),
            "billing_period_start": row.billing_period_start,
            "billing_period_end": row.billing_period_end,
            "cancel_at": row.subscription_cancel_at,
            "pending_plan": row.pending_plan,
            "pending_plan_invoice_id": row.pending_plan_invoice_id,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    async def list_user_teams(
        db: AsyncSession, user_id: str
    ) -> List[Dict[str, Any]]:
        result = await db.execute(
            text(
                """
                SELECT t.id, t.name, t.slug, t.owner_id, t.plan,
                       t.subscription_status, t.seat_limit, tm.role
                FROM team_members tm
                JOIN teams t ON t.id = tm.team_id
                WHERE tm.user_id = :user_id
                ORDER BY t.created_at ASC
                """
            ),
            {"user_id": user_id},
        )
        return [
            {
                "id": row.id,
                "name": row.name,
                "slug": row.slug,
                "owner_id": row.owner_id,
                "plan": (row.plan or "free").lower(),
                "subscription_status": (row.subscription_status or "inactive").lower(),
                "seat_limit": int(row.seat_limit or 3),
                "role": str(row.role).lower(),
            }
            for row in result.fetchall()
        ]
