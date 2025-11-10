"""
Full matrix generation worker - combines council + matrix processing.

Complete end-to-end pipeline:
1. AI council deliberation (base clips)
2. Matrix processing (9 variations per clip)
3. Export to Premiere Pro XML (optional)
"""
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


async def generate_full_matrix_task(
    ctx: Dict[str, Any],
    task_id: str,
    video_path: str,
    user_id: str,
    user_notes: str = "",
    matrix_options: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Full matrix generation worker task.

    Pipeline:
    1. Transcribe video (MLX + AssemblyAI fallback)
    2. AI council deliberation (5 models → base clips)
    3. Matrix processing (temporal × canvas × effects)
    4. Export to Premiere XML

    Args:
        ctx: arq context (provides Redis connection)
        task_id: Task ID for tracking
        video_path: Source video file path
        user_id: User ID
        user_notes: User instructions for AI council
        matrix_options: Matrix processing options

    Returns:
        Dict with processing results
    """
    from ..database import AsyncSessionLocal
    from ..workers.progress import ProgressTracker
    from ..config import Config
    from ..utils.transcription_utils import get_transcript_with_fallback, format_transcript_for_ai, cache_transcript_data
    from ..utils.simple_video import get_video_duration
    from ..council.deliberation import run_council_analysis
    from ..workers.matrix_processing import process_clip_matrix
    from ..export import generate_premiere_xml
    from sqlalchemy import text
    import os

    config = Config()
    logger.info(f"🚀 Starting full matrix generation for task {task_id}")
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

        # ========== PHASE 1: TRANSCRIPTION (0-30%) ==========
        await progress.update(5, "Getting video duration...", "processing")

        video_duration = await get_video_duration(video_path)
        if video_duration == 0:
            raise Exception("Could not determine video duration")

        logger.info(f"📏 Video duration: {video_duration/60:.1f} minutes")

        await progress.update(10, "Transcribing video (MLX Whisper)... May take 15-20 minutes", "processing")

        transcript_data = await get_transcript_with_fallback(video_path, prefer_mlx=True)
        cache_transcript_data(video_path, transcript_data)

        transcript = format_transcript_for_ai(transcript_data)
        logger.info(f"✅ Transcription complete ({len(transcript)} chars)")

        # ========== PHASE 2: AI COUNCIL DELIBERATION (30-50%) ==========
        await progress.update(30, "AI Council Phase 1: Independent analysis by 5 models...", "processing")

        deliberation = await run_council_analysis(
            transcript=transcript,
            video_duration=video_duration,
            user_notes=user_notes
        )

        logger.info(f"✅ Council deliberation complete")
        logger.info(f"Final candidates: {len(deliberation.final_candidates)}")
        logger.info(f"Consensus level: {deliberation.consensus_level:.2%}")

        await progress.update(
            50,
            f"Council selected {len(deliberation.final_candidates)} base clips. Starting matrix processing...",
            "processing"
        )

        # Convert candidates to clip dicts for matrix processing
        base_clips = [
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

        # ========== PHASE 3: MATRIX PROCESSING (50-95%) ==========
        await progress.update(50, "Starting matrix processing (9 variations per clip)...", "processing")

        # Parse matrix options
        matrix_options = matrix_options or {}

        matrix_result = await process_clip_matrix(
            ctx=ctx,
            task_id=task_id,
            base_clips=base_clips,
            video_path=video_path,
            user_id=user_id,
            transcript_data=transcript_data,
            options=matrix_options
        )

        logger.info(f"✅ Matrix processing complete: {matrix_result['total_variations']} variations")

        # ========== PHASE 4: EXPORT TO PREMIERE XML (95-100%) ==========
        await progress.update(95, "Generating Premiere Pro XML export...", "processing")

        # Generate XML for all variations
        xml_path = os.path.join(matrix_result['output_dir'], "premiere_export.xml")

        # Load manifest to get all variations
        import json
        with open(matrix_result['manifest_path'], 'r') as f:
            manifest = json.load(f)

        # Prepare clips for XML export
        xml_clips = [
            {
                'file_path': var['file_path'],
                'name': var['filename'],
                'start': var['start_time'],
                'duration': var['duration'],
                'metadata': {
                    'base_title': var['base_title'],
                    'temporal_type': var['temporal_type'],
                    'canvas_style': var['canvas_style'],
                    'engagement_score': var['engagement_score']
                }
            }
            for var in manifest['variations']
        ]

        generate_premiere_xml(
            clips=xml_clips,
            output_path=xml_path,
            project_name=f"SupoClip_{task_id}"
        )

        logger.info(f"✅ Premiere XML exported: {xml_path}")

        # Update task status to completed
        async with AsyncSessionLocal() as db:
            await db.execute(
                text("UPDATE tasks SET status = :status WHERE id = :task_id"),
                {"status": "completed", "task_id": task_id}
            )
            await db.commit()

        await progress.update(
            100,
            f"Complete! Generated {matrix_result['total_variations']} variations from {len(base_clips)} base clips",
            "completed"
        )

        logger.info(f"🎉 Full matrix generation complete for task {task_id}")

        return {
            "success": True,
            "task_id": task_id,
            "base_clips": len(base_clips),
            "total_variations": matrix_result['total_variations'],
            "consensus_level": deliberation.consensus_level,
            "output_dir": matrix_result['output_dir'],
            "premiere_xml": xml_path,
            "manifest": matrix_result['manifest_path']
        }

    except Exception as e:
        logger.error(f"❌ Error in full matrix generation task {task_id}: {e}", exc_info=True)
        await progress.error(f"Error: {str(e)}")

        # Update task status to error
        async with AsyncSessionLocal() as db:
            await db.execute(
                text("UPDATE tasks SET status = :status WHERE id = :task_id"),
                {"status": "error", "task_id": task_id}
            )
            await db.commit()

        raise
