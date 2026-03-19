from .youtube_utils import *
from .video_utils import *
from .ai import *
from .config import Config
from .caption_templates import get_template_info, get_template_names
from datetime import datetime
from contextlib import asynccontextmanager
from pathlib import Path
import logging
import json
import asyncio
from typing import Dict, Any

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("logs/backend.log")],
)

logger = logging.getLogger(__name__)
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy import text

from .models import User, Task, Source, GeneratedClip
from .database import init_db, close_db, get_db, AsyncSessionLocal
from .auth_headers import get_signed_user_id, USER_ID_HEADER
from .api.routes.tasks import router as tasks_router
from .api.routes.feedback import router as feedback_router
from .api.routes.billing import router as billing_router
from .services.video_service import VideoService, UPLOAD_URL_PREFIX

config = Config()


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await init_db()
        yield
    finally:
        await close_db()


app = FastAPI(
    title="SupoClip API",
    description="Python-based backend for SupoClip",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "x-supoclip-user-id",
        "x-supoclip-ts",
        "x-supoclip-signature",
        "user_id",
    ],
)

# Include API routers
app.include_router(tasks_router)
app.include_router(feedback_router)
app.include_router(billing_router)

# Mount static files for serving clips
clips_dir = Path(config.temp_dir) / "clips"
clips_dir.mkdir(parents=True, exist_ok=True)
app.mount("/clips", StaticFiles(directory=str(clips_dir)), name="clips")


def _get_authenticated_user_id(request: Request) -> str:
    if config.monetization_enabled:
        return get_signed_user_id(request, config)

    user_id = request.headers.get("user_id") or request.headers.get(USER_ID_HEADER)
    if not user_id:
        raise HTTPException(status_code=401, detail="User authentication required")
    return user_id


def _resolve_uploaded_video_path(url: str) -> Path:
    return VideoService.resolve_local_video_path(url)


@app.get("/")
def read_root():
    return {
        "message": "This is the SupoClip FastAPI-based API. Visit /docs for the API documentation."
    }


@app.get("/health/db")
async def check_database_health(db: AsyncSession = Depends(get_db)):
    """Check database connectivity"""
    try:
        await db.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        return {"status": "unhealthy", "database": "disconnected", "error": str(e)}


