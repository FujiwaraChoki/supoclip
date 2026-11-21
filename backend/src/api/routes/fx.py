"""Dynamic-edit asset routes (§5.6).

``GET /fx/assets`` lists the built-in SFX, music beds and emoji atlas plus this machine's drop-ins (flagged
``custom: true``). Entries carry content-addressed URLs and never a file path. ``GET /fx/assets/{sha256}.{ext}`` serves
one of those files with immutable caching. Only hashes the registry knows are served, and no path is built from the
request. Auth matches ``GET /fonts/{name}``. Neither route checks the ``dynamic_edits`` flag: rendering and previewing
existing edits is never gated (§0.4.8).
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import resolve_authenticated_user_id
from ...config import get_config
from ...database import get_db
from ...edit_fx import assets
from ...utils.async_helpers import run_in_thread

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/fx", tags=["fx"])

IMMUTABLE_CACHE = "public, max-age=31536000, immutable"
# Drop-ins can change on the API machine, so the list itself is revalidated.
MANIFEST_CACHE = "private, no-cache"
MEDIA_TYPES = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "ogg": "audio/ogg",
    "png": "image/png",
    "json": "application/json",
}
_ASSET_NAME = re.compile(r"^([0-9a-f]{64})\.([a-z0-9]{2,4})$")


async def _authenticate(request: Request, db: AsyncSession) -> str:
    return await resolve_authenticated_user_id(request, db, get_config())


@router.get("/assets")
async def get_fx_assets(request: Request, db: AsyncSession = Depends(get_db, scope="function")):
    """The asset manifest: built-ins plus this machine's drop-ins, URLs only."""
    await _authenticate(request, db)
    try:
        # Unmeasured drop-ins are measured with ffmpeg on first sight: keep that off the event loop.
        manifest = await run_in_thread(assets.public_manifest)
    except Exception:
        logger.exception("fx_assets_manifest_failed")
        raise HTTPException(status_code=500, detail="Edit assets are unavailable") from None
    return JSONResponse(manifest, headers={"Cache-Control": MANIFEST_CACHE})


@router.get("/assets/{filename}")
async def get_fx_asset_file(
    filename: str,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """One asset by content hash. Unknown hashes, extensions and anything path-like are 404."""
    await _authenticate(request, db)
    match = _ASSET_NAME.match(filename or "")
    if match is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    sha256, ext = match.groups()
    path = await run_in_thread(assets.asset_file, sha256, ext)
    if path is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(
        path=str(path),
        media_type=MEDIA_TYPES.get(ext, "application/octet-stream"),
        headers={"Cache-Control": IMMUTABLE_CACHE, "Access-Control-Allow-Origin": "*"},
    )
