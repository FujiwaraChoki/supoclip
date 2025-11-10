"""
Mass clip generation API endpoints.
"""
import logging
import json
import asyncio
from typing import Optional
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from arq import create_pool
from arq.connections import RedisSettings

from ...database import get_db, AsyncSessionLocal
from ...config import Config
from ...models import Task, Source, User
from ...workers.progress import ProgressTracker

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/mass", tags=["mass-generation"])
config = Config()


@router.post("/generate")
async def start_mass_generation(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Start mass clip generation task (300-500 clips from long video).

    Request body:
    {
        "uploaded_file_path": "/app/uploads/video.mp4" or "youtube_url",
        "target_clips": 300,
        "enable_vvsa": true,
        "source_type": "upload" or "youtube",
        "font_options": {
            "font_family": "TikTokSans-Regular",
            "font_size": 24,
            "font_color": "#FFFFFF"
        }
    }

    Returns:
    {
        "task_id": "uuid",
        "message": "Mass generation started"
    }
    """
    try:
        data = await request.json()
        headers = request.headers
        user_id = headers.get("user_id")

        # Validate inputs
        if not user_id:
            raise HTTPException(status_code=401, detail="User authentication required")

        video_path = data.get("uploaded_file_path")
        if not video_path:
            raise HTTPException(status_code=400, detail="uploaded_file_path is required")

        target_clips = data.get("target_clips", 100)
        enable_vvsa = data.get("enable_vvsa", True)
        source_type = data.get("source_type", "upload")

        # Font options
        font_options = data.get("font_options", {})
        font_family = font_options.get("font_family", "TikTokSans-Regular")
        font_size = font_options.get("font_size", 24)
        font_color = font_options.get("font_color", "#FFFFFF")

        logger.info(f"🚀 Mass generation request: {video_path}, target={target_clips}, user={user_id}")

        # Verify user exists
        user_result = await db.execute(
            text("SELECT 1 FROM users WHERE id = :user_id"),
            {"user_id": user_id}
        )
        if not user_result.fetchone():
            raise HTTPException(status_code=404, detail="User not found")

        # Create source record
        source = Source()
        source.type = source_type
        source.title = data.get("title", "Mass Generation Video")

        db.add(source)
        await db.flush()

        # Create task record
        task = Task(
            user_id=user_id,
            source_id=source.id,
            status="queued",
            font_family=font_family,
            font_size=font_size,
            font_color=font_color
        )

        db.add(task)
        await db.commit()
        await db.refresh(task)

        logger.info(f"✅ Created task {task.id} for mass generation")

        # Enqueue the task in ARQ worker
        redis = await create_pool(
            RedisSettings(
                host=config.redis_host,
                port=config.redis_port,
                database=0
            )
        )

        job = await redis.enqueue_job(
            'generate_mass_clips_task',
            task_id=task.id,
            video_path=video_path,
            target_clips=target_clips,
            user_id=user_id,
            enable_vvsa=enable_vvsa,
            font_family=font_family,
            font_size=font_size,
            font_color=font_color
        )

        await redis.close()

        logger.info(f"✅ Enqueued job {job.job_id} for task {task.id}")

        return {
            "task_id": task.id,
            "job_id": str(job.job_id),
            "message": "Mass generation started",
            "target_clips": target_clips
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error starting mass generation: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error starting mass generation: {str(e)}")


@router.get("/status/{task_id}")
async def get_mass_generation_status(
    task_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Get current status of mass generation task.

    Returns:
    {
        "task_id": "uuid",
        "status": "queued" | "processing" | "completed" | "error",
        "progress": 0-100,
        "message": "Current status message",
        "clips_generated": 150
    }
    """
    try:
        # Get task from database
        task_result = await db.execute(
            text("SELECT * FROM tasks WHERE id = :task_id"),
            {"task_id": task_id}
        )
        task = task_result.fetchone()

        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        # Get progress from Redis
        from redis.asyncio import Redis
        redis = Redis(host=config.redis_host, port=config.redis_port, decode_responses=True)

        progress_key = f"progress:{task_id}"
        progress_data = await redis.get(progress_key)
        await redis.close()

        if progress_data:
            progress_info = json.loads(progress_data)
        else:
            progress_info = {
                "progress": 0,
                "message": "Task queued",
                "status": task.status
            }

        # Get clips count
        clips_result = await db.execute(
            text("SELECT COUNT(*) as count FROM generated_clips WHERE task_id = :task_id"),
            {"task_id": task_id}
        )
        clips_count = clips_result.fetchone().count

        return {
            "task_id": task_id,
            "status": task.status,
            "progress": progress_info.get("progress", 0),
            "message": progress_info.get("message", "Processing..."),
            "clips_generated": clips_count
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error getting task status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting task status: {str(e)}")


@router.get("/status/{task_id}/stream")
async def stream_mass_generation_progress(task_id: str):
    """
    Server-Sent Events (SSE) endpoint for real-time progress updates.

    Client should connect to this endpoint and listen for progress events:

    ```javascript
    const eventSource = new EventSource(`/mass/status/${taskId}/stream`);
    eventSource.onmessage = (event) => {
        const data = JSON.parse(event.data);
        console.log(data.progress, data.message);
    };
    ```
    """
    async def event_generator():
        """Generate SSE events for progress updates."""
        from redis.asyncio import Redis

        redis = Redis(host=config.redis_host, port=config.redis_port, decode_responses=True)

        try:
            # Subscribe to progress updates
            pubsub = redis.pubsub()
            await pubsub.subscribe(f"progress:{task_id}")

            # Send initial status
            progress_key = f"progress:{task_id}"
            initial_data = await redis.get(progress_key)

            if initial_data:
                yield f"data: {initial_data}\n\n"
            else:
                yield f"data: {json.dumps({'progress': 0, 'message': 'Task queued', 'status': 'queued'})}\n\n"

            # Listen for updates
            timeout_count = 0
            max_timeout = 30  # 30 * 2 seconds = 1 minute without updates

            while timeout_count < max_timeout:
                try:
                    # Wait for message with timeout
                    message = await asyncio.wait_for(pubsub.get_message(ignore_subscribe_messages=True), timeout=2.0)

                    if message and message['type'] == 'message':
                        yield f"data: {message['data']}\n\n"
                        timeout_count = 0  # Reset timeout counter

                        # Check if task is complete
                        data = json.loads(message['data'])
                        if data.get('status') in ['completed', 'error']:
                            logger.info(f"Task {task_id} finished with status: {data.get('status')}")
                            break
                    else:
                        timeout_count += 1

                except asyncio.TimeoutError:
                    # No message received, send keepalive
                    timeout_count += 1
                    current_data = await redis.get(progress_key)
                    if current_data:
                        yield f"data: {current_data}\n\n"

            await pubsub.unsubscribe(f"progress:{task_id}")
            await pubsub.close()

        finally:
            await redis.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # Disable nginx buffering
        }
    )


