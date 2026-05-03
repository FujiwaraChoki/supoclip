from __future__ import annotations

from fastapi import HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .auth_headers import get_authenticated_user_id
from .config import Config


async def require_admin_user(
    request: Request, db: AsyncSession, config: Config
) -> str:
    user_id = get_authenticated_user_id(request, config)

    result = await db.execute(
        text("SELECT is_admin FROM users WHERE id = :user_id"),
        {"user_id": user_id},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="User not found")
    if not bool(getattr(row, "is_admin", False)):
        raise HTTPException(status_code=403, detail="Admin access required")
    return str(user_id)