@app.post("/start")
async def start_task(request: Request):
    """Start a new task for authenticated users"""
    if config.monetization_enabled:
        raise HTTPException(status_code=404, detail="Not found")

    logger.info("🚀 Starting new task request")

    data = await request.json()

    raw_source = data.get("source")
    user_id = _get_authenticated_user_id(request)

    # Get font customization options from request
    font_options = data.get("font_options", {})
    font_family = font_options.get("font_family", "TikTokSans-Regular")
    font_size = font_options.get("font_size", 24)
    font_color = font_options.get("font_color", "#FFFFFF")

    # Get caption template and B-roll options
    caption_template = data.get("caption_template", "default")
    include_broll = data.get("include_broll", False)

    logger.info(
        f"📝 Request data - URL: {raw_source.get('url') if raw_source else 'None'}, User ID: {user_id}"
    )

    if not raw_source or not raw_source.get("url"):
        logger.error("❌ Source URL is missing")
        raise HTTPException(status_code=400, detail="Source URL is required")

    if not user_id:
        logger.error("❌ User ID is missing")
        raise HTTPException(status_code=401, detail="User authentication required")

    # Validate user_id is a valid string and user exists
    if not user_id or len(user_id.strip()) == 0:
        logger.error(f"❌ Invalid user ID format: {user_id}")
        raise HTTPException(status_code=400, detail="Invalid user ID format")

    logger.info(f"🔍 Checking if user {user_id} exists in database")
    # Check if user exists in database
    async with AsyncSessionLocal() as db:
        user_exists = await db.execute(
            text("SELECT 1 FROM users WHERE id = :user_id"), {"user_id": user_id}
        )
        if not user_exists.fetchone():
            logger.error(f"❌ User {user_id} not found in database")
            raise HTTPException(status_code=404, detail="User not found")

        logger.info(f"✅ User {user_id} found in database")

        source = Source()
        source.type = source.decide_source_type(raw_source["url"])
        logger.info(f"📺 Source type detected: {source.type}")

        if source.type == "youtube":
            logger.info("🎬 Getting YouTube video title")
            source.title = get_youtube_video_title(raw_source["url"])
            if not source.title:
                logger.warning("⚠️ Could not get YouTube title, using default")
                source.title = "YouTube Video"
            logger.info(f"📝 Video title: {source.title}")
        else:
            source.title = raw_source.get("title", "Uploaded Video")
            logger.info(f"📝 Custom title: {source.title}")

        relevant_segments_json = []
        clips_info = []
        relevant_parts = None

        logger.info("💾 Saving source and creating task in database")
        async with AsyncSessionLocal() as db:
            db.add(source)
            await db.flush()
            logger.info(f"✅ Source saved with ID: {source.id}")

            task = Task(
                user_id=user_id,
                source_id=source.id,
                generated_clips_ids=None,
                font_family=font_family,
                font_size=font_size,
                font_color=font_color,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )

            db.add(task)
            await db.commit()
            logger.info(f"✅ Task created with ID: {task.id}")

            # Determine video path based on source type
            video_path = None
            if source.type == "youtube":
                logger.info("⬇️ Starting YouTube video download")
                video_path = download_youtube_video(raw_source["url"])
                if not video_path:
                    logger.error("❌ Failed to download video")
                    raise HTTPException(
                        status_code=500, detail="Failed to download video"
                    )
                logger.info(f"✅ Video downloaded to: {video_path}")
            else:
                video_path = _resolve_uploaded_video_path(raw_source["url"])
                logger.info(f"📁 Using uploaded video at: {video_path}")

                # Verify the uploaded file exists
                if not Path(video_path).exists():
                    logger.error(f"❌ Uploaded video file not found: {video_path}")
                    raise HTTPException(
                        status_code=404, detail="Uploaded video file not found"
                    )

            # Process video (same for both YouTube and uploaded videos)
            if video_path:
                logger.info(
                    "🎤 Starting transcript generation with AssemblyAI + SRT equalization"
                )
                transcript = get_video_transcript(video_path)
                logger.info(
                    f"✅ AssemblyAI transcript generated with 10-char line equalization (length: {len(transcript)} characters)"
                )

                logger.info(
                    "🤖 Starting AI analysis for relevant segments with virality scoring"
                )
                relevant_parts = await get_most_relevant_parts_by_transcript(
                    transcript, include_broll=include_broll
                )
                logger.info(
                    f"✅ AI analysis complete - found {len(relevant_parts.most_relevant_segments)} segments"
                )

                # Convert to JSON format for response with virality data
                logger.info(
                    "📊 Converting AI results to JSON format with virality scores"
                )
                relevant_segments_json = []
                for segment in relevant_parts.most_relevant_segments:
                    segment_data = {
                        "start_time": segment.start_time,
                        "end_time": segment.end_time,
                        "text": segment.text,
                        "relevance_score": segment.relevance_score,
                        "reasoning": segment.reasoning,
                    }
                    # Add virality data if available
                    if segment.virality:
                        segment_data.update(
                            {
                                "virality_score": segment.virality.total_score,
                                "hook_score": segment.virality.hook_score,
                                "engagement_score": segment.virality.engagement_score,
                                "value_score": segment.virality.value_score,
                                "shareability_score": segment.virality.shareability_score,
                                "hook_type": segment.virality.hook_type,
                                "virality_reasoning": segment.virality.virality_reasoning,
                            }
                        )
                    relevant_segments_json.append(segment_data)
                logger.info(f"✅ Created {len(relevant_segments_json)} segment records")

                # Create standalone clips from relevant segments and custom fonts
                logger.info("🎬 Starting standalone clip generation")
                clips_output_dir = Path(config.temp_dir) / "clips"
                logger.info(f"📁 Output directory: {clips_output_dir}")
                logger.info(
                    f"🎨 Font settings - Family: {font_family}, Size: {font_size}, Color: {font_color}, Template: {caption_template}"
                )
                clips_info = create_clips_with_transitions(
                    video_path,
                    relevant_segments_json,
                    clips_output_dir,
                    font_family,
                    font_size,
                    font_color,
                    caption_template,
                )
                logger.info(
                    f"✅ Generated {len(clips_info)} standalone video clips"
                )

                # Save clips to database
                logger.info("💾 Saving clips to database")
                async with AsyncSessionLocal() as db:
                    clip_ids = []
                    for i, clip_info in enumerate(clips_info):
                        logger.info(
                            f"💾 Saving clip {i + 1}/{len(clips_info)}: {clip_info['filename']}"
                        )
                        clip_record = GeneratedClip(
                            task_id=task.id,
                            filename=clip_info["filename"],
                            file_path=clip_info["path"],
                            start_time=clip_info["start_time"],
                            end_time=clip_info["end_time"],
                            duration=clip_info["duration"],
                            text=clip_info["text"],
                            relevance_score=clip_info["relevance_score"],
                            reasoning=clip_info["reasoning"],
                            clip_order=i + 1,
                            # Virality scores
                            virality_score=clip_info.get("virality_score", 0),
                            hook_score=clip_info.get("hook_score", 0),
                            engagement_score=clip_info.get("engagement_score", 0),
                            value_score=clip_info.get("value_score", 0),
                            shareability_score=clip_info.get("shareability_score", 0),
                            hook_type=clip_info.get("hook_type"),
                        )
                        db.add(clip_record)
                        await db.flush()
                        clip_ids.append(clip_record.id)
                        logger.info(f"✅ Clip {i + 1} saved with ID: {clip_record.id}")

                    # Update task with clip IDs
                    logger.info(f"🔗 Updating task with {len(clip_ids)} clip IDs")
                    task_update = await db.execute(
                        text(
                            "UPDATE tasks SET generated_clips_ids = :clip_ids WHERE id = :task_id"
                        ),
                        {"clip_ids": clip_ids, "task_id": task.id},
                    )
                    await db.commit()
                    logger.info("✅ Task updated with clip IDs")
            else:
                logger.error("❌ No video path available for processing")
                raise HTTPException(
                    status_code=500, detail="No video available for processing"
                )

            logger.info(f"🎉 Task completed successfully! Task ID: {task.id}")
        logger.info(
            f"📊 Final results - Segments: {len(relevant_segments_json)}, Clips: {len(clips_info)}"
        )

        return {
            "message": "Task started successfully",
            "task_id": task.id,
            "relevant_segments": relevant_segments_json,
            "clips": clips_info,
            "summary": relevant_parts.summary if relevant_parts else None,
            "key_topics": relevant_parts.key_topics if relevant_parts else None,
        }