@router.get("/list")
async def list_mass_generation_tasks(
    request: Request,
    db: AsyncSession = Depends(get_db),
    limit: int = 50,
    offset: int = 0
):
    """
    List all mass generation tasks for the authenticated user.

    Query params:
    - limit: Number of tasks to return (default 50)
    - offset: Offset for pagination (default 0)
    """
    try:
        headers = request.headers
        user_id = headers.get("user_id")

        if not user_id:
            raise HTTPException(status_code=401, detail="User authentication required")

        # Get tasks with clip counts
        tasks_result = await db.execute(
            text("""
                SELECT
                    t.id,
                    t.status,
                    t.created_at,
                    t.updated_at,
                    s.title as source_title,
                    s.type as source_type,
                    COUNT(gc.id) as clips_count
                FROM tasks t
                LEFT JOIN sources s ON t.source_id = s.id
                LEFT JOIN generated_clips gc ON t.id = gc.task_id
                WHERE t.user_id = :user_id
                GROUP BY t.id, t.status, t.created_at, t.updated_at, s.title, s.type
                ORDER BY t.created_at DESC
                LIMIT :limit OFFSET :offset
            """),
            {"user_id": user_id, "limit": limit, "offset": offset}
        )

        tasks = []
        for task in tasks_result.fetchall():
            tasks.append({
                "id": task.id,
                "status": task.status,
                "source_title": task.source_title,
                "source_type": task.source_type,
                "clips_count": task.clips_count,
                "created_at": task.created_at.isoformat(),
                "updated_at": task.updated_at.isoformat()
            })

        return {
            "tasks": tasks,
            "total": len(tasks),
            "limit": limit,
            "offset": offset
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error listing tasks: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error listing tasks: {str(e)}")
