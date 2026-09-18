"""
Media API routes (fonts, transitions, uploads).
"""

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse
from pathlib import Path
from typing import Any, cast, Optional
import logging
import uuid
import aiofiles
import base64
import tempfile

from ...config import get_config
from ...database import get_db
from ...auth_headers import resolve_authenticated_user_id
from ...services.billing_service import BillingService
from ...font_registry import (
    FONTS_DIR,
    SUPPORTED_FONT_EXTENSIONS,
    build_user_font_stem,
    find_font_path,
    find_user_font_path,
    get_available_fonts as list_available_fonts,
    get_user_fonts_dir,
    sanitize_font_stem,
)
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends

logger = logging.getLogger(__name__)
router = APIRouter(tags=["media"])
MAX_VIDEO_UPLOAD_BYTES = 1_000_000_000
MAX_FONT_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_LOGO_UPLOAD_BYTES = 5 * 1024 * 1024  # 5MB for logos
LOGO_DIR = Path(__file__).parent.parent.parent.parent / "logos"
LOGO_USERS_DIR = LOGO_DIR / "users"


async def _get_authenticated_user_id(request: Request, db: AsyncSession) -> str:
    config = get_config()
    return await resolve_authenticated_user_id(request, db, config)


async def _write_upload_to_disk(
    uploaded_file: UploadFile,
    target_path: Path,
    max_bytes: int,
) -> None:
    chunk_size = 1024 * 1024
    written = 0

    try:
        async with aiofiles.open(target_path, "wb") as destination:
            while True:
                chunk = await uploaded_file.read(chunk_size)
                if not chunk:
                    break

                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(
                        status_code=413, detail="Uploaded file is too large"
                    )

                await destination.write(chunk)
    except Exception:
        if target_path.exists():
            target_path.unlink(missing_ok=True)
        raise


@router.get("/fonts")
async def get_available_fonts_route(
    request: Request, db: AsyncSession = Depends(get_db)
):
    """Get list of available fonts."""
    try:
        user_id = await _get_authenticated_user_id(request, db)
        if not FONTS_DIR.exists():
            return {"fonts": [], "message": "Fonts directory not found"}

        fonts = list_available_fonts(user_id=user_id)
        logger.info(f"Found {len(fonts)} available fonts")
        return {"fonts": fonts}

    except Exception as e:
        logger.error(f"Error retrieving fonts: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error retrieving fonts: {str(e)}")