@app.post("/start-with-progress")
async def start_task_with_progress(request: Request):
    """Start a new task and return task ID for SSE tracking"""
    if config.monetization_enabled:
        raise HTTPException(status_code=404, detail="Not found")

    data = await request.json()
    raw_source = data.get("source")
    user_id = _get_authenticated_user_id(request)

    # Get font customization options from request
    font_options = data.get("font_options", {})
    font_family = font_options.get("font_family", "TikTokSans-Regular")
    font_size = font_options.get("font_size", 24)
    font_color = font_options.get("font_color", "#FFFFFF")

    # Get caption template and B-roll options
    caption_template = data.get("caption_template", "default")
    include_broll = data.get("include_broll", False)

    logger.info(
        f"📝 Request data - URL: {raw_source.get('url') if raw_source else 'None'}, User ID: {user_id}"
    )

    if not raw_source or not raw_source.get("url"):
        logger.error("❌ Source URL is missing")
        raise HTTPException(status_code=400, detail="Source URL is required")

    if not user_id:
        logger.error("❌ User ID is missing")
        raise HTTPException(status_code=401, detail="User authentication required")

    # Validate user_id and create initial task
    async with AsyncSessionLocal() as db:
        user_exists = await db.execute(
            text("SELECT 1 FROM users WHERE id = :user_id"), {"user_id": user_id}
        )
        if not user_exists.fetchone():
            logger.error(f"❌ User {user_id} not found in database")
            raise HTTPException(status_code=404, detail="User not found")

        source = Source()
        source.type = source.decide_source_type(raw_source["url"])

        # Get actual title based on source type
        if source.type == "youtube":
            try:
                source.title = get_youtube_video_title(raw_source["url"])
                if not source.title:
                    logger.warning("⚠️ Could not get YouTube title, using default")
                    source.title = "YouTube Video"
                logger.info(f"📝 YouTube video title: {source.title}")
            except Exception as e:
                logger.warning(
                    f"⚠️ Could not get YouTube title, using default: {str(e)}"
                )
                source.title = "YouTube Video"
        else:
            source.title = raw_source.get("title", "Uploaded Video")

        db.add(source)
        await db.flush()

        task = Task(
            user_id=user_id,
            source_id=source.id,
            generated_clips_ids=None,
            status="processing",
            font_family=font_family,
            font_size=font_size,
            font_color=font_color,
            caption_template=caption_template,
            include_broll=include_broll,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

        db.add(task)
        await db.commit()

        # Start processing in background
        asyncio.create_task(
            process_video_task(
                task.id,
                raw_source,
                user_id,
                font_family,
                font_size,
                font_color,
                caption_template,
                include_broll,
            )
        )

        return {"task_id": task.id, "message": "Task started successfully"}


async def update_task_status(task_id: str, status: str):
    """Update task status in database"""
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "UPDATE tasks SET status = :status, updated_at = NOW() WHERE id = :task_id"
            ),
            {"status": status, "task_id": task_id},
        )
        await db.commit()


