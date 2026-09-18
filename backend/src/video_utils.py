"""Video utils helpers for the video pipeline."""

from .media.common import (
    Any,
    Dict,
    List,
    Optional,
    Path,
    Tuple,
    VALID_OUTPUT_FORMATS,
    find_font_path,
    get_template,
    logger,
    normalize_source_ranges,
    save_clip_source_ranges,
    shutil,
    subprocess,
    tempfile,
    uuid,
)
from .media.ffmpeg import (
    ffprobe_duration,
    ffprobe_video_size,
    render_source_ranges_ffmpeg,
    round_to_even,
    run_ffmpeg_command,
)
from .media.transcription import (
    get_video_transcript,
)
from .media.timeline import (
    build_clip_keep_ranges,
    build_keep_ranges_from_source_ranges,
    extend_keep_ranges_to_sentence_boundary,
    parse_timestamp_to_seconds,
)
from .media.captions import (
    ass_fonts_dir,
    build_assemblyai_ass_subtitles,
)
from .media.reframing import (
    render_reframed_clip_ffmpeg,
)
from .media.ffmpeg import (
    build_audio_output_args,
    build_final_video_encode_args,
    burn_ass_subtitles_ffmpeg,
    clamp_even,
    crossfade_fade_for_ranges,
    ffmpeg_escape_filter_path,
    ffmpeg_escape_filter_value,
    ffprobe_has_audio,
    render_ranges_crossfade_ffmpeg,
    subtitles_filter_fragment,
)
from .media.transcription import (
    _assemblyai_speech_models_value,
    _format_words_for_analysis,
    _get_transcript_with_assemblyai,
    _get_transcript_with_whisper,
    _get_transcript_with_youtube_captions,
    _get_whisper_model,
    _join_transcript_tokens,
    _prepare_audio_for_transcription,
    _serialize_transcript_word,
    _submit_and_wait_for_assemblyai_transcript,
    _whisper_result_to_transcript_data,
    cache_transcript_data,
    format_ms_to_timestamp,
    format_transcript_for_analysis,
    load_cached_transcript_data,
    transcribe_with_whisper,
    transcribe_with_youtube_captions,
)
from .media.timeline import (
    _build_cleanup_phrases,
    _merge_intervals,
    _normalize_cleanup_token,
    build_clip_signal_summary,
    detect_audio_peak_times,
    get_absolute_words_in_range,
    get_transcript_text_in_range,
    get_words_for_keep_ranges,
    get_words_in_range,
    parse_transcript_lines,
    seconds_to_mmss,
    word_ends_sentence,
)
from .media.captions import (
    _balance_title_lines,
    ass_font_name,
    ass_timestamp,
    build_hook_title_ass,
    emoji_rendering_supported,
    escape_ass_text,
    get_safe_vertical_position,
    get_scaled_font_size,
    get_subtitle_max_width,
    hex_to_ass_color,
)
from .media.reframing import (
    _detect_dominant_face,
    _median_filter,
    _open_face_detectors,
    _scene_cuts_from_diffs,
    analyze_vertical_clip,
    build_crop_trajectory,
    build_layout_plan,
    build_pan_expression,
    build_smooth_pan_expression,
    build_speaker_timeline_from_motion,
    build_vertical_compositor_filter,
    build_vertical_filter_plan,
    cluster_two_face_regions,
    compute_vertical_crop_dims,
    count_scene_cuts,
    detect_faces_in_clip,
    detect_optimal_crop_region,
    detect_speaker_reframe_plan,
    filter_face_outliers,
    kenburns_zoom_fragment,
    parse_motion_metadata,
    smooth_values,
    trajectory_has_movement,
)
from .media.common import (
    ANALYSIS_LONG_UTTERANCE_MAX_DURATION_MS,
    ANALYSIS_LONG_UTTERANCE_MAX_WORDS,
    ANALYSIS_SEGMENT_MAX_DURATION_MS,
    ANALYSIS_SEGMENT_MAX_WORDS,
    ANALYSIS_SEGMENT_MIN_WORDS,
    ANALYSIS_UTTERANCE_SPLIT_THRESHOLD_MS,
    ANALYSIS_UTTERANCE_SPLIT_THRESHOLD_WORDS,
    AUDIO_BITRATE,
    CAPTION_TEMPLATES,
    CLIP_END_PADDING_SECONDS,
    CLIP_END_SENTENCE_EXTENSION_SECONDS,
    DEFAULT_FILTERED_WORDS,
    EMOJI_FONT_NAME,
    FACE_PRESENCE_MIN_AREA,
    FACE_PRESENCE_RATE,
    FACE_RATE_WINDOW,
    FINAL_VIDEO_CRF,
    FINAL_VIDEO_PRESET,
    FONTS_DIR,
    HOOK_TITLE_MIN_SECONDS,
    HOOK_TITLE_SECONDS,
    HOOK_TITLE_TOP_MARGIN_FRAC,
    INTERMEDIATE_CRF,
    KENBURNS_MIN_SECONDS,
    KENBURNS_SUPERSAMPLE_H,
    KENBURNS_SUPERSAMPLE_W,
    KENBURNS_ZOOM_DELTA,
    LAYOUT_SNAP_WINDOW,
    LOUDNORM_FILTER,
    MIN_LAYOUT_SECONDS,
    OUTPUT_FPS,
    POWER_WORDS,
    SENTENCE_END_RE,
    TRANSCRIPT_CACHE_SCHEMA_VERSION,
    ThreadPoolExecutor,
    _EMOJI_SUPPORT_CACHE,
    _WHISPER_AVAILABLE,
    _WHISPER_MODEL_CACHE,
    _whisper,
    aai,
    annotate_caption_words,
    clip_cleanup_enabled,
    cv2,
    get_config,
    get_font_family_name,
    httpx,
    json,
    logging,
    normalize_token,
    np,
    re,
    srt,
    time,
    timedelta,
)