@router.get("/fonts/{font_name}")
async def get_font_file(
    font_name: str,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Serve a specific font file."""
    try:
        user_id = await _get_authenticated_user_id(request, db)
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
        user_id = await _get_authenticated_user_id(request, db)
        billing_service = BillingService(db)
        summary = await billing_service.get_usage_summary(user_id)
        paid_access = not summary.get("monetization_enabled") or (
            summary.get("plan") in {"pro", "scale"}
            and summary.get("subscription_status") in {"active", "trialing"}
        )
        if not paid_access:
            raise HTTPException(
                status_code=403,
                detail="Custom font uploads are available for paid plans only",
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

        await _write_upload_to_disk(uploaded_file, target_path, MAX_FONT_UPLOAD_BYTES)

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


@router.delete("/fonts/{font_name}")
async def delete_font(
    font_name: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Delete a custom font owned by the authenticated user."""
    try:
        user_id = await _get_authenticated_user_id(request, db)
        font_path = find_user_font_path(font_name, user_id)

        if font_path is None:
            if find_font_path(font_name) is not None:
                raise HTTPException(
                    status_code=403, detail="Bundled system fonts cannot be deleted"
                )
            raise HTTPException(status_code=404, detail="Custom font not found")

        deleted_name = font_path.stem
        font_path.unlink(missing_ok=True)
        logger.info("Deleted custom font %s for user %s", font_path.name, user_id)
        return {"font_name": deleted_name, "message": "Font deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error deleting font %s: %s", font_name, e)
        raise HTTPException(status_code=500, detail=f"Error deleting font: {str(e)}")


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
    config = get_config()
    return {
        "configured": bool(config.pexels_api_key),
        "provider": "pexels" if config.pexels_api_key else None,
    }


@router.post("/upload")
async def upload_video(request: Request, db: AsyncSession = Depends(get_db)):
    """Upload a video to the server."""
    try:
        await _get_authenticated_user_id(request, db)
        config = get_config()

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
        await _write_upload_to_disk(upload, video_path, MAX_VIDEO_UPLOAD_BYTES)

        logger.info(f"✅ Video uploaded successfully to: {video_path}")

        return {
            "message": "Video uploaded successfully",
            "video_path": f"upload://{unique_filename}",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error uploading video: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error uploading video: {str(e)}")


# ============================================================================
# LOGO MANAGEMENT ENDPOINTS
# ============================================================================

def _get_user_logos_dir(user_id: str) -> Path:
    """Get the logos directory for a specific user."""
    safe_user_id = "".join(c if c.isalnum() or c in "-_" else "-" for c in user_id)
    user_dir = LOGO_USERS_DIR / safe_user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def _validate_png_transparency(file_path: Path) -> bool:
    """Validate that PNG has alpha channel (transparency)."""
    try:
        from PIL import Image
        with Image.open(file_path) as img:
            return img.mode in ('RGBA', 'LA') or 'A' in img.getbands()
    except Exception:
        return False


@router.get("/logos")
async def get_logos(
    request: Request, db: AsyncSession = Depends(get_db)
):
    """List all logos for the authenticated user."""
    try:
        user_id = await _get_authenticated_user_id(request, db)
        user_logos_dir = _get_user_logos_dir(user_id)

        logos = []
        for logo_file in user_logos_dir.glob("*.png"):
            try:
                from PIL import Image
                with Image.open(logo_file) as img:
                    width, height = img.size
                    has_alpha = img.mode in ('RGBA', 'LA') or 'A' in img.getbands()
            except Exception:
                width, height = 0, 0
                has_alpha = False

            logos.append({
                "name": logo_file.stem,
                "filename": logo_file.name,
                "path": f"logos/users/{user_logos_dir.name}/{logo_file.name}",
                "width": width,
                "height": height,
                "has_transparency": has_alpha,
                "size_bytes": logo_file.stat().st_size,
            })

        return {"logos": logos}
    except Exception as e:
        logger.error(f"Error retrieving logos: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error retrieving logos: {str(e)}")


@router.post("/logos/upload")
async def upload_logo(
    request: Request,
    uploaded_file: UploadFile = File(..., alias="file"),
    db: AsyncSession = Depends(get_db),
):
    """Upload a logo PNG with transparency."""
    try:
        user_id = await _get_authenticated_user_id(request, db)

        if not uploaded_file.filename:
            raise HTTPException(status_code=400, detail="Missing file name")

        uploaded_filename = uploaded_file.filename or "logo.png"
        extension = Path(uploaded_filename).suffix.lower()
        if extension != ".png":
            raise HTTPException(status_code=400, detail="Only PNG files are supported for logos")

        user_logos_dir = _get_user_logos_dir(user_id)

        # Sanitize filename
        original_stem = Path(uploaded_filename).stem
        safe_stem = "".join(c if c.isalnum() or c in "-_" else "-" for c in original_stem).strip("-")
        if not safe_stem:
            safe_stem = "logo"

        target_path = user_logos_dir / f"{safe_stem}.png"
        suffix = 2
        while target_path.exists():
            target_path = user_logos_dir / f"{safe_stem}-{suffix}.png"
            suffix += 1

        # Write file
        await _write_upload_to_disk(uploaded_file, target_path, MAX_LOGO_UPLOAD_BYTES)

        # Validate transparency
        if not _validate_png_transparency(target_path):
            target_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=400,
                detail="PNG must have transparency (alpha channel). Please upload a PNG with transparent background."
            )

        # Get dimensions
        from PIL import Image
        with Image.open(target_path) as img:
            width, height = img.size

        logger.info(f"Uploaded logo: {target_path.name} ({width}x{height})")

        return {
            "logo": {
                "name": target_path.stem,
                "filename": target_path.name,
                "path": f"logos/users/{user_logos_dir.name}/{target_path.name}",
                "width": width,
                "height": height,
                "has_transparency": True,
                "size_bytes": target_path.stat().st_size,
            },
            "message": "Logo uploaded successfully",
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error uploading logo: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error uploading logo: {str(e)}")


@router.delete("/logos/{logo_name}")
async def delete_logo(
    logo_name: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Delete a logo owned by the authenticated user."""
    try:
        user_id = await _get_authenticated_user_id(request, db)
        user_logos_dir = _get_user_logos_dir(user_id)

        # Find the logo file (with or without extension)
        logo_path = user_logos_dir / logo_name
        if not logo_path.exists() and not logo_path.suffix:
            logo_path = user_logos_dir / f"{logo_name}.png"

        if not logo_path.exists():
            raise HTTPException(status_code=404, detail="Logo not found")

        deleted_name = logo_path.stem
        logo_path.unlink(missing_ok=True)
        logger.info("Deleted logo %s for user %s", logo_path.name, user_id)
        return {"logo_name": deleted_name, "message": "Logo deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error deleting logo %s: %s", logo_name, e)
        raise HTTPException(status_code=500, detail=f"Error deleting logo: {str(e)}")


@router.post("/logos/{logo_name}/default")
async def set_default_logo(
    logo_name: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Set a logo as the default for the user's tasks."""
    try:
        user_id = await _get_authenticated_user_id(request, db)
        user_logos_dir = _get_user_logos_dir(user_id)

        # Find the logo file
        logo_path = user_logos_dir / logo_name
        if not logo_path.exists() and not logo_path.suffix:
            logo_path = user_logos_dir / f"{logo_name}.png"

        if not logo_path.exists():
            raise HTTPException(status_code=404, detail="Logo not found")

        # Return the relative path to be stored in Task.logo_path
        relative_path = f"logos/users/{user_logos_dir.name}/{logo_path.name}"
        return {"logo_path": relative_path, "message": "Default logo set successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error setting default logo %s: %s", logo_name, e)
        raise HTTPException(status_code=500, detail=f"Error setting default logo: {str(e)}")


# ============================================================================
# PREVIEW FRAME ENDPOINT
# ============================================================================

@router.post("/tasks/{task_id}/preview-frame")
async def generate_preview_frame(
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Generate a single preview frame with visual identity applied."""
    try:
        from ...services.task_service import TaskService
        from ...video_utils import create_optimized_clip
        from ...config import get_config
        from pathlib import Path
        import subprocess

        user_id = await _get_authenticated_user_id(request, db)

        # Parse optional overrides from request body
        body = await request.json()
        logo_path_override = body.get("logo_path")
        theme_text_override = body.get("theme_text")
        theme_font_size_override = body.get("theme_font_size")
        theme_position_override = body.get("theme_position")
        theme_alignment_override = body.get("theme_alignment")

        task_service = TaskService(db)
        task = await task_service.task_repo.get_task_by_id(db, task_id)

        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        if task.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Not authorized for this task")

        # Get source video path
        source_url = task.get("source_url")
        source_type = task.get("source_type")

        if not source_url or not source_type:
            # Try to get from Redis metadata
            import redis.asyncio as redis
            import json
            runtime_config = get_config()
            redis_client = redis.Redis(
                host=runtime_config.redis_host,
                port=runtime_config.redis_port,
                password=runtime_config.redis_password,
                decode_responses=True,
            )
            try:
                payload = await redis_client.get(f"task_source:{task_id}")
                if payload:
                    metadata = json.loads(payload)
                    source_url = metadata.get("url") or source_url
                    source_type = metadata.get("source_type") or source_type
            finally:
                await redis_client.aclose()

        if not source_url or not source_type:
            raise HTTPException(status_code=400, detail="Task source not available")

        # Get video path
        if source_type == "youtube":
            from ...youtube_utils import async_download_youtube_video
            video_path = await async_download_youtube_video(source_url, 3, task_id)
        else:
            from ...video_utils import VideoService
            video_path = VideoService.resolve_local_video_path(source_url)

        if not video_path or not video_path.exists():
            raise HTTPException(status_code=400, detail="Source video not found")

        # Prepare visual identity settings (merge task settings with overrides)
        logo_path = logo_path_override or task.get("logo_path")
        include_hook_titles = task.get("include_hook_titles", True)
        if not isinstance(include_hook_titles, bool):
            include_hook_titles = True
        theme_text = (
            theme_text_override
            or task.get("theme_text")
            or (task.get("hook_title") if include_hook_titles else None)
            or ""
        )
        theme_font_family = task.get("theme_font_family") or "Anton-Regular"
        theme_font_size = theme_font_size_override or task.get("theme_font_size") or 72
        theme_font_color = task.get("theme_font_color") or "#FFFFFF"
        theme_position = theme_position_override or task.get("theme_position") or "center"
        theme_alignment = theme_alignment_override or task.get("theme_alignment") or "center"
        theme_line_spacing = task.get("theme_line_spacing") or 1.2
        theme_margin = task.get("theme_margin") or 0.08
        logo_position_x = task.get("logo_position_x") or 0.5
        logo_position_y = task.get("logo_position_y") or 0.9
        logo_size = task.get("logo_size") or 0.15
        logo_opacity = task.get("logo_opacity") or 1.0
        caption_template = task.get("caption_template") or "default"
        font_family = task.get("font_family")
        font_size = task.get("font_size")
        font_color = task.get("font_color")

        # Create a 1-second clip at the beginning for preview
        output_dir = Path(get_config().temp_dir) / "preview_frames"
        output_dir.mkdir(parents=True, exist_ok=True)
        preview_clip_path = output_dir / f"preview_{task_id}_{uuid.uuid4().hex[:8]}.mp4"

        # Convert logo path to absolute if provided
        logo_abs_path = None
        if logo_path:
            # Check if it's a relative path from logos directory
            if logo_path.startswith("logos/users/"):
                logo_abs_path = Path(get_config().temp_dir).parent / logo_path
            else:
                logo_abs_path = Path(logo_path)
            if not logo_abs_path.exists():
                logo_abs_path = None

        # Create optimized clip (1 second) with visual identity
        success = await create_optimized_clip(
            video_path=video_path,
            start_time=0.0,
            end_time=1.0,
            output_path=preview_clip_path,
            add_subtitles=True,
            font_family=font_family,
            font_size=font_size,
            font_color=font_color,
            caption_template=caption_template,
            output_format="vertical",
            keep_ranges=[(0.0, 1.0)],
            hook_title=theme_text,
            include_hook_titles=include_hook_titles,
            # Visual identity params
            logo_path=logo_abs_path,
            logo_position_x=logo_position_x,
            logo_position_y=logo_position_y,
            logo_size=logo_size,
            logo_opacity=logo_opacity,
            theme_text=theme_text,
            theme_font_family=theme_font_family,
            theme_font_size=theme_font_size,
            theme_font_color=theme_font_color,
            theme_position=theme_position,
            theme_alignment=theme_alignment,
            theme_line_spacing=theme_line_spacing,
            theme_margin=theme_margin,
        )

        if not success or not preview_clip_path.exists():
            raise HTTPException(status_code=500, detail="Failed to generate preview clip")

        # Extract frame at 0.5s as base64 PNG
        frame_path = output_dir / f"frame_{task_id}_{uuid.uuid4().hex[:8]}.png"
        result = subprocess.run([
            "ffmpeg", "-y", "-ss", "0.5", "-i", str(preview_clip_path),
            "-vframes", "1", "-f", "image2pipe", "-vcodec", "png", str(frame_path)
        ], capture_output=True, timeout=30)

        if result.returncode != 0 or not frame_path.exists():
            raise HTTPException(status_code=500, detail="Failed to extract preview frame")

        # Read frame as base64
        with open(frame_path, "rb") as f:
            frame_base64 = base64.b64encode(f.read()).decode("utf-8")

        # Cleanup
        preview_clip_path.unlink(missing_ok=True)
        frame_path.unlink(missing_ok=True)

        return {
            "frame_base64": frame_base64,
            "width": 1080,
            "height": 1920,
            "format": "png",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating preview frame: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error generating preview frame: {str(e)}")
