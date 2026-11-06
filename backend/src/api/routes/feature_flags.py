"""``GET /feature-flags``: every feature flag, evaluated for the caller and platform."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import resolve_authenticated_user_id
from ...config import get_config
from ...database import get_db
from ...feature_flags import client_platform, evaluated_flags

router = APIRouter(tags=["feature flags"])


@router.get("/feature-flags")
async def get_feature_flags(request: Request, db: AsyncSession = Depends(get_db)):
    """``{"platform": "web" | "ios", "flags": {key: bool}}``.

    Only the flags themselves: a feature may have its own gate on top
    (workflows also need WORKFLOWS_ACCESS, so clients read ``GET /tasks/workflows``).
    """
    user_id = await resolve_authenticated_user_id(request, db, get_config())
    platform = client_platform(request)
    return {"platform": platform, "flags": await evaluated_flags(db, platform=platform, user_id=user_id)}