class VideoProcessor:
    """Handles video processing operations with optimized settings."""

    def __init__(
        self,
        font_family: str = "THEBOLDFONT",
        font_size: int = 24,
        font_color: str = "#FFFFFF",
    ):
        self.font_family = font_family
        self.font_size = font_size
        self.font_color = font_color
        resolved_font = find_font_path(font_family, allow_all_user_fonts=True)
        if not resolved_font:
            resolved_font = find_font_path("TikTokSans-Regular")
        if not resolved_font:
            resolved_font = find_font_path("THEBOLDFONT")
        self.font_path = str(resolved_font) if resolved_font else ""

    def get_optimal_encoding_settings(
        self, target_quality: str = "high"
    ) -> Dict[str, Any]:
        """Get optimal encoding settings for different quality levels."""
        settings = {
            "high": {
                "codec": "libx264",
                "audio_codec": "aac",
                "audio_bitrate": "256k",
                "preset": "slow",
                "ffmpeg_params": [
                    "-crf",
                    "18",
                    "-pix_fmt",
                    "yuv420p",
                    "-profile:v",
                    "high",
                    "-movflags",
                    "+faststart",
                    "-sws_flags",
                    "lanczos",
                ],
            },
            "medium": {
                "codec": "libx264",
                "audio_codec": "aac",
                "bitrate": "4000k",
                "audio_bitrate": "192k",
                "preset": "fast",
                "ffmpeg_params": ["-crf", "23", "-pix_fmt", "yuv420p"],
            },
        }
        return settings.get(target_quality, settings["high"])