async def process_video_task(
    task_id: str,
    raw_source: dict,
    user_id: str,
    font_family: str = "TikTokSans-Regular",
    font_size: int = 24,
    font_color: str = "#FFFFFF",
    caption_template: str = "default",
    include_broll: bool = False,
):
    """Background task to process video and update task status"""

    try:
        logger.info(
            f"🚀 Starting background processing for task {task_id} with template '{caption_template}'"
        )
        await update_task_status(task_id, "processing")

        # Get source from database
        async with AsyncSessionLocal() as db:
            source_result = await db.execute(
                text(
                    "SELECT * FROM sources WHERE id IN (SELECT source_id FROM tasks WHERE id = :task_id)"
                ),
                {"task_id": task_id},
            )
            source_data = source_result.fetchone()
            if not source_data:
                raise Exception("Source not found")

        logger.info(f"📊 Task {task_id}: Analyzing video source...")

        # Determine video path based on source type
        video_path = None
        if source_data.type == "youtube":
            logger.info(f"📊 Task {task_id}: Downloading YouTube video...")
            video_path = download_youtube_video(raw_source["url"])
            if not video_path:
                raise Exception("Failed to download video")
            logger.info(f"✅ Video downloaded to: {video_path}")
        else:
            video_path = _resolve_uploaded_video_path(raw_source["url"])
            if not Path(video_path).exists():
                raise Exception("Uploaded video file not found")

        # Process video
        if video_path:
            logger.info(f"📊 Task {task_id}: Generating transcript with AssemblyAI...")
            transcript = get_video_transcript(video_path)
            logger.info(
                f"✅ Transcript generated (length: {len(transcript)} characters)"
            )

            logger.info(
                f"📊 Task {task_id}: AI analyzing content for best clips with virality scoring..."
            )
            relevant_parts = await get_most_relevant_parts_by_transcript(
                transcript, include_broll=include_broll
            )
            logger.info(
                f"✅ AI analysis complete - found {len(relevant_parts.most_relevant_segments)} segments"
            )

            # Convert to JSON format with virality data
            relevant_segments_json = []
            for segment in relevant_parts.most_relevant_segments:
                segment_data = {
                    "start_time": segment.start_time,
                    "end_time": segment.end_time,
                    "text": segment.text,
                    "relevance_score": segment.relevance_score,
                    "reasoning": segment.reasoning,
                }
                # Add virality data if available
                if segment.virality:
                    segment_data.update(
                        {
                            "virality_score": segment.virality.total_score,
                            "hook_score": segment.virality.hook_score,
                            "engagement_score": segment.virality.engagement_score,
                            "value_score": segment.virality.value_score,
                            "shareability_score": segment.virality.shareability_score,
                            "hook_type": segment.virality.hook_type,
                            "virality_reasoning": segment.virality.virality_reasoning,
                        }
                    )
                relevant_segments_json.append(segment_data)

            logger.info(
                f"📊 Task {task_id}: Creating {len(relevant_segments_json)} standalone video clips..."
            )
            clips_output_dir = Path(config.temp_dir) / "clips"
            logger.info(
                f"🎨 Task {task_id}: Font settings - Family: {font_family}, Size: {font_size}, Color: {font_color}, Template: {caption_template}"
            )
            clips_info = create_clips_with_transitions(
                video_path,
                relevant_segments_json,
                clips_output_dir,
                font_family,
                font_size,
                font_color,
                caption_template,
            )
            logger.info(f"✅ Generated {len(clips_info)} standalone video clips")

            logger.info(f"📊 Task {task_id}: Saving clips to database...")
            async with AsyncSessionLocal() as db:
                clip_ids = []
                for i, clip_info in enumerate(clips_info):
                    clip_record = GeneratedClip(
                        task_id=task_id,
                        filename=clip_info["filename"],
                        file_path=clip_info["path"],
                        start_time=clip_info["start_time"],
                        end_time=clip_info["end_time"],
                        duration=clip_info["duration"],
                        text=clip_info["text"],
                        relevance_score=clip_info["relevance_score"],
                        reasoning=clip_info["reasoning"],
                        clip_order=i + 1,
                        # Virality scores
                        virality_score=clip_info.get("virality_score", 0),
                        hook_score=clip_info.get("hook_score", 0),
                        engagement_score=clip_info.get("engagement_score", 0),
                        value_score=clip_info.get("value_score", 0),
                        shareability_score=clip_info.get("shareability_score", 0),
                        hook_type=clip_info.get("hook_type"),
                    )
                    db.add(clip_record)
                    await db.flush()
                    clip_ids.append(clip_record.id)

                # Update task with clip IDs
                await db.execute(
                    text(
                        "UPDATE tasks SET generated_clips_ids = :clip_ids WHERE id = :task_id"
                    ),
                    {"clip_ids": clip_ids, "task_id": task_id},
                )
                await db.commit()

        # Mark as completed
        await update_task_status(task_id, "completed")
        logger.info(f"🎉 Task {task_id} completed successfully!")

    except Exception as e:
        logger.error(f"❌ Error processing task {task_id}: {str(e)}")
        await update_task_status(task_id, "error")
        logger.error(f"📊 Task {task_id} marked as error: {str(e)}")


