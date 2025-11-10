"""
Matrix processing pipeline for clip variations.

Generates 9 variations per clip (3 canvas × 3 temporal):
- Canvas styles: original, flipped, blurry_bg
- Temporal variations: base, +4s, +35s
- Plus: watermarks, title cards, music, captions

This creates the full matrix of clips ready for posting.
"""
import logging
import os
from typing import Dict, Any, List, Optional
from pathlib import Path
import json

logger = logging.getLogger(__name__)


async def process_clip_matrix(
    ctx: Dict[str, Any],
    task_id: str,
    base_clips: List[Dict[str, Any]],
    video_path: str,
    user_id: str,
    transcript_data: Dict[str, Any],
    options: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Process clips through matrix pipeline.

    For each base clip:
    1. Generate 3 temporal variations (base, +4s, +35s)
    2. Render each temporal variation on 3 canvas styles (original, flipped, blurry_bg)
    3. Apply watermark, title card, music, captions to each variation
    4. Organize into folders by date/channel

    This creates 9 variations per base clip (3 temporal × 3 canvas).

    Args:
        ctx: arq context (provides Redis connection)
        task_id: Task ID for tracking
        base_clips: List of base clip dicts from council deliberation
        video_path: Source video file path
        user_id: User ID
        transcript_data: Full transcript with word-level timing
        options: Processing options (watermark settings, title style, music prefs)

    Returns:
        Dict with processing results
    """
    from ..database import AsyncSessionLocal
    from ..workers.progress import ProgressTracker
    from ..config import Config
    from ..variations import generate_temporal_variations
    from ..reframe import FaceTracker, CanvasRenderer
    from ..watermark import WatermarkOverlay
    from ..titlecards import TitleCardGenerator
    from ..music import MusicSwapper, add_music_to_video
    from ..captions import CaptionGenerator
    from ..utils.simple_video import cut_clip_ffmpeg, get_video_duration

    config = Config()
    logger.info(f"🎬 Starting matrix processing for task {task_id}")
    logger.info(f"Base clips: {len(base_clips)}")

    # Create progress tracker
    progress = ProgressTracker(ctx['redis'], task_id)
    await progress.update(0, "Initializing matrix processing...", "processing")

    # Parse options
    options = options or {}
    enable_watermark = options.get('enable_watermark', True)
    enable_title_card = options.get('enable_title_card', True)
    enable_music = options.get('enable_music', True)
    enable_captions = options.get('enable_captions', True)
    title_style = options.get('title_style', 'tt3')
    canvas_styles = options.get('canvas_styles', ['original', 'flipped', 'blurry_bg'])

    # Initialize processors
    face_tracker = FaceTracker()
    canvas_renderer = CanvasRenderer()
    watermark_overlay = WatermarkOverlay()
    title_generator = TitleCardGenerator()
    music_swapper = MusicSwapper()
    caption_generator = CaptionGenerator()

    # Get video duration
    video_duration = await get_video_duration(video_path)

    # Extract caption words from transcript
    caption_words = []
    if enable_captions:
        source = transcript_data.get('source', 'mlx')
        if source == 'assemblyai':
            caption_words = CaptionGenerator.words_from_assemblyai(transcript_data)
        else:
            caption_words = CaptionGenerator.words_from_mlx(transcript_data)

    # Get user's watermark
    watermark_path = None
    if enable_watermark:
        watermark_path = watermark_overlay.get_watermark_for_account(user_id)
        if not watermark_path:
            logger.warning(f"No watermark found for user {user_id}, skipping watermarks")
            enable_watermark = False

    # Create output directory structure
    output_base = os.path.join(config.temp_dir, "matrix", task_id)
    os.makedirs(output_base, exist_ok=True)

    all_variations = []
    total_clips = len(base_clips)

    for clip_idx, base_clip in enumerate(base_clips):
        logger.info(f"Processing clip {clip_idx + 1}/{total_clips}: {base_clip['title']}")

        # Update progress
        base_progress = int((clip_idx / total_clips) * 90)
        await progress.update(
            base_progress,
            f"Processing clip {clip_idx + 1}/{total_clips}: {base_clip['title']}...",
            "processing"
        )

        # Parse timestamps
        start_parts = base_clip['start_time'].split(':')
        end_parts = base_clip['end_time'].split(':')
        base_start = int(start_parts[0]) * 60 + int(start_parts[1])
        base_end = int(end_parts[0]) * 60 + int(end_parts[1])

        # STEP 1: Generate temporal variations
        logger.info(f"  Step 1: Generating temporal variations")
        temporal_variations = generate_temporal_variations(
            base_start=float(base_start),
            base_end=float(base_end),
            video_duration=video_duration,
            include_frame_offset=True
        )

        # STEP 2: For each temporal variation, create canvas variations
        for temp_var in temporal_variations:
            logger.info(f"  Step 2: Processing {temp_var.type} variation ({temp_var.duration:.1f}s)")

            # Cut base clip for this temporal variation
            temp_dir = os.path.join(output_base, f"temp_{clip_idx}_{temp_var.type}")
            os.makedirs(temp_dir, exist_ok=True)

            base_clip_path = os.path.join(temp_dir, "base.mp4")
            await cut_clip_ffmpeg(
                video_path,
                base_clip_path,
                temp_var.start_time,
                temp_var.duration
            )

            # STEP 3: Create canvas variations
            for canvas_style in canvas_styles:
                logger.info(f"    Canvas: {canvas_style}")

                # Create canvas variation
                canvas_path = os.path.join(temp_dir, f"canvas_{canvas_style}.mp4")

                if canvas_style == 'original':
                    success = await canvas_renderer.render_original_style(base_clip_path, canvas_path)
                elif canvas_style == 'flipped':
                    success = await canvas_renderer.render_flipped_style(base_clip_path, canvas_path)
                else:  # blurry_bg
                    success = await canvas_renderer.render_blurry_bg_style(base_clip_path, canvas_path)

                if not success:
                    logger.error(f"Failed to render {canvas_style} canvas")
                    continue

                # Current clip path (for chaining processors)
                current_path = canvas_path

                # STEP 4: Add watermark
                if enable_watermark:
                    watermark_out = os.path.join(temp_dir, f"watermark_{canvas_style}.mp4")
                    success = await watermark_overlay.apply_watermark(
                        current_path,
                        watermark_out,
                        watermark_path,
                        position="bottom_right",
                        scale=0.15
                    )
                    if success:
                        current_path = watermark_out

                # STEP 5: Add title card/overlay
                if enable_title_card:
                    title_out = os.path.join(temp_dir, f"title_{canvas_style}.mp4")
                    success = await title_generator.add_title_overlay(
                        current_path,
                        title_out,
                        base_clip['title'],
                        style=title_style
                    )
                    if success:
                        current_path = title_out

                # STEP 6: Add music
                if enable_music:
                    music_out = os.path.join(temp_dir, f"music_{canvas_style}.mp4")
                    selected_song = music_swapper.select_random_song()
                    success = add_music_to_video(
                        current_path,
                        selected_song.path,
                        music_out,
                        music_volume=0.3
                    )
                    if success:
                        current_path = music_out
                        logger.info(f"    Music: {selected_song.filename}")

                # STEP 7: Add captions
                if enable_captions and caption_words:
                    captions_out = os.path.join(temp_dir, f"final_{canvas_style}.mp4")
                    caption_lines = caption_generator.format_captions(
                        caption_words,
                        temp_var.start_time,
                        temp_var.start_time + temp_var.duration
                    )
                    success = await caption_generator.add_captions_to_video(
                        current_path,
                        captions_out,
                        caption_lines,
                        position="center"
                    )
                    if success:
                        current_path = captions_out

                # STEP 8: Move to final location
                final_filename = f"{clip_idx:04d}_{temp_var.type}_{canvas_style}_{base_clip['title'][:30]}.mp4"
                final_filename = "".join(c for c in final_filename if c.isalnum() or c in ('_', '-', '.'))
                final_path = os.path.join(output_base, final_filename)

                os.rename(current_path, final_path)

                # Record variation
                all_variations.append({
                    'clip_index': clip_idx,
                    'base_title': base_clip['title'],
                    'temporal_type': temp_var.type,
                    'canvas_style': canvas_style,
                    'file_path': final_path,
                    'filename': final_filename,
                    'duration': temp_var.duration,
                    'start_time': temp_var.start_time,
                    'end_time': temp_var.start_time + temp_var.duration,
                    'engagement_score': base_clip.get('engagement_score', 0.0),
                    'category': base_clip.get('category', 'unknown')
                })

                logger.info(f"    ✅ Created: {final_filename}")

            # Cleanup temp directory
            import shutil
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)

        # Log progress
        logger.info(f"✅ Completed clip {clip_idx + 1}/{total_clips}")

    # Final progress
    await progress.update(
        95,
        f"Matrix processing complete! Generated {len(all_variations)} total variations",
        "processing"
    )

    logger.info(f"🎉 Matrix processing complete for task {task_id}")
    logger.info(f"Total variations generated: {len(all_variations)}")
    logger.info(f"Output directory: {output_base}")

    # Save manifest
    manifest_path = os.path.join(output_base, "manifest.json")
    with open(manifest_path, 'w') as f:
        json.dump({
            'task_id': task_id,
            'total_variations': len(all_variations),
            'base_clips': total_clips,
            'variations_per_clip': len(all_variations) // total_clips if total_clips > 0 else 0,
            'variations': all_variations
        }, f, indent=2)

    logger.info(f"Saved manifest: {manifest_path}")

    await progress.update(100, "Complete!", "completed")

    return {
        'success': True,
        'task_id': task_id,
        'total_variations': len(all_variations),
        'base_clips': total_clips,
        'output_dir': output_base,
        'manifest_path': manifest_path
    }
