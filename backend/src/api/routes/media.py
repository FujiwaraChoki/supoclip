"""
Media API routes (fonts, transitions, uploads).
"""

from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse
from pathlib import Path
from typing import Any, cast
import logging
import uuid
import aiofiles

from ...config import Config
from ...database import get_db
from ...auth_headers import get_signed_user_id, USER_ID_HEADER
from ...services.billing_service import BillingService
from ...font_registry import (
    FONTS_DIR,
    SUPPORTED_FONT_EXTENSIONS,
    build_user_font_stem,
    find_font_path,
    get_available_fonts as list_available_fonts,
    get_user_fonts_dir,
    sanitize_font_stem,
)
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends

logger = logging.getLogger(__name__)
config = Config()
router = APIRouter(tags=["media"])


def _get_authenticated_user_id(request: Request) -> str:
    if config.monetization_enabled:
        return get_signed_user_id(request, config)

    # Self-hosted: accept user_id or x-supoclip-user-id (frontend uses buildBackendAuthHeaders)
    user_id = request.headers.get("user_id") or request.headers.get(USER_ID_HEADER)
    if not user_id:
        raise HTTPException(status_code=401, detail="User authentication required")
    return user_id


@router.get("/fonts")
async def get_available_fonts_route(request: Request):
    """Get list of available fonts."""
    try:
        user_id = _get_authenticated_user_id(request)
        if not FONTS_DIR.exists():
            return {"fonts": [], "message": "Fonts directory not found"}

        fonts = list_available_fonts(user_id=user_id)
        logger.info(f"Found {len(fonts)} available fonts")
        return {"fonts": fonts}

    except Exception as e:
        logger.error(f"Error retrieving fonts: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error retrieving fonts: {str(e)}")


@router.get("/fonts/{font_name}")
async def get_font_file(font_name: str, request: Request):
    """Serve a specific font file."""
    try:
        user_id = _get_authenticated_user_id(request)
        font_path = find_font_path(font_name, user_id=user_id)

        if not font_path:
            raise HTTPException(status_code=404, detail="Font not found")

        media_type = "font/ttf" if font_path.suffix.lower() == ".ttf" else "font/otf"

        return FileResponse(
            path=str(font_path),
            media_type=media_type,
            headers={
                "Cache-Control": "public, max-age=31536000",
                "Access-Control-Allow-Origin": "*",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving font {font_name}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error serving font: {str(e)}")


@router.post("/fonts/upload")
async def upload_font(
    request: Request,
    uploaded_file: UploadFile = File(..., alias="file"),
    db: AsyncSession = Depends(get_db),
):
    """Upload a custom .ttf/.otf font so it appears in the font picker."""
    try:
        user_id = _get_authenticated_user_id(request)
        billing_service = BillingService(db)
        summary = await billing_service.get_usage_summary(user_id)
        pro_access = not summary.get("monetization_enabled") or (
            summary.get("plan") == "pro"
            and summary.get("subscription_status") in {"active", "trialing"}
        )
        if not pro_access:
            raise HTTPException(
                status_code=403,
                detail="Custom font uploads are available for Pro users only",
            )

        if not uploaded_file.filename:
            raise HTTPException(status_code=400, detail="Missing file name")

        uploaded_filename = uploaded_file.filename or "font.ttf"
        extension = Path(uploaded_filename).suffix.lower()
        if extension not in SUPPORTED_FONT_EXTENSIONS:
            raise HTTPException(
                status_code=400, detail="Only .ttf and .otf fonts are supported"
            )

        user_fonts_dir = get_user_fonts_dir(user_id)
        user_fonts_dir.mkdir(parents=True, exist_ok=True)

        original_stem = sanitize_font_stem(uploaded_filename)
        stored_stem = build_user_font_stem(user_id, original_stem)
        target_path = user_fonts_dir / f"{stored_stem}{extension}"
        suffix = 2
        while target_path.exists():
            target_path = user_fonts_dir / f"{stored_stem}-{suffix}{extension}"
            suffix += 1

        content = await uploaded_file.read()
        async with aiofiles.open(target_path, "wb") as uploaded_font:
            await uploaded_font.write(content)

        logger.info(f"Uploaded font: {target_path.name}")

        return {
            "font": {
                "name": target_path.stem,
                "display_name": original_stem.replace("-", " ")
                .replace("_", " ")
                .title(),
                "filename": target_path.name,
                "format": extension.lstrip("."),
                "scope": "user",
            },
            "message": "Font uploaded successfully",
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error uploading font: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error uploading font: {str(e)}")


@router.get("/transitions")
async def get_available_transitions():
    """Get list of available transition effects."""
    try:
        from ...video_utils import get_available_transitions

        transitions = get_available_transitions()

        transition_info = []
        for transition_path in transitions:
            transition_file = Path(transition_path)
            transition_info.append(
                {
                    "name": transition_file.stem,
                    "display_name": transition_file.stem.replace("_", " ")
                    .replace("-", " ")
                    .title(),
                    "file_path": transition_path,
                }
            )

        logger.info(f"Found {len(transition_info)} available transitions")
        return {"transitions": transition_info}

    except Exception as e:
        logger.error(f"Error retrieving transitions: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error retrieving transitions: {str(e)}"
        )


@router.get("/caption-templates")
async def get_caption_templates():
    """Get available caption templates.

    Returns a stable default list if optional template module is unavailable.
    """
    default_templates = [
        {
            "id": "default",
            "name": "Default",
            "description": "Clean subtitle style",
            "animation": "none",
            "font_family": "TikTokSans-Regular",
            "font_size": 24,
            "font_color": "#FFFFFF",
        }
    ]

    try:
        from ...caption_templates import get_template_info

        templates = get_template_info()
        return {"templates": templates or default_templates}
    except Exception:
        return {"templates": default_templates}


@router.get("/broll/status")
async def get_broll_status():
    """Return whether B-roll integrations are configured."""
    return {
        "configured": bool(config.pexels_api_key),
        "provider": "pexels" if config.pexels_api_key else None,
    }


@router.post("/upload")
async def upload_video(request: Request):
    """Upload a video to the server."""
    try:
        # Get the form data
        form_data = await request.form()
        video_file = cast(Any, form_data.get("video"))

        if not getattr(video_file, "filename", None) or not hasattr(video_file, "read"):
            raise HTTPException(status_code=400, detail="No video file provided")

        upload = cast(UploadFile, video_file)
        upload_filename = upload.filename or "upload.mp4"

        # Create uploads directory
        uploads_dir = Path(config.temp_dir) / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)

        # Generate unique filename
        file_extension = Path(upload_filename).suffix
        unique_filename = f"{uuid.uuid4()}{file_extension}"
        video_path = uploads_dir / unique_filename

        # Save the uploaded file
        async with aiofiles.open(video_path, "wb") as f:
            content = await upload.read()
            await f.write(content)

        logger.info(f"✅ Video uploaded successfully to: {video_path}")

        return {"message": "Video uploaded successfully", "video_path": str(video_path)}
    except Exception as e:
        logger.error(f"❌ Error uploading video: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error uploading video: {str(e)}")
