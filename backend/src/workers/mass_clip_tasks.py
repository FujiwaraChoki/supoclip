"""
Mass clip generation worker tasks - handles large-scale clip generation.
"""
import logging
from typing import Dict, Any, List
from pathlib import Path
import json

logger = logging.getLogger(__name__)


async def generate_mass_clips_task(
    ctx: Dict[str, Any],
    task_id: str,
    video_path: str,
    target_clips: int,
    user_id: str,
    enable_vvsa: bool = True,
    font_family: str = "TikTokSans-Regular",
    font_size: int = 24,
    font_color: str = "#FFFFFF"
) -> Dict[str, Any]:
    """
    Background worker task for mass clip generation (300-500 clips).

    Args:
        ctx: arq context (provides Redis connection)
        task_id: Task ID to update
        video_path: Path to video file
        target_clips: Target number of clips (e.g., 300-500)
        user_id: User ID who created the task
        enable_vvsa: Enable Viral Video Start Algorithm scoring
        font_family: Font family for subtitles
        font_size: Font size for subtitles
        font_color: Font color for subtitles

    Returns:
        Dict with processing results
    """
    from ..database import AsyncSessionLocal
    from ..workers.progress import ProgressTracker
    from ..video_utils import VideoProcessor
    from ..ai import get_most_relevant_parts_by_transcript
    from ..models import GeneratedClip, Task
    from sqlalchemy import text, select
    from ..config import Config

    config = Config()
    logger.info(f"🚀 Starting mass clip generation for task {task_id}")
    logger.info(f"🎯 Target clips: {target_clips}, Video: {video_path}")

    # Create progress tracker
    progress = ProgressTracker(ctx['redis'], task_id)

    try:
        # Update task status to processing
        async with AsyncSessionLocal() as db:
            await db.execute(
                text("UPDATE tasks SET status = :status WHERE id = :task_id"),
                {"status": "processing", "task_id": task_id}
            )
            await db.commit()

        await progress.update(5, "Starting transcription...", "processing")

        # STEP 1: Transcribe video (this is the longest step - 15-20 minutes for 3-hour video)
        logger.info(f"📝 Task {task_id}: Starting transcription")
        await progress.update(10, "Transcribing video with MLX Whisper... This may take 15-20 minutes for long videos", "processing")

        # Use new transcription utility with MLX primary, AssemblyAI fallback
        from ..utils.transcription_utils import get_transcript_with_fallback, format_transcript_for_ai, cache_transcript_data

        # Get raw transcript data
        transcript_data = await get_transcript_with_fallback(video_path, prefer_mlx=True)

        # Cache for future use
        cache_transcript_data(video_path, transcript_data)

        # Format for AI analysis
        transcript = format_transcript_for_ai(transcript_data)

        logger.info(f"✅ Task {task_id}: Transcription complete ({len(transcript)} chars)")
        logger.info(f"Source: {transcript_data.get('source', 'mlx')}")
        await progress.update(40, "Transcription complete! Analyzing content with AI...", "processing")

        # STEP 2: AI Analysis for clip selection
        # For mass generation, we need to use a different strategy
        # Break transcript into overlapping segments and analyze each
        logger.info(f"🤖 Task {task_id}: Starting AI analysis for {target_clips} clips")
        await progress.update(45, f"AI analyzing content to find {target_clips} viral moments...", "processing")

        # Split transcript into chunks for analysis
        all_segments = []
        transcript_lines = transcript.split('\n')
        chunk_size = max(50, len(transcript_lines) // 10)  # Analyze in 10 chunks minimum

        for i in range(0, len(transcript_lines), chunk_size // 2):  # 50% overlap
            chunk = '\n'.join(transcript_lines[i:i + chunk_size])
            if not chunk.strip():
                continue

            logger.info(f"Analyzing chunk {i // (chunk_size // 2) + 1}")
            analysis = await get_most_relevant_parts_by_transcript(chunk)
            all_segments.extend(analysis.most_relevant_segments)

            # Update progress
            progress_percent = 45 + int((i / len(transcript_lines)) * 20)
            await progress.update(
                progress_percent,
                f"Analyzing content... Found {len(all_segments)} potential clips so far",
                "processing"
            )

        logger.info(f"✅ Task {task_id}: AI analysis complete - found {len(all_segments)} segments")

        # Sort by relevance score and take top N
        all_segments.sort(key=lambda x: x.relevance_score, reverse=True)
        selected_segments = all_segments[:target_clips]

        logger.info(f"📊 Task {task_id}: Selected top {len(selected_segments)} segments")
        await progress.update(65, f"Selected {len(selected_segments)} segments. Generating video clips...", "processing")

        # STEP 3: Generate video clips
        clips_output_dir = Path(config.temp_dir) / "clips"
        clips_output_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"🎬 Task {task_id}: Starting clip generation")

        # Convert segments to format expected by clip generation
        segments_json = [
            {
                "start_time": seg.start_time,
                "end_time": seg.end_time,
                "text": seg.text,
                "relevance_score": seg.relevance_score,
                "reasoning": seg.reasoning
            }
            for seg in selected_segments
        ]

        # Generate clips with progress updates
        from ..video_utils import create_clips_with_transitions

        clips_info = []
        batch_size = 10  # Process 10 clips at a time for progress updates

        for batch_idx in range(0, len(segments_json), batch_size):
            batch = segments_json[batch_idx:batch_idx + batch_size]

            logger.info(f"🎬 Generating clips {batch_idx + 1}-{min(batch_idx + batch_size, len(segments_json))}")

            # Generate this batch of clips
            batch_clips = await loop.run_in_executor(
                None,
                partial(
                    create_clips_with_transitions,
                    video_path,
                    batch,
                    clips_output_dir,
                    font_family,
                    font_size,
                    font_color
                )
            )

            clips_info.extend(batch_clips)

            # Update progress
            progress_percent = 65 + int(((batch_idx + batch_size) / len(segments_json)) * 25)
            await progress.update(
                progress_percent,
                f"Generated {len(clips_info)} / {len(segments_json)} clips...",
                "processing"
            )

        logger.info(f"✅ Task {task_id}: Generated {len(clips_info)} clips")
        await progress.update(90, f"Clips generated! Saving {len(clips_info)} clips to database...", "processing")

        # STEP 4: Save clips to database using BULK INSERT
        logger.info(f"💾 Task {task_id}: Saving clips to database with bulk insert")

        async with AsyncSessionLocal() as db:
            # Prepare bulk insert data
            clip_dicts = []
            for i, clip_info in enumerate(clips_info):
                clip_dicts.append({
                    "task_id": task_id,
                    "filename": clip_info["filename"],
                    "file_path": clip_info["path"],
                    "start_time": clip_info["start_time"],
                    "end_time": clip_info["end_time"],
                    "duration": clip_info["duration"],
                    "text": clip_info.get("text", ""),
                    "relevance_score": clip_info.get("relevance_score", 0.0),
                    "reasoning": clip_info.get("reasoning", ""),
                    "clip_order": i + 1
                })

            # Bulk insert - single transaction for all clips
            from sqlalchemy import insert
            if clip_dicts:
                await db.execute(insert(GeneratedClip), clip_dicts)

                # Update task with clip count
                await db.execute(
                    text("UPDATE tasks SET status = :status, generated_clips_ids = :clip_ids WHERE id = :task_id"),
                    {
                        "status": "completed",
                        "clip_ids": [str(i) for i in range(len(clip_dicts))],  # Placeholder IDs
                        "task_id": task_id
                    }
                )
                await db.commit()

        logger.info(f"✅ Task {task_id}: Saved {len(clips_info)} clips to database")
        await progress.update(100, f"Complete! Generated {len(clips_info)} clips successfully", "completed")

        logger.info(f"🎉 Task {task_id} completed successfully!")

        return {
            "success": True,
            "task_id": task_id,
            "clips_generated": len(clips_info),
            "target_clips": target_clips
        }

    except Exception as e:
        logger.error(f"❌ Error in mass clip generation task {task_id}: {e}", exc_info=True)
        await progress.error(f"Error: {str(e)}")

        # Update task status to error
        async with AsyncSessionLocal() as db:
            await db.execute(
                text("UPDATE tasks SET status = :status WHERE id = :task_id"),
                {"status": "error", "task_id": task_id}
            )
            await db.commit()

        raise


# Worker configuration - add mass generation to worker functions
class MassClipWorkerSettings:
    """Configuration for mass clip generation worker."""

    from ..config import Config
    from arq.connections import RedisSettings

    config = Config()

    # Functions to run
    functions = [generate_mass_clips_task]
    queue_name = "supoclip_mass_tasks"

    # Redis settings
    redis_settings = RedisSettings(
        host=config.redis_host,
        port=config.redis_port,
        database=0
    )

    # Retry settings
    max_tries = 2  # Only retry once for mass generation
    job_timeout = 7200  # 2 hour timeout for mass generation

    # Worker pool settings
    max_jobs = 2  # Only process 2 mass generation jobs simultaneously
