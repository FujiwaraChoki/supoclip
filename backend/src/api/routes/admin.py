from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...admin_auth import require_admin_user
from ...config import get_config
from ...database import get_db

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/health")
async def admin_health(
    request: Request, db: AsyncSession = Depends(get_db)
):
    await require_admin_user(request, db, get_config())
    return {"status": "ok"}