@app.get("/tasks/{task_id}/clips")
async def get_task_clips(task_id: str, db: AsyncSession = Depends(get_db)):
    """Get all clips for a specific task"""
    try:
        # Get task and verify it exists
        task_result = await db.execute(
            text("SELECT * FROM tasks WHERE id = :task_id"), {"task_id": task_id}
        )
        task = task_result.fetchone()
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        # Get clips for this task (including virality scores)
        clips_result = await db.execute(
            text("""
        SELECT id, filename, file_path, start_time, end_time, duration,
               text, relevance_score, reasoning, clip_order, created_at,
               virality_score, hook_score, engagement_score, value_score,
               shareability_score, hook_type
        FROM generated_clips
        WHERE task_id = :task_id
        ORDER BY clip_order ASC
      """),
            {"task_id": task_id},
        )
        clips = clips_result.fetchall()

        # Convert to list of dictionaries and add serving URLs
        clips_data = []
        for clip in clips:
            clip_data = {
                "id": clip.id,
                "filename": clip.filename,
                "file_path": clip.file_path,
                "start_time": clip.start_time,
                "end_time": clip.end_time,
                "duration": clip.duration,
                "text": clip.text,
                "relevance_score": clip.relevance_score,
                "reasoning": clip.reasoning,
                "clip_order": clip.clip_order,
                "created_at": clip.created_at.isoformat(),
                "video_url": f"/clips/{clip.filename}",  # URL for frontend to access the clip
                # Virality scores
                "virality_score": clip.virality_score or 0,
                "hook_score": clip.hook_score or 0,
                "engagement_score": clip.engagement_score or 0,
                "value_score": clip.value_score or 0,
                "shareability_score": clip.shareability_score or 0,
                "hook_type": clip.hook_type,
            }
            clips_data.append(clip_data)

        return {"task_id": task_id, "clips": clips_data, "total_clips": len(clips_data)}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving clips: {str(e)}")


@app.get("/tasks/{task_id}")
async def get_task_details(task_id: str, db: AsyncSession = Depends(get_db)):
    """Get task details including clips"""
    try:
        # Get task details
        task_result = await db.execute(
            text("""
        SELECT t.*, s.title as source_title, s.type as source_type
        FROM tasks t
        LEFT JOIN sources s ON t.source_id = s.id
        WHERE t.id = :task_id
      """),
            {"task_id": task_id},
        )
        task = task_result.fetchone()
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        # Get clips count
        clips_count_result = await db.execute(
            text(
                "SELECT COUNT(*) as count FROM generated_clips WHERE task_id = :task_id"
            ),
            {"task_id": task_id},
        )
        clips_count = clips_count_result.fetchone().count

        task_data = {
            "id": task.id,
            "user_id": task.user_id,
            "source_id": task.source_id,
            "source_title": task.source_title,
            "source_type": task.source_type,
            "status": task.status,
            "generated_clips_ids": task.generated_clips_ids,
            "clips_count": clips_count,
            "font_family": task.font_family if hasattr(task, "font_family") else None,
            "font_size": task.font_size if hasattr(task, "font_size") else None,
            "font_color": task.font_color if hasattr(task, "font_color") else None,
            "created_at": task.created_at.isoformat(),
            "updated_at": task.updated_at.isoformat(),
        }

        return task_data

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving task: {str(e)}")


