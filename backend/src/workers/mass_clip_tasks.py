"""
Mass clip generation worker tasks - 5-model council system with simple cuts.
"""
import logging
from typing import Dict, Any, List
from pathlib import Path
import json
import os

logger = logging.getLogger(__name__)


async def generate_mass_clips_task(
    ctx: Dict[str, Any],
    task_id: str,
    video_path: str,
    user_id: str,
    user_notes: str = ""
) -> Dict[str, Any]:
    """
    Background worker task for mass clip generation with 5-model council.

    NEW ARCHITECTURE:
    - 5-model council deliberation (OpenRouter)
    - Adaptive clip targeting (50/250/500 based on duration)
    - Simple cuts only (no crop, no captions, no effects)
    - User instruction notes guide the AI

    Args:
        ctx: arq context (provides Redis connection)
        task_id: Task ID to update
        video_path: Path to video file
        user_id: User ID who created the task
        user_notes: User instructions for what to look for in clips

    Returns:
        Dict with processing results
    """
    from ..database import AsyncSessionLocal
    from ..workers.progress import ProgressTracker
    from ..models import GeneratedClip, Task
    from sqlalchemy import text, insert
    from ..config import Config
    from ..utils.transcription_utils import get_transcript_with_fallback, format_transcript_for_ai, cache_transcript_data
    from ..utils.simple_video import get_video_duration, batch_cut_clips
    from ..council.deliberation import run_council_analysis, calculate_target_clips

    config = Config()
    logger.info(f"🚀 Starting mass clip generation for task {task_id}")
    logger.info(f"📹 Video: {video_path}")
    logger.info(f"📝 User notes: {user_notes or 'None'}")

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

        await progress.update(5, "Getting video duration...", "processing")

        # STEP 1: Get video duration for adaptive targeting
        logger.info(f"📏 Step 1: Analyzing video duration")
        video_duration = await get_video_duration(video_path)

        if video_duration == 0:
            raise Exception("Could not determine video duration")

        target_clips = calculate_target_clips(video_duration)
        logger.info(f"🎯 Video: {video_duration/60:.1f} min → Target: {target_clips} clips")

        await progress.update(
            10,
            f"Video duration: {video_duration/60:.1f} min. Targeting {target_clips} clips. Starting transcription...",
            "processing"
        )

        # STEP 2: Transcribe video (MLX primary, AssemblyAI fallback)
        logger.info(f"📝 Step 2: Transcribing video (15-20 min for long videos)")
        await progress.update(15, "Transcribing with MLX Whisper... This may take 15-20 minutes", "processing")

        transcript_data = await get_transcript_with_fallback(video_path, prefer_mlx=True)
        cache_transcript_data(video_path, transcript_data)

        # Format for AI
        transcript = format_transcript_for_ai(transcript_data)

        logger.info(f"✅ Transcription complete ({len(transcript)} chars)")
        logger.info(f"Source: {transcript_data.get('source', 'mlx')}")

        await progress.update(40, "Transcription complete! Starting AI council deliberation...", "processing")

        # STEP 3: Run 5-model council deliberation
        logger.info(f"🤖 Step 3: Running 5-model council deliberation")
        await progress.update(45, "AI Council Phase 1: Independent analysis by 5 models...", "processing")

        deliberation = await run_council_analysis(
            transcript=transcript,
            video_duration=video_duration,
            user_notes=user_notes
        )

        logger.info(f"✅ Council deliberation complete")
        logger.info(f"Final candidates: {len(deliberation.final_candidates)}")
        logger.info(f"Consensus level: {deliberation.consensus_level:.2f}")

        await progress.update(
            65,
            f"Council selected {len(deliberation.final_candidates)} clips (consensus: {deliberation.consensus_level:.0%}). Generating videos...",
            "processing"
        )

        # STEP 4: Generate clips (simple cuts, no effects)
        logger.info(f"🎬 Step 4: Generating {len(deliberation.final_candidates)} clips")

        output_dir = os.path.join(config.temp_dir, "clips", task_id)
        os.makedirs(output_dir, exist_ok=True)

        # Prepare clip data
        clips_to_cut = [
            {
                "start_time": candidate.start_time,
                "end_time": candidate.end_time,
                "title": candidate.title,
                "reasoning": candidate.reasoning,
                "engagement_score": candidate.engagement_score,
                "category": candidate.category
            }
            for candidate in deliberation.final_candidates
        ]

        # Progress callback
        async def clip_progress(current, total):
            percent = 65 + int((current / total) * 25)
            await progress.update(
                percent,
                f"Generating clips: {current}/{total}...",
                "processing"
            )

        # Batch cut clips
        generated_paths = await batch_cut_clips(
            input_path=video_path,
            output_dir=output_dir,
            clips=clips_to_cut,
            progress_callback=clip_progress
        )

        logger.info(f"✅ Generated {len(generated_paths)} clips")

        await progress.update(90, "Clips generated! Saving to database...", "processing")

        # STEP 5: Save clips to database (bulk insert)
        logger.info(f"💾 Step 5: Saving {len(generated_paths)} clips to database")

        async with AsyncSessionLocal() as db:
            # Prepare bulk insert data
            clip_dicts = []
            for i, (clip_data, clip_path) in enumerate(zip(clips_to_cut, generated_paths)):
                # Parse timestamps to get duration
                start_parts = clip_data["start_time"].split(':')
                end_parts = clip_data["end_time"].split(':')

                start_seconds = int(start_parts[0]) * 60 + int(start_parts[1])
                end_seconds = int(end_parts[0]) * 60 + int(end_parts[1])
                duration = end_seconds - start_seconds

                clip_dicts.append({
                    "task_id": task_id,
                    "filename": os.path.basename(clip_path),
                    "file_path": clip_path,
                    "start_time": clip_data["start_time"],
                    "end_time": clip_data["end_time"],
                    "duration": duration,
                    "text": clip_data.get("title", ""),
                    "relevance_score": clip_data.get("engagement_score", 0.0),
                    "reasoning": clip_data.get("reasoning", ""),
                    "clip_order": i + 1
                })

            # Bulk insert - single transaction
            if clip_dicts:
                await db.execute(insert(GeneratedClip), clip_dicts)

                # Update task
                await db.execute(
                    text("""
                        UPDATE tasks
                        SET status = :status,
                            generated_clips_ids = :clip_ids
                        WHERE id = :task_id
                    """),
                    {
                        "status": "completed",
                        "clip_ids": [str(i) for i in range(len(clip_dicts))],
                        "task_id": task_id
                    }
                )
                await db.commit()

        logger.info(f"✅ Saved {len(clip_dicts)} clips to database")

        await progress.update(
            100,
            f"Complete! Generated {len(generated_paths)} clips with {deliberation.consensus_level:.0%} council consensus",
            "completed"
        )

        logger.info(f"🎉 Task {task_id} completed successfully!")

        return {
            "success": True,
            "task_id": task_id,
            "clips_generated": len(generated_paths),
            "target_clips": target_clips,
            "consensus_level": deliberation.consensus_level,
            "video_duration": video_duration
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
