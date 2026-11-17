"""Tables and columns owned by the web app's Prisma schema.

Hosted SupoClip has them; a self-hosted or test database built from the backend's
own migrations may not. Queries check here instead of assuming.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def has_table(db: AsyncSession, name: str) -> bool:
    return bool((await db.execute(text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f"public.{name}"})).scalar())


async def has_column(db: AsyncSession, table: str, column: str) -> bool:
    row = (
        await db.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :table AND column_name = :column"
            ),
            {"table": table, "column": column},
        )
    ).first()
    return row is not None


async def account_column(db: AsyncSession, alias: str = "u") -> str:
    """SQL for a user's Stripe account id, or NULL where the column doesn't exist."""
    return f"{alias}.stripe_account_id" if await has_column(db, "users", "stripe_account_id") else "CAST(NULL AS VARCHAR)"