@app.get("/fonts")
async def get_available_fonts():
    """Get list of available fonts"""
    try:
        fonts_dir = Path(__file__).parent.parent / "fonts"
        if not fonts_dir.exists():
            return {"fonts": [], "message": "Fonts directory not found"}

        font_files = []
        for font_file in fonts_dir.glob("*.ttf"):
            font_name = font_file.stem  # Get filename without extension
            font_files.append(
                {
                    "name": font_name,
                    "display_name": font_name.replace("-", " ")
                    .replace("_", " ")
                    .title(),
                    "file_path": str(font_file),
                }
            )

        logger.info(f"Found {len(font_files)} available fonts")
        return {"fonts": font_files}

    except Exception as e:
        logger.error(f"Error retrieving fonts: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error retrieving fonts: {str(e)}")


@app.get("/fonts/{font_name}")
async def get_font_file(font_name: str):
    """Serve a specific font file"""
    try:
        fonts_dir = Path(__file__).parent.parent / "fonts"
        font_path = fonts_dir / f"{font_name}.ttf"

        if not font_path.exists():
            raise HTTPException(status_code=404, detail="Font not found")

        return FileResponse(
            path=str(font_path),
            media_type="font/ttf",
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


@app.get("/transitions")
async def get_available_transitions():
    """Get list of available transition effects"""
    try:
        from .video_utils import get_available_transitions

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


@app.get("/caption-templates")
async def get_caption_templates():
    """Get list of available caption templates with styling info"""
    try:
        templates = get_template_info()
        logger.info(f"Returning {len(templates)} caption templates")
        return {"templates": templates}
    except Exception as e:
        logger.error(f"Error retrieving caption templates: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error retrieving caption templates: {str(e)}"
        )


@app.get("/broll/search")
async def search_broll(query: str, count: int = 5, orientation: str = "portrait"):
    """Search for B-roll videos from Pexels"""
    try:
        from .broll import search_broll_videos, get_video_download_url

        if not config.pexels_api_key:
            raise HTTPException(
                status_code=503,
                detail="B-roll service not configured (missing Pexels API key)",
            )

        videos = await search_broll_videos(
            query, orientation=orientation, per_page=count
        )

        # Format results for frontend
        results = []
        for video in videos:
            download_url = get_video_download_url(
                video, quality="hd", orientation=orientation
            )
            results.append(
                {
                    "id": video.get("id"),
                    "duration": video.get("duration"),
                    "width": video.get("width"),
                    "height": video.get("height"),
                    "thumbnail": video.get("image"),
                    "download_url": download_url,
                    "user": video.get("user", {}).get("name", "Unknown"),
                    "user_url": video.get("user", {}).get("url", ""),
                }
            )

        logger.info(f"B-roll search for '{query}': found {len(results)} results")
        return {"query": query, "videos": results, "total": len(results)}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error searching B-roll: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error searching B-roll: {str(e)}")


@app.get("/broll/status")
async def broll_status():
    """Check if B-roll service is configured"""
    return {
        "configured": bool(config.pexels_api_key),
        "provider": "pexels" if config.pexels_api_key else None,
    }


# endpoint to upload a video
@app.post("/upload")
async def upload_video(request: Request):
    """Upload a video to the server"""
    try:
        import aiofiles
        import uuid

        _get_authenticated_user_id(request)

        # Get the form data
        form_data = await request.form()
        video_file = form_data.get("video")

        if not video_file or not hasattr(video_file, "filename"):
            raise HTTPException(status_code=400, detail="No video file provided")

        # Create uploads directory
        uploads_dir = Path(config.temp_dir) / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)

        # Generate unique filename to avoid conflicts
        file_extension = Path(video_file.filename).suffix
        unique_filename = f"{uuid.uuid4()}{file_extension}"
        video_path = uploads_dir / unique_filename

        # Save the uploaded file
        async with aiofiles.open(video_path, "wb") as f:
            content = await video_file.read()
            await f.write(content)

        logger.info(f"✅ Video uploaded successfully to: {video_path}")

        return {
            "message": "Video uploaded successfully",
            "video_path": f"{UPLOAD_URL_PREFIX}{unique_filename}",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error uploading video: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error uploading video: {str(e)}")