def create_optimized_clip(
    video_path: Path,
    start_time: float,
    end_time: float,
    output_path: Path,
    add_subtitles: bool = True,
    font_family: Optional[str] = None,
    font_size: Optional[int] = None,
    font_color: Optional[str] = None,
    caption_template: str = "default",
    output_format: str = "vertical",
    keep_ranges: Optional[List[Tuple[float, float]]] = None,
    hook_title: Optional[str] = None,
    extend_to_sentence: bool = True,
) -> bool:
    """Create clip with optional subtitles. output_format: 'vertical' (9:16) or 'original' (keep source size)."""
    try:
        if keep_ranges:
            effective_keep_ranges = normalize_source_ranges(keep_ranges)
        else:
            effective_keep_ranges = [
                (max(start_time, start), min(end_time, end))
                for start, end in [(start_time, end_time)]
                if min(end_time, end) - max(start_time, start) > 0.05
            ]
        if extend_to_sentence:
            effective_keep_ranges = extend_keep_ranges_to_sentence_boundary(
                video_path, effective_keep_ranges,
            )
        duration = sum(end - start for start, end in effective_keep_ranges)
        if duration <= 0:
            logger.error(f"Invalid clip duration: {duration:.1f}s")
            return False

        keep_original = output_format == "original"
        logger.info(
            f"Creating clip: {start_time:.1f}s - {end_time:.1f}s ({duration:.1f}s) "
            f"subtitles={add_subtitles} template '{caption_template}' format={'original' if keep_original else 'vertical'}"
        )

        # Fast path: no subtitles + original = ffmpeg stream copy (no re-encoding)
        if not add_subtitles and not hook_title and keep_original and len(effective_keep_ranges) == 1 and extend_to_sentence:
            fast_path_start, fast_path_end = effective_keep_ranges[0]
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-ss", str(fast_path_start),
                    "-i", str(video_path),
                    "-t", str(fast_path_end - fast_path_start),
                    "-c", "copy",
                    "-movflags", "+faststart",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode != 0:
                logger.error(f"ffmpeg stream copy failed: {result.stderr}")
                return False
            logger.info(f"Successfully created clip (stream copy): {output_path}")
            return True

        with tempfile.TemporaryDirectory(prefix="supoclip_render_") as temp_dir:
            temp_root = Path(temp_dir)
            source_clip_path = temp_root / "source.mp4"
            final_clip_path = temp_root / "final.mp4"
            ass_path = temp_root / "captions.ass"

            if not render_source_ranges_ffmpeg(
                video_path,
                effective_keep_ranges,
                source_clip_path,
            ):
                raise RuntimeError("ffmpeg source-range render failed")

            reframe_format = (
                output_format if output_format in VALID_OUTPUT_FORMATS else "vertical"
            )

            # Output dimensions are known ahead of the render: vertical modes are
            # always 1080x1920, "original" keeps the (even) source size. Knowing
            # them lets us build the ASS captions up front and burn them in the
            # SAME pass as reframing — one encode instead of two.
            if reframe_format == "original":
                src_w, src_h = ffprobe_video_size(source_clip_path)
                target_width, target_height = round_to_even(src_w), round_to_even(src_h)
            else:
                target_width, target_height = 1080, 1920

            burn_ass_path: Optional[Path] = None
            fonts_dir: Optional[Path] = None
            if (add_subtitles or hook_title) and build_assemblyai_ass_subtitles(
                video_path,
                start_time,
                end_time,
                target_width,
                target_height,
                ass_path,
                font_family,
                font_size,
                font_color,
                caption_template,
                effective_keep_ranges,
                hook_title=hook_title,
                include_captions=add_subtitles,
            ):
                burn_ass_path = ass_path
                fonts_dir = ass_fonts_dir(
                    font_family or get_template(caption_template)["font_family"]
                )

            framed_ok, _, _ = render_reframed_clip_ffmpeg(
                source_clip_path,
                final_clip_path,
                reframe_format,
                subtitle_ass_path=burn_ass_path,
                fonts_dir=fonts_dir,
            )
            if not framed_ok:
                raise RuntimeError("ffmpeg reframe render failed")

            shutil.move(str(final_clip_path), str(output_path))
            logger.info(f"Successfully created clip with ffmpeg: {output_path}")
            return True

    except Exception as e:
        logger.error(f"Failed to create clip: {e}")
        return False


def create_clips_from_segments(
    video_path: Path,
    segments: List[Dict[str, Any]],
    output_dir: Path,
    font_family: Optional[str] = None,
    font_size: Optional[int] = None,
    font_color: Optional[str] = None,
    caption_template: str = "default",
    output_format: str = "vertical",
    add_subtitles: bool = True,
    cleanup_settings: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Create optimized video clips from segments with template support."""
    logger.info(
        f"Creating {len(segments)} clips subtitles={add_subtitles} template '{caption_template}'"
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    clips_info = []

    for i, segment in enumerate(segments):
        try:
            # Debug log the segment data
            logger.info(
                f"Processing segment {i + 1}: start='{segment.get('start_time')}', end='{segment.get('end_time')}'"
            )

            provided_keep_ranges = normalize_source_ranges(segment.get("keep_ranges"))
            provided_source_ranges = normalize_source_ranges(segment.get("source_ranges"))
            if provided_keep_ranges:
                start_seconds = provided_keep_ranges[0][0]
                end_seconds = provided_keep_ranges[-1][1]
            elif provided_source_ranges:
                start_seconds = provided_source_ranges[0][0]
                end_seconds = provided_source_ranges[-1][1]
            else:
                start_seconds = parse_timestamp_to_seconds(segment["start_time"])
                end_seconds = parse_timestamp_to_seconds(segment["end_time"])

            duration = end_seconds - start_seconds
            logger.info(
                f"Segment {i + 1} duration: {duration:.1f}s (start: {start_seconds}s, end: {end_seconds}s)"
            )

            if duration <= 0:
                logger.warning(
                    f"Skipping clip {i + 1}: invalid duration {duration:.1f}s (start: {start_seconds}s, end: {end_seconds}s)"
                )
                continue

            clip_filename = (
                f"clip_{i + 1}_{segment['start_time'].replace(':', '')}-"
                f"{segment['end_time'].replace(':', '')}_{uuid.uuid4().hex[:12]}.mp4"
            )
            clip_path = output_dir / clip_filename

            if provided_keep_ranges:
                keep_ranges = provided_keep_ranges
            elif provided_source_ranges:
                keep_ranges = build_keep_ranges_from_source_ranges(
                    video_path,
                    provided_source_ranges,
                    cleanup_settings,
                )
            else:
                keep_ranges = build_clip_keep_ranges(
                    video_path, start_seconds, end_seconds, cleanup_settings
                )
            keep_ranges = extend_keep_ranges_to_sentence_boundary(video_path, keep_ranges)

            success = create_optimized_clip(
                video_path,
                start_seconds,
                end_seconds,
                clip_path,
                add_subtitles,
                font_family,
                font_size,
                font_color,
                caption_template,
                output_format,
                keep_ranges,
                hook_title=segment.get("hook_title"),
            )

            if success:
                save_clip_source_ranges(clip_path, keep_ranges)
                cleaned_duration = sum(end - start for start, end in keep_ranges)
                clip_info = {
                    "clip_id": i + 1,
                    "filename": clip_filename,
                    "path": str(clip_path),
                    "start_time": segment["start_time"],
                    "end_time": segment["end_time"],
                    "duration": cleaned_duration,
                    "text": segment["text"],
                    "relevance_score": segment["relevance_score"],
                    "reasoning": segment["reasoning"],
                    # Include virality data if available
                    "virality_score": segment.get("virality_score", 0),
                    "hook_score": segment.get("hook_score", 0),
                    "engagement_score": segment.get("engagement_score", 0),
                    "value_score": segment.get("value_score", 0),
                    "shareability_score": segment.get("shareability_score", 0),
                    "hook_type": segment.get("hook_type"),
                    "hook_title": segment.get("hook_title"),
                    "keep_ranges": keep_ranges,
                }
                clips_info.append(clip_info)
                logger.info(f"Created clip {i + 1}: {cleaned_duration:.1f}s")
            else:
                logger.error(f"Failed to create clip {i + 1}")

        except Exception as e:
            logger.error(f"Error processing clip {i + 1}: {e}")

    logger.info(f"Successfully created {len(clips_info)}/{len(segments)} clips")
    return clips_info


def get_available_transitions() -> List[str]:
    """Get list of available transition video files."""
    transitions_dir = Path(__file__).parent.parent / "transitions"
    if not transitions_dir.exists():
        logger.warning("Transitions directory not found")
        return []

    transition_files = []
    for file_path in transitions_dir.glob("*.mp4"):
        transition_files.append(str(file_path))

    logger.info(f"Found {len(transition_files)} transition files")
    return transition_files


def apply_transition_effect(
    clip1_path: Path, clip2_path: Path, transition_path: Path, output_path: Path
) -> bool:
    """Apply transition effect between two clips using a transition video."""
    try:
        clip1_duration = ffprobe_duration(clip1_path)
        clip2_duration = ffprobe_duration(clip2_path)
        transition_duration = min(1.5, clip1_duration, clip2_duration)
        if transition_duration <= 0:
            logger.warning("Transition duration is zero, skipping transition effect")
            return False

        width, height = ffprobe_video_size(clip2_path)
        clip1_tail_start = max(0.0, clip1_duration - transition_duration)
        filter_parts = [
            (
                f"[0:v]trim=start={clip1_tail_start:.3f}:end={clip1_duration:.3f},"
                f"setpts=PTS-STARTPTS,scale={width}:{height}:flags=lanczos[v0]"
            ),
            (
                f"[1:v]trim=start=0:end={transition_duration:.3f},"
                f"setpts=PTS-STARTPTS,scale={width}:{height}:flags=lanczos[v1]"
            ),
            (
                f"[v0][v1]xfade=transition=fade:duration={transition_duration:.3f}:"
                "offset=0[vintro]"
            ),
        ]
        if clip2_duration - transition_duration > 0.05:
            filter_parts.extend(
                [
                    (
                        f"[1:v]trim=start={transition_duration:.3f}:end={clip2_duration:.3f},"
                        "setpts=PTS-STARTPTS[vrem]"
                    ),
                    "[vintro][vrem]concat=n=2:v=1:a=0[v]",
                ]
            )
            video_label = "[v]"
        else:
            video_label = "[vintro]"

        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(clip1_path),
            "-i",
            str(clip2_path),
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            video_label,
            "-map",
            "1:a?",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        success = run_ffmpeg_command(command).returncode == 0
        if success:
            logger.info("Applied transition effect: %s", output_path)
        return success

    except Exception as e:
        logger.error(f"Error applying transition effect: {e}")
        return False


def resize_for_916_filter(target_width: int, target_height: int) -> str:
    """Return a scale/crop filter that fills a target portrait frame."""
    return (
        f"scale={target_width}:{target_height}:force_original_aspect_ratio=increase:"
        f"flags=lanczos,crop={target_width}:{target_height},setsar=1"
    )


def create_clips_with_transitions(
    video_path: Path,
    segments: List[Dict[str, Any]],
    output_dir: Path,
    font_family: Optional[str] = None,
    font_size: Optional[int] = None,
    font_color: Optional[str] = None,
    caption_template: str = "default",
    output_format: str = "vertical",
    add_subtitles: bool = True,
    cleanup_settings: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Create standalone video clips without inter-clip transitions.

    Kept as a backward-compatible wrapper for older call sites.
    """
    logger.info(
        f"Creating {len(segments)} standalone clips subtitles={add_subtitles} template '{caption_template}'"
    )
    logger.info(
        "Inter-clip transitions are disabled for standalone SupoClip exports"
    )
    return create_clips_from_segments(
        video_path,
        segments,
        output_dir,
        font_family,
        font_size,
        font_color,
        caption_template,
        output_format,
        add_subtitles,
        cleanup_settings,
    )


def get_video_transcript_with_assemblyai(path: Path) -> str:
    """Backward compatibility wrapper."""
    return get_video_transcript(path)


def create_9_16_clip(
    video_path: Path,
    start_time: float,
    end_time: float,
    output_path: Path,
    subtitle_text: str = "",
) -> bool:
    """Backward compatibility wrapper."""
    return create_optimized_clip(
        video_path, start_time, end_time, output_path, add_subtitles=bool(subtitle_text)
    )


def insert_broll_into_clip(
    main_clip_path: Path,
    broll_path: Path,
    insert_time: float,
    broll_duration: float,
    output_path: Path,
    transition_duration: float = 0.3,
) -> bool:
    """
    Insert B-roll footage into a clip at a specified timestamp.

    Args:
        main_clip_path: Path to the main video clip
        broll_path: Path to the B-roll video
        insert_time: When to insert B-roll (seconds from clip start)
        broll_duration: How long to show B-roll (seconds)
        output_path: Where to save the composited clip
        transition_duration: Crossfade duration (seconds)

    Returns:
        True if successful
    """
    try:
        main_duration = ffprobe_duration(main_clip_path)
        source_broll_duration = ffprobe_duration(broll_path)
        target_width, target_height = ffprobe_video_size(main_clip_path)

        insert_time = max(0.0, min(insert_time, max(0.0, main_duration - 0.5)))
        actual_broll_duration = min(
            max(0.0, broll_duration),
            source_broll_duration,
            max(0.0, main_duration - insert_time),
        )
        if actual_broll_duration <= 0.05:
            logger.warning("B-roll duration is too short, skipping insertion")
            return False

        broll_end_time = insert_time + actual_broll_duration
        fade_duration = min(
            max(0.0, transition_duration),
            max(0.0, actual_broll_duration / 3),
        )

        filter_parts: List[str] = []
        concat_labels: List[str] = []
        segment_count = 0
        if insert_time > 0.05:
            filter_parts.append(
                f"[0:v]trim=start=0:end={insert_time:.3f},setpts=PTS-STARTPTS[vpre]"
            )
            concat_labels.append("[vpre]")
            segment_count += 1

        broll_filter = (
            f"[1:v]trim=start=0:end={actual_broll_duration:.3f},setpts=PTS-STARTPTS,"
            f"{resize_for_916_filter(target_width, target_height)}"
        )
        if fade_duration > 0:
            broll_filter += (
                f",fade=t=in:st=0:d={fade_duration:.3f},"
                f"fade=t=out:st={max(0.0, actual_broll_duration - fade_duration):.3f}:"
                f"d={fade_duration:.3f}"
            )
        filter_parts.append(f"{broll_filter}[vbroll]")
        concat_labels.append("[vbroll]")
        segment_count += 1

        if main_duration - broll_end_time > 0.05:
            filter_parts.append(
                f"[0:v]trim=start={broll_end_time:.3f}:end={main_duration:.3f},"
                "setpts=PTS-STARTPTS[vpost]"
            )
            concat_labels.append("[vpost]")
            segment_count += 1

        if segment_count > 1:
            filter_parts.append(
                f"{''.join(concat_labels)}concat=n={segment_count}:v=1:a=0[v]"
            )
            video_label = "[v]"
        else:
            video_label = concat_labels[0]

        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(main_clip_path),
            "-i",
            str(broll_path),
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            video_label,
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        if run_ffmpeg_command(command).returncode != 0:
            return False

        logger.info(
            f"Inserted B-roll at {insert_time:.1f}s ({actual_broll_duration:.1f}s duration): {output_path}"
        )
        return True

    except Exception as e:
        logger.error(f"Error inserting B-roll: {e}")
        return False


def apply_broll_to_clip(
    clip_path: Path, broll_suggestions: List[Dict[str, Any]], output_path: Path
) -> bool:
    """
    Apply multiple B-roll insertions to a clip.

    Args:
        clip_path: Path to the main clip
        broll_suggestions: List of B-roll suggestions with local_path, timestamp, duration
        output_path: Where to save the final clip

    Returns:
        True if successful
    """
    if not broll_suggestions:
        logger.info("No B-roll suggestions to apply")
        return False

    try:
        # Sort suggestions by timestamp (process from end to start to preserve timing)
        sorted_suggestions = sorted(
            broll_suggestions, key=lambda x: x.get("timestamp", 0), reverse=True
        )

        current_clip_path = clip_path
        temp_paths = []

        for i, suggestion in enumerate(sorted_suggestions):
            broll_path = suggestion.get("local_path")
            if not broll_path or not Path(broll_path).exists():
                logger.warning(f"B-roll file not found: {broll_path}")
                continue

            timestamp = suggestion.get("timestamp", 0)
            duration = suggestion.get("duration", 3.0)

            # Create temp output for intermediate clips
            if i < len(sorted_suggestions) - 1:
                temp_output = output_path.parent / f"temp_broll_{i}.mp4"
                temp_paths.append(temp_output)
            else:
                temp_output = output_path

            success = insert_broll_into_clip(
                current_clip_path, Path(broll_path), timestamp, duration, temp_output
            )

            if success:
                current_clip_path = temp_output
            else:
                logger.warning(f"Failed to insert B-roll at {timestamp}s")

        # Cleanup temp files
        for temp_path in temp_paths:
            if temp_path.exists() and temp_path != output_path:
                try:
                    temp_path.unlink()
                except Exception:
                    pass

        return True

    except Exception as e:
        logger.error(f"Error applying B-roll to clip: {e}")
        return False
