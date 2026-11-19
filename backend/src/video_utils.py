"""Video utils helpers for the video pipeline."""

from dataclasses import dataclass, field

from .clip_source_map import save_clip_broll, save_clip_caption_settings, save_clip_vertical_layout
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
    source_range_bounds,
    subprocess,
    tempfile,
    uuid,
)
from .media.ffmpeg import (
    audio_stream_map_args,
    ffprobe_duration,
    ffprobe_video_size,
    render_source_ranges_ffmpeg,
    round_to_even,
    run_ffmpeg_command,
    select_audio_stream_index,
)
from .media.source_limits import (
    fit_within_1080p,
    probe_video_geometry,
    source_within_output_limits,
)
from .media.transcription import (
    get_video_transcript,
)
from .media.timeline import (
    add_hook_teaser_to_keep_ranges,
    apply_segment_hook_teaser,
    build_clip_keep_ranges,
    build_hook_teaser_range,
    build_keep_ranges_from_source_ranges,
    clamp_keep_ranges_to_max_duration,
    extend_keep_ranges_to_sentence_boundary,
    parse_timestamp_to_seconds,
    keep_ranges_transcript_text,
    snap_clip_start_to_sentence,
    snap_keep_ranges_to_sentence_start,
)
from .media.captions import (
    ass_fonts_dir,
    build_assemblyai_ass_subtitles,
)
from .media.render_ranges import burned_hook_title, point_labels_for, resolve_render_ranges, segment_bounds
from .media.reframing import (
    render_reframed_clip_ffmpeg,
)
from .media.fx_chain import FxRenderInput
from .edit_fx.contracts import FxPlan
from .media.source_captions import (
    band_through_full_height_crop,
    detect_burned_caption_band,
)
from .media.speaker_framing import (
    build_clip_speaker_turns,
)
from .media.ffmpeg import (
    build_audio_output_args,
    NO_CHAPTERS,
    build_final_video_encode_args,
    burn_ass_subtitles_ffmpeg,
    clamp_even,
    crossfade_fade_for_ranges,
    ffmpeg_escape_filter_path,
    ffmpeg_escape_filter_value,
    ffprobe_has_audio,
    render_ranges_crossfade_ffmpeg,
    rendered_clip_duration,
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
    layout_report: Optional[Dict[str, Any]] = None,
    broll_overlays: Optional[List[Dict[str, Any]]] = None,
    output_offsets: Optional[List[float]] = None,
    point_labels: Optional[List[Dict[str, Any]]] = None,
    fx: Optional["FxRenderInput"] = None,
    caption_override: Optional[Dict[str, Any]] = None,
) -> bool:
    """Create clip with optional subtitles. output_format: 'vertical' (9:16) or 'original' (keep source size).

    ``broll_overlays`` are stock cutaways (path, start, duration on the clip
    timeline) composited under the captions. If that render fails the clip is
    rendered without them; ``layout_report["broll_applied"]`` says which.

    ``output_offsets`` (one per kept range) fixes where each range starts on
    the clip timeline, for workflow clips with planned junction fades. The
    stitch, captions and speaker framing all follow them. Without them the
    renderer's default crossfades apply.

    ``point_labels`` are a recap's on-screen points ({"text", "start"} on the
    clip timeline), burned in with the captions.

    ``fx`` (dynamic edits) renders the clip through the fx chain with the
    fallback ladder full → full without B-roll → safe → none (FINAL §6.6);
    ``layout_report`` then receives ``fx_level``, ``applied_warp``,
    ``face_track`` and ``fx_plan`` (the plan as rendered). ``caption_override``
    (``words``, ``highlight_words``, ``position_y``, ``font_size``) replaces the
    transcript captions and their placement (the FX caption edit).
    """
    try:
        labels_kwargs = {"point_labels": point_labels} if point_labels else {}
        if keep_ranges:
            effective_keep_ranges = normalize_source_ranges(keep_ranges)
        else:
            effective_keep_ranges = [
                (max(start_time, start), min(end_time, end))
                for start, end in [(start_time, end_time)]
                if min(end_time, end) - max(start_time, start) > 0.05
            ]
        if extend_to_sentence:
            if not keep_ranges:
                # Only raw bounds are snapped; supplied ranges may carry
                # cleanup cuts or manual trims that must not be undone.
                effective_keep_ranges = snap_keep_ranges_to_sentence_start(
                    video_path, effective_keep_ranges,
                )
            effective_keep_ranges = extend_keep_ranges_to_sentence_boundary(
                video_path, effective_keep_ranges,
            )
            effective_keep_ranges = clamp_keep_ranges_to_max_duration(
                video_path, effective_keep_ranges,
            )
        duration = sum(end - start for start, end in effective_keep_ranges)
        if duration <= 0:
            logger.error(f"Invalid clip duration: {duration:.1f}s")
            return False
        if output_offsets is not None and len(output_offsets) != len(effective_keep_ranges):
            raise ValueError(
                f"{len(output_offsets)} output offsets for {len(effective_keep_ranges)} kept ranges"
            )
        # Planned offsets reach every consumer of the clip timeline; legacy
        # calls keep their original arguments.
        offsets_kwargs = {"output_offsets": output_offsets} if output_offsets is not None else {}

        keep_original = output_format == "original"
        logger.info(
            f"Creating clip: {start_time:.1f}s - {end_time:.1f}s ({duration:.1f}s) "
            f"subtitles={add_subtitles} template '{caption_template}' format={'original' if keep_original else 'vertical'}"
        )

        # Fast path: no subtitles + original = ffmpeg stream copy (no re-encoding).
        # Oversized or high-frame-rate sources fall through so the render caps them.
        if (
            not add_subtitles
            and not hook_title
            and not point_labels
            and not broll_overlays
            and not fx
            and keep_original
            and len(effective_keep_ranges) == 1
            and (output_offsets is None or len(output_offsets) == 1)
            and extend_to_sentence
            and source_within_output_limits(video_path)
        ):
            fast_path_start, fast_path_end = effective_keep_ranges[0]
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-ss", str(fast_path_start),
                    "-i", str(video_path),
                    *audio_stream_map_args(select_audio_stream_index(video_path)),
                    *NO_CHAPTERS,
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
                **offsets_kwargs,
            ):
                raise RuntimeError("ffmpeg source-range render failed")

            reframe_format = (
                output_format if output_format in VALID_OUTPUT_FORMATS else "vertical"
            )

            # Output dimensions are known ahead of the render: vertical modes are
            # always 1080x1920, "original" keeps the (even) source size. Knowing
            # them lets us build the ASS captions up front and burn them in the
            # SAME pass as reframing — one encode instead of two.
            src_w, src_h = ffprobe_video_size(source_clip_path)
            if reframe_format == "original":
                # Captions are burned after the 1080p/60 scale, so their play
                # resolution has to be the capped display size. A 4K or rotated
                # master would otherwise place them on the unscaled frame.
                geometry = probe_video_geometry(source_clip_path)
                if geometry is not None:
                    target_width, target_height = fit_within_1080p(
                        geometry.display_width, geometry.display_height
                    )
                    target_width, target_height = round_to_even(target_width), round_to_even(target_height)
                else:
                    target_width, target_height = round_to_even(src_w), round_to_even(src_h)
            else:
                target_width, target_height = 1080, 1920

            speaker_turns: List[Dict[str, Any]] = []
            speaker_words: List[Dict[str, Any]] = []
            vertical_plan = None
            caption_y = None
            plan_report: Dict[str, Any] = {}
            if reframe_format == "vertical":
                if get_config().speaker_framing:
                    try:
                        speaker_turns, speaker_words = build_clip_speaker_turns(
                            video_path, effective_keep_ranges, **offsets_kwargs
                        )
                    except Exception as exc:
                        logger.warning("Speaker turn extraction failed: %s", exc)
                try:
                    video_filter, mode, caption_y = build_vertical_filter_plan(
                        source_clip_path, src_w, src_h, speaker_turns, speaker_words,
                        report=plan_report,
                        # FX path: never Ken Burns; fx-core draws an editable push instead (one zoom source).
                        **({"allow_kenburns": False} if fx else {}),
                    )
                    vertical_plan = (video_filter, mode)
                except Exception as exc:
                    logger.warning("Vertical layout planning failed: %s", exc)
                    caption_y = None
            if layout_report is not None and caption_y is not None:
                layout_report["caption_y"] = caption_y
            if layout_report is not None and vertical_plan is not None:
                # The editor replays this exact plan instead of guessing a
                # centre crop (see bake_editor_layout).
                try:
                    layout_report["vertical_layout"] = {
                        "kind": plan_report.get("kind") or "generated",
                        "video_filter": vertical_plan[0],
                        "mode": vertical_plan[1],
                        "width": src_w,
                        "height": src_h,
                        "duration": ffprobe_duration(source_clip_path),
                        # Interview shots: the studio derives its caption rows from them.
                        **({"segments": plan_report["interview_segments"]}
                           if plan_report.get("interview_segments") else {}),
                    }
                except Exception as exc:
                    logger.warning("Could not record the vertical layout for the editor: %s", exc)

            # Detect source captions in both polarities, then inspect their
            # actual composed positions for fit, reaction, stack and split layouts.
            source_caption_band = None
            if add_subtitles:
                try:
                    source_caption_band = detect_burned_caption_band(source_clip_path)
                    if source_caption_band and reframe_format != "original":
                        from .media.source_captions import detect_rendered_caption_band
                        from .media.reframing import caption_probe_plan
                        probe_filter, probe_mode = caption_probe_plan(source_clip_path, reframe_format, vertical_plan)
                        source_caption_band = detect_rendered_caption_band(source_clip_path, probe_filter, probe_mode, ffprobe_duration(source_clip_path))
                except Exception as exc:
                    logger.warning("Burned-in caption detection failed: %s", exc)
                    source_caption_band = None
            if layout_report is not None and source_caption_band is not None:
                layout_report["source_caption_band"] = source_caption_band
                if isinstance(layout_report.get("vertical_layout"), dict) and plan_report.get("interview_segments"):
                    layout_report["vertical_layout"]["caption_band"] = list(source_caption_band)

            override = caption_override or {}
            ass_font_size = override.get("font_size") or font_size
            position_y = override["position_y"] if override.get("position_y") is not None else caption_y
            override_kwargs: Dict[str, Any] = {}
            if override.get("words") is not None:
                override_kwargs["caption_words"] = list(override["words"])
            if override.get("highlight_words"):
                override_kwargs["highlight_words"] = list(override["highlight_words"])

            def build_ass(
                include_captions: bool,
                hook: Optional[str],
                template_override: Optional[Dict[str, Any]] = None,
                time_map=None,
                censor_spans=None,
            ) -> Optional[Path]:
                if not (include_captions or hook or point_labels):
                    return None
                built = build_assemblyai_ass_subtitles(
                    video_path,
                    start_time,
                    end_time,
                    target_width,
                    target_height,
                    ass_path,
                    font_family,
                    ass_font_size,
                    font_color,
                    caption_template,
                    effective_keep_ranges,
                    hook_title=hook,
                    include_captions=include_captions,
                    position_y_override=position_y,
                    avoid_band=source_caption_band,
                    # A hook-only ASS over fx-core captions must not overwrite where fx-core drew them.
                    caption_layout_report=layout_report if (include_captions or fx is None) else None,
                    **offsets_kwargs,
                    **labels_kwargs,
                    **override_kwargs,
                    **({"template_override": template_override} if template_override else {}),
                    **({"time_map": time_map} if time_map is not None else {}),
                    **({"censor_spans": censor_spans} if censor_spans else {}),
                    # Only stacked interview openings move the hook title.
                    **_interview_hook_kwargs(plan_report),
                    # A Dynamic edits clip rendered without the drawing stage: heavy outlined type, no boxes.
                    **({"hook_heavy": True} if fx is not None else {}),
                )
                return ass_path if built else None

            if fx is not None:
                framed_ok = _render_fx_ladder(
                    fx,
                    source_clip_path=source_clip_path,
                    final_clip_path=final_clip_path,
                    reframe_format=reframe_format,
                    width=target_width,
                    height=target_height,
                    src_size=(src_w, src_h),
                    effective_keep_ranges=effective_keep_ranges,
                    output_offsets=output_offsets,
                    add_subtitles=add_subtitles,
                    hook_title=hook_title,
                    font_family=font_family,
                    font_size=ass_font_size,
                    font_color=font_color,
                    caption_template=caption_template,
                    caption_y=position_y,
                    caption_y_pinned=override.get("position_y") is not None,
                    source_caption_band=source_caption_band,
                    caption_words=override.get("words"),
                    highlight_words=override.get("highlight_words"),
                    speaker_turns=speaker_turns,
                    speaker_words=speaker_words,
                    vertical_plan=vertical_plan,
                    plan_report=plan_report,
                    broll_overlays=broll_overlays,
                    layout_report=layout_report,
                    build_ass=build_ass,
                )
                if not framed_ok:
                    raise RuntimeError("ffmpeg reframe render failed")
                shutil.move(str(final_clip_path), str(output_path))
                logger.info(f"Successfully created clip with ffmpeg: {output_path}")
                return True

            burn_ass_path: Optional[Path] = None
            fonts_dir: Optional[Path] = None
            # An FX template without fx (its planning failed) burns its legacy ASS fallback, as the ladder's
            # none level does. Legacy templates take the unchanged call.
            from .edit_fx.contracts import is_fx_template

            ass_template = (
                get_template(get_template(caption_template).get("ass_fallback") or "default")
                if is_fx_template(caption_template)
                else None
            )
            if (add_subtitles or hook_title or point_labels) and build_ass(
                add_subtitles, hook_title, template_override=ass_template
            ):
                burn_ass_path = ass_path
                fonts_dir = ass_fonts_dir(
                    font_family or (ass_template or get_template(caption_template))["font_family"]
                )

            def render(overlays):
                return render_reframed_clip_ffmpeg(
                    source_clip_path,
                    final_clip_path,
                    reframe_format,
                    subtitle_ass_path=burn_ass_path,
                    fonts_dir=fonts_dir,
                    speaker_turns=speaker_turns,
                    speaker_words=speaker_words,
                    vertical_plan=vertical_plan,
                    broll_overlays=overlays,
                )[0]

            framed_ok = render(broll_overlays)
            broll_applied = bool(broll_overlays) and framed_ok
            if not framed_ok and broll_overlays:
                # Stock footage must never cost the user the clip itself.
                logger.warning("Render with B-roll failed; rendering the clip without it")
                framed_ok = render(None)
            if layout_report is not None:
                layout_report["broll_applied"] = broll_applied
            if not framed_ok:
                raise RuntimeError("ffmpeg reframe render failed")

            shutil.move(str(final_clip_path), str(output_path))
            logger.info(f"Successfully created clip with ffmpeg: {output_path}")
            return True

    except Exception as e:
        logger.error(f"Failed to create clip: {e}")
        return False


# --------------------------------------------------------------------------- dynamic edits (FINAL §6.1, §6.6)

# Passage breaks: a junction whose source gap is at least this long reads as a new passage (AP-M2).
FX_PASSAGE_GAP_SECONDS = 3.0
# A/V check after a warped render: video and audio must agree within one frame (G4).
FX_AV_TOLERANCE_SECONDS = 0.034
# The hook title band starts a little after 0 (its fade in), FINAL §6.1.2.
FX_HOOK_WINDOW_START = 0.12
# Shortest span cue kept after the plan is clamped to the probed stage-1 length.
FX_MIN_SPAN_SECONDS = 0.04


def fx_junctions(
    keep_ranges: List[Tuple[float, float]],
    output_offsets: Optional[List[float]] = None,
    group_output_starts: Optional[List[Optional[float]]] = None,
) -> Tuple[List[float], List[float]]:
    """Junction centres on the clip (E) timeline, and the subset that starts a new passage.

    ``cuts`` are ``offset_i + fade_i / 2`` for every junction (AR-m2; hard cuts have fade 0). A junction is a
    passage break when its source gap is at least ``FX_PASSAGE_GAP_SECONDS`` or it jumps backwards (a cold-open
    teaser), or when it starts a workflow group (``group_output_starts``).
    """
    from .media.ffmpeg import junction_fades_for_offsets, output_offsets_for_ranges

    ranges = normalize_source_ranges(keep_ranges)
    if len(ranges) < 2:
        return [], []
    offsets = list(output_offsets) if output_offsets is not None else output_offsets_for_ranges(ranges)
    if len(offsets) != len(ranges):
        return [], []
    fades = junction_fades_for_offsets(ranges, offsets)
    group_starts = [float(v) for v in group_output_starts or [] if v is not None and float(v) > 0]
    cuts: List[float] = []
    breaks: List[float] = []
    for index in range(1, len(ranges)):
        centre = round(float(offsets[index]) + fades[index - 1] / 2.0, 3)
        cuts.append(centre)
        gap = ranges[index][0] - ranges[index - 1][1]
        starts_group = any(abs(start - float(offsets[index])) < 1e-3 for start in group_starts)
        if gap >= FX_PASSAGE_GAP_SECONDS or gap < -1e-6 or starts_group:
            breaks.append(centre)
    return cuts, breaks


def fx_broll_windows(overlays: Optional[List[Dict[str, Any]]]) -> List[Tuple[float, float]]:
    """Clip-timeline windows covered by stock cutaways (placements or overlays: ``start`` + ``duration``)."""
    windows: List[Tuple[float, float]] = []
    for overlay in overlays or []:
        try:
            start = max(0.0, float(overlay["start"]))
            end = start + float(overlay["duration"])
        except (KeyError, TypeError, ValueError):
            continue
        if end > start:
            windows.append((round(start, 3), round(end, 3)))
    return sorted(windows)


def clamp_fx_plan(plan: "FxPlan", duration: float) -> "FxPlan":
    """The plan cut to the probed stage-1 length (AR-M13): spans clipped, cues past the end dropped."""
    if not duration or duration <= 0 or duration >= float(plan.edit_duration) - 1e-6:
        return plan
    from .edit_fx.contracts import INSTANT_KINDS

    cues = []
    for cue in plan.cues:
        if cue.kind in INSTANT_KINDS:
            if cue.start < duration - 1e-6:
                cues.append(cue)
            continue
        if cue.start >= duration - FX_MIN_SPAN_SECONDS:
            continue
        cues.append(cue if cue.end <= duration else cue.model_copy(update={"end": round(duration, 3)}))
    return plan.model_copy(update={"cues": tuple(cues), "edit_duration": float(duration)})


def fx_caption_words(
    words: List[Dict[str, Any]], highlight_words: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """Caption words for fx-core: ``{id, start, end, text, highlight}``.

    Emphasis follows the legacy captions (power words, numbers) unless the caption edit names the highlighted
    words itself.
    """
    out = [dict(word) for word in words or [] if isinstance(word, dict)]
    if highlight_words:
        wanted = {normalize_token(str(text)) for text in highlight_words if str(text).strip()}
        for word in out:
            word["highlight"] = normalize_token(str(word.get("text", ""))) in wanted
        return out
    if any("highlight" in word for word in out):
        return out
    try:
        _, emphasis = annotate_caption_words(out, enable_emoji=False)
    except Exception as exc:  # noqa: BLE001 - emphasis is decorative
        logger.debug("Caption emphasis unavailable for FX captions: %s", exc)
        emphasis = set()
    for index, word in enumerate(out):
        word["highlight"] = index in emphasis
    return out


def _hex_luma(value: Optional[str]) -> Optional[float]:
    text = str(value or "").strip().lstrip("#")
    if len(text) < 6:
        return None
    try:
        r, g, b = (int(text[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return None
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def fx_font_color(template: Dict[str, Any], font_color: Optional[str]) -> Optional[str]:
    """The task colour for FX captions. Templates with dark text on a light card keep their dark text when the
    task colour is the create default (white): white on a white card would be unreadable."""
    if not font_color:
        return None
    template_luma = _hex_luma(template.get("font_color"))
    if (
        template_luma is not None
        and template_luma < 0.35
        and template.get("background")
        and str(font_color).strip().upper() in ("#FFFFFF", "#FFF", "WHITE")
    ):
        return None
    return font_color


def fx_caption_y(
    template: Dict[str, Any],
    *,
    font_size: Optional[int],
    width: int,
    height: int,
    position_y: Optional[float],
    avoid_band: Optional[Tuple[float, float]],
) -> float:
    """The caption centre the legacy ASS would use (safe-area clamp, moved clear of burned-in captions)."""
    from .media.captions import caption_center_clear_of_band

    base_size = int(font_size) if font_size else int(template.get("font_size") or 32)
    font_px = get_scaled_font_size(base_size, width)
    text_height = int(font_px * 1.5)
    pos = float(position_y) if position_y is not None else float(template.get("position_y", 0.80))
    y_pos = get_safe_vertical_position(height, text_height, pos)
    if avoid_band is not None:
        y_pos = caption_center_clear_of_band(y_pos, text_height, avoid_band, height)
    return max(0.0, min(1.0, y_pos / height))


def _av_mismatch(path: Path) -> bool:
    """True when the video and audio streams of ``path`` differ by more than a frame."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        streams = json.loads(result.stdout or "{}").get("streams") or []
    except Exception as exc:  # noqa: BLE001 - an unreadable probe is not a mismatch; the chain checked frames
        logger.warning("fx_av_check_unavailable (%s)", exc)
        return False
    durations: Dict[str, float] = {}
    for stream in streams:
        try:
            durations.setdefault(stream.get("codec_type"), float(stream.get("duration")))
        except (TypeError, ValueError):
            continue
    if "video" not in durations or "audio" not in durations:
        return False
    return abs(durations["video"] - durations["audio"]) > FX_AV_TOLERANCE_SECONDS


# Vertical layouts made of separate panels (reactor over source, call tiles, two speakers at once): no frame zooms.
FX_PANEL_LAYOUTS = frozenset({"reaction", "stack", "overlap"})


def _interview_hook_kwargs(plan_report: Dict[str, Any]) -> Dict[str, Any]:
    """Where a stacked interview opening puts the libass hook title: on the panel seam. (Dynamic edits draw their
    title with fx-core, which places it itself; this is the classic render and the fallback levels.)"""
    if plan_report.get("hook_center_y") is None:
        return {}
    return {"hook_center_y": plan_report["hook_center_y"]}


# Intensity-look captions on an interview clip sit on the stacked seam on two-shots, at mid-chest on close-ups and
# below the picture on fitted shots (per-shot rows, edit_fx.caption_rows). A caption edit's own position pins the
# captions instead (no rows).
INTERVIEW_LOOK_CAPTION_Y = 0.5


def _without_warp(plan: "FxPlan") -> "FxPlan":
    return plan.model_copy(update={"cues": tuple(c for c in plan.cues if c.kind not in ("speed", "freeze"))})


def _capture_fx_event(event: str, properties: Dict[str, Any]) -> None:
    """PostHog ``fx_render`` / ``fx_render_failed`` (no content). Never raises."""
    try:
        from .observability import get_posthog_client, get_trace_id, get_user_id

        client = get_posthog_client()
        if client is None:
            return
        props = dict(properties)
        trace_id = get_trace_id()
        if trace_id and trace_id != "-":
            props["trace_id"] = trace_id
        user_id = get_user_id()
        if not user_id:
            props["$process_person_profile"] = False
        client.capture(event, distinct_id=str(user_id or "supoclip-worker"), properties=props)
    except Exception as exc:  # noqa: BLE001 - analytics never breaks a render
        logger.debug("Could not capture %s: %s", event, exc)


def _fx_stage(error: Optional[str]) -> str:
    return (error or "unknown").split(":", 1)[0].strip()[:20] or "unknown"


@dataclass
class _FxChainFailure:
    """Stands in for an ``FxResult`` when the chain could not even start (ladder bookkeeping only)."""
    error: str
    ok: bool = False
    applied_warp: List[Dict[str, Any]] = field(default_factory=list)
    timings: Dict[str, float] = field(default_factory=dict)
    face_track: Optional[Dict[str, Any]] = None
    library_fingerprint: str = ""


def _fx_ladder_setup(
    fx: "FxRenderInput",
    *,
    source_clip_path: Path,
    effective_keep_ranges: List[Tuple[float, float]],
    output_offsets: Optional[List[float]],
    broll_overlays: Optional[List[Dict[str, Any]]],
    kenburns_eligible: bool,
    fx_template: bool,
    template: Dict[str, Any],
    layout_kind: Optional[str] = None,
    panel_windows: "tuple[tuple[float, float], ...] | list" = (),
    interview_segments: "list[dict] | tuple" = (),
    src_size: Optional[Tuple[int, int]] = None,
    caption_y_pinned: bool = False,
    caption_template: str,
    add_subtitles: bool,
    hook_title: Optional[str],
    font_family: Optional[str],
    font_size: Optional[int],
    font_color: Optional[str],
    width: int,
    height: int,
    caption_y: Optional[float],
    source_caption_band: Optional[Tuple[float, float]],
    caption_words: Optional[List[Dict[str, Any]]],
    highlight_words: Optional[List[str]],
    report: Dict[str, Any],
):
    """The render inputs shared by the full and safe levels: the plan clamped to the probed stage-1 length (with
    baseline pushes where Ken Burns would have been), bleep spans, the fx-core caption style, the caption words and
    the hook band."""
    from .edit_fx.baseline import baseline_pushes
    from .edit_fx.caption_style import fx_caption_style
    from .edit_fx.contracts import caption_look_for
    from .media.common import HOOK_TITLE_MIN_SECONDS, hook_title_window

    plan = clamp_fx_plan(fx.plan, ffprobe_duration(source_clip_path))
    duration = float(plan.edit_duration)
    cuts, _ = fx_junctions(effective_keep_ranges, output_offsets)
    all_broll_windows = fx_broll_windows(broll_overlays)
    if layout_kind in FX_PANEL_LAYOUTS:
        # A zoom of the whole frame would cross the seam between stacked panels: keep the planned reframes out.
        plan = plan.model_copy(update={"cues": tuple(
            c for c in plan.cues if not (c.kind == "zoom" and c.origin == "auto"))})
    elif panel_windows:
        # An interview stacks only its two-shots: reframes stay on the single-person shots, and a word behind the
        # speaker would be cut by the panel edge there.
        def on_panels(c) -> bool:
            return any(c.start < end and start < max(c.end, c.start + 1e-3) for start, end in panel_windows)

        plan = plan.model_copy(update={"cues": tuple(
            c for c in plan.cues
            if not (on_panels(c) and ((c.kind == "zoom" and c.origin == "auto")
                                      or (c.kind == "accent" and c.variant == "behind")))
        )})
    # Slow baseline pushes only where the references use them: style-only and subtle (spec §3.5).
    if kenburns_eligible and plan.style.intensity in ("off", "subtle") and (plan.style.zooms or not plan.style.active):
        plan = baseline_pushes(plan, cuts=cuts, broll_windows=all_broll_windows)

    bleeps = (
        [(float(c.start), float(c.end)) for c in plan.cues if c.kind == "bleep" and c.end > c.start]
        if plan.timing == "word"
        else []
    )
    caption_style = None
    look = caption_look_for(caption_template, plan.style.intensity, plan.sensitivity)
    if fx_template and add_subtitles and look != "template":
        # Intensity looks own font, size and position; a layout's caption row (a stacked layout's seam) wins over
        # the look's default, and burned-in source captions still push it clear.
        from .edit_fx.caption_style import LOOKS, look_caption_style, look_size_px
        from .media.captions import caption_center_clear_of_band

        y = float(caption_y) if caption_y is not None else float(LOOKS[look]["y"])
        rows: tuple = ()
        if (layout_kind == "interview" or (layout_kind == "compositor" and interview_segments)) and not caption_y_pinned:
            # A row per shot, where the references put captions: on the seam of a stacked two-shot, at mid-chest on
            # a close-up, below the picture of a fitted graphic; never on the lower person's face. The row changes
            # at the first caption group after a cut (fx-core). A caption edit's own position pins them instead.
            from .edit_fx.caption_rows import interview_caption_rows

            y = INTERVIEW_LOOK_CAPTION_Y
            rows = interview_caption_rows(
                interview_segments, text_px=look_size_px(look, width), width=width, height=height,
                source_size=src_size, band=source_caption_band, duration=duration,
            )
        if source_caption_band is not None:
            text_height = int(look_size_px(look, width) * 1.5)
            y = caption_center_clear_of_band(y * height, text_height, source_caption_band, height) / height
        caption_style = look_caption_style(
            caption_template, look, width=width, height=height, caption_y=max(0.0, min(1.0, y)),
            font_color=fx_font_color(template, font_color), rows=rows,
        )
        if rows:
            report["caption_rows"] = [row.model_dump(mode="json") for row in rows]
        report["caption_y"] = caption_style.y
    elif fx_template and add_subtitles:
        caption_style = fx_caption_style(
            template,
            template_id=caption_template,
            font_family=font_family,
            font_size=font_size,
            font_color=fx_font_color(template, font_color),
            width=width,
            height=height,
            caption_y=fx_caption_y(
                template, font_size=font_size, width=width, height=height,
                position_y=caption_y, avoid_band=source_caption_band,
            ),
            enabled=True,
        )
        # Where fx-core draws the captions: the studio and caption edits read it back from the sidecar.
        report["caption_y"] = caption_style.y
    words = fx_caption_words(list(caption_words) if caption_words is not None else list(fx.words), highlight_words)
    hook_window = None
    if hook_title:
        # == build_hook_title_ass: a very short clip shows the title throughout.
        hook_window = ((0.0, max(0.5, duration)) if duration <= HOOK_TITLE_MIN_SECONDS
                       else (FX_HOOK_WINDOW_START, hook_title_window(duration)))
    return plan, duration, bleeps, caption_style, words, hook_window


def _fx_hook_title(hook_title: str, *, plan: "FxPlan", caption_template: str, template: Dict[str, Any],
                   caption_style, font_family: Optional[str], window):
    """The hook title fx-core draws for a generated clip (``edit_fx.hook_title``)."""
    from .edit_fx.contracts import is_listed_fx_template
    from .edit_fx.hook_title import build_hook, hook_accent, hook_look

    look = hook_look(caption_template, plan.style.intensity, plan.sensitivity)
    return build_hook(
        hook_title, template_id=caption_template, look=look,
        # The listed styles' captions use the look's own font, whatever the task's font.
        font=None if is_listed_fx_template(caption_template) else (font_family or template.get("font_family")),
        accent=hook_accent(caption_template, look, caption_style, template),
        window=window, uppercase=bool(template.get("uppercase")),
    )


def _render_fx_ladder(
    fx: "FxRenderInput",
    *,
    source_clip_path: Path,
    final_clip_path: Path,
    reframe_format: str,
    width: int,
    height: int,
    src_size: Tuple[int, int],
    effective_keep_ranges: List[Tuple[float, float]],
    output_offsets: Optional[List[float]],
    add_subtitles: bool,
    hook_title: Optional[str],
    font_family: Optional[str],
    font_size: Optional[int],
    font_color: Optional[str],
    caption_template: str,
    caption_y: Optional[float],
    source_caption_band: Optional[Tuple[float, float]],
    caption_words: Optional[List[Dict[str, Any]]],
    highlight_words: Optional[List[str]],
    speaker_turns: List[Dict[str, Any]],
    caption_y_pinned: bool = False,
    speaker_words: List[Dict[str, Any]],
    vertical_plan: Optional[Tuple[str, str]],
    plan_report: Dict[str, Any],
    broll_overlays: Optional[List[Dict[str, Any]]],
    layout_report: Optional[Dict[str, Any]],
    build_ass,
) -> bool:
    """Render one FX clip down the fallback ladder (FINAL §6.6). Returns whether ``final_clip_path`` was written.

    1. full (fx chain with fx-core; skipped when ``FX_RUNTIME=off``), then full without B-roll after a P1
       failure with cutaways, and full with an identity warp when a warped render's A/V disagree;
    2. safe: P1 → P3 with the ASS fallback captions, the original hook title and the audio FX;
    3. none: the legacy render, with Ken Burns restored and the original hook title.
    """
    from .edit_fx import env as fx_env
    from .edit_fx.contracts import is_fx_template
    from .edit_fx.warp import build_warp
    from .media.fx_chain import FxRequest

    started = time.monotonic()
    report: Dict[str, Any] = layout_report if layout_report is not None else {}
    template = get_template(caption_template)
    fx_template = is_fx_template(caption_template)
    fallback_template = get_template(template.get("ass_fallback") or "default") if fx_template else None
    ass_fonts = ass_fonts_dir(font_family or get_template(caption_template)["font_family"])
    fallback_fonts = (
        ass_fonts_dir(font_family or fallback_template["font_family"]) if fallback_template else ass_fonts
    )

    plan = fx.plan
    duration = float(plan.edit_duration)
    kenburns_eligible = bool(plan_report.get("kenburns_eligible"))
    report["kenburns_eligible"] = kenburns_eligible
    bleeps: List[Tuple[float, float]] = []
    caption_style = None
    words: List[Dict[str, Any]] = []
    hook_window = None
    setup_error: Optional[str] = None
    try:
        plan, duration, bleeps, caption_style, words, hook_window = _fx_ladder_setup(
            fx,
            source_clip_path=source_clip_path,
            effective_keep_ranges=effective_keep_ranges,
            output_offsets=output_offsets,
            broll_overlays=broll_overlays,
            kenburns_eligible=kenburns_eligible,
            fx_template=fx_template,
            template=template,
            layout_kind=plan_report.get("kind"),
            panel_windows=plan_report.get("panel_windows") or (),
            interview_segments=plan_report.get("interview_segments") or (),
            src_size=src_size,
            caption_y_pinned=caption_y_pinned,
            caption_template=caption_template,
            add_subtitles=add_subtitles,
            hook_title=hook_title,
            font_family=font_family,
            font_size=font_size,
            font_color=font_color,
            width=width,
            height=height,
            caption_y=caption_y,
            source_caption_band=source_caption_band,
            caption_words=caption_words,
            highlight_words=highlight_words,
            report=report,
        )
    except Exception as exc:  # noqa: BLE001 - decorative layers never cost the clip: straight to the legacy render
        setup_error = f"setup: {exc}"
        logger.warning("fx_render_failed level=full error=%s", setup_error[:500])
        _capture_fx_event("fx_render_failed", {
            "stage": "setup", "level": "full", "error": setup_error[:300],
            "template": caption_template, "intensity": plan.style.intensity,
        })

    drawn_hook = None   # the title fx-core draws at level full (None: the ASS layer burns it, or there is none)

    def request(current_plan, overlays) -> "FxRequest":
        return FxRequest(
            plan=current_plan,
            words=words,
            caption=caption_style,
            face_track=fx.face_track,
            reaction_files=dict(fx.reaction_files or {}),
            broll_windows=tuple(fx_broll_windows(overlays)),
            hook_window=hook_window,
            hook=drawn_hook,
            loudness=fx_env.fx_loudnorm_mode(),
        )

    def chain(mode: str, current_plan, overlays, ass: Optional[Path], fonts: Optional[Path]):
        options: Dict[str, Any] = {"mode": mode}
        ok = render_reframed_clip_ffmpeg(
            source_clip_path,
            final_clip_path,
            reframe_format,
            subtitle_ass_path=ass,
            fonts_dir=fonts if ass else None,
            speaker_turns=speaker_turns,
            speaker_words=speaker_words,
            vertical_plan=vertical_plan,
            broll_overlays=overlays,
            fx_request=request(current_plan, overlays),
            fx_report=options,
        )[0]
        return ok, options.get("result")

    def downgrade(stage_from: str, result) -> None:
        error = getattr(result, "error", None) or "render failed"
        logger.warning("fx_render_failed level=%s error=%s", stage_from, error[:500])
        _capture_fx_event("fx_render_failed", {
            "stage": _fx_stage(error), "level": stage_from, "error": error[:300],
            "template": caption_template, "intensity": plan.style.intensity,
        })

    level: Optional[str] = None
    result = None
    applied_warp: List[Dict[str, Any]] = []
    broll_applied = False
    overlays = broll_overlays or None

    # 1. full
    if setup_error is None and fx_env.fx_runtime() != "off":
        try:
            warp = build_warp(plan.cues if fx_env.fx_warp_enabled() else (), duration)
            # A plan that only adds sound needs no drawing stage (P1 → P3 renders all of it), unless the clip has a
            # title: fx-core draws that, so every Dynamic edits clip gets the same title whatever else it has.
            mode = "full" if plan.draws or (hook_title and not plan.hook_title_suppressed) else "safe"
            # With the drawing stage, fx-core draws the title and the ASS layer carries none; without it libass burns the title.
            fx_hook = mode == "full" and bool(hook_title) and not plan.hook_title_suppressed
            ass_hook = None if (fx_hook or plan.hook_title_suppressed) else hook_title
            full_ass = build_ass(
                add_subtitles and not fx_template,
                ass_hook,
                time_map=warp.to_output if warp.nontrivial else None,
                censor_spans=bleeps,
            )
            if fx_hook:
                drawn_hook = _fx_hook_title(
                    hook_title, plan=plan, caption_template=caption_template, template=template,
                    caption_style=caption_style, font_family=font_family, window=hook_window,
                )
            ok, result = chain(mode, plan, overlays, full_ass, ass_fonts)
            if not ok and overlays and _fx_stage(getattr(result, "error", None)) == "p1":
                downgrade("full", result)
                overlays = None
                ok, result = chain(mode, plan, None, full_ass, ass_fonts)
            if ok and result is not None and result.applied_warp and _av_mismatch(final_clip_path):
                logger.warning("fx_av_mismatch: retrying with an identity warp")
                _capture_fx_event("fx_render_failed", {
                    "stage": "av_check", "level": "full", "error": "audio and video lengths differ",
                    "template": caption_template, "intensity": plan.style.intensity,
                })
                identity_ass = build_ass(add_subtitles and not fx_template, ass_hook, censor_spans=bleeps)
                ok, result = chain(mode, _without_warp(plan), overlays, identity_ass, ass_fonts)
        except Exception as exc:  # noqa: BLE001 - an unexpected chain error drops a level, never the clip
            ok, result = False, _FxChainFailure(error=f"setup: {exc}")
        if ok:
            level = "full"
            applied_warp = list(result.applied_warp) if result is not None else []
            broll_applied = bool(overlays)
        elif _fx_stage(getattr(result, "error", None)) == "cancelled":
            return False
        else:
            downgrade("full", result)
    overlays = broll_overlays or None

    drawn_hook = None
    # 2. safe: no deno, no warp, no picture FX; the ASS fallback captions, the original hook title and the audio FX.
    if level is None and setup_error is None:
        try:
            safe_ass = build_ass(add_subtitles, hook_title, template_override=fallback_template, censor_spans=bleeps)
            ok, result = chain("safe", plan, overlays, safe_ass, fallback_fonts)
            if not ok and overlays and _fx_stage(getattr(result, "error", None)) == "p1":
                downgrade("safe", result)
                overlays = None
                ok, result = chain("safe", plan, None, safe_ass, fallback_fonts)
        except Exception as exc:  # noqa: BLE001
            ok, result = False, _FxChainFailure(error=f"setup: {exc}")
        if ok:
            level = "safe"
            broll_applied = bool(overlays)
        else:
            downgrade("safe", result)
            result = None

    # 3. none: the legacy render exactly as today, Ken Burns included.
    if level is None:
        legacy_plan = vertical_plan
        if reframe_format == "vertical" and vertical_plan is not None:
            try:
                rebuilt_report: Dict[str, Any] = {}
                video_filter, legacy_mode, _ = build_vertical_filter_plan(
                    source_clip_path, src_size[0], src_size[1], speaker_turns, speaker_words,
                    report=rebuilt_report,
                )
                legacy_plan = (video_filter, legacy_mode)
                if "vertical_layout" in report:
                    report["vertical_layout"] = {
                        **report["vertical_layout"],
                        "kind": rebuilt_report.get("kind") or report["vertical_layout"].get("kind"),
                        "video_filter": video_filter,
                        "mode": legacy_mode,
                    }
            except Exception as exc:  # noqa: BLE001 - keep the FX-path plan (no Ken Burns) if replanning fails
                logger.warning("Could not rebuild the Ken Burns layout for the legacy render: %s", exc)
        legacy_ass = build_ass(add_subtitles, hook_title, template_override=fallback_template)

        def render(current_overlays):
            return render_reframed_clip_ffmpeg(
                source_clip_path,
                final_clip_path,
                reframe_format,
                subtitle_ass_path=legacy_ass,
                fonts_dir=fallback_fonts if legacy_ass else None,
                speaker_turns=speaker_turns,
                speaker_words=speaker_words,
                vertical_plan=legacy_plan,
                broll_overlays=current_overlays,
            )[0]

        ok = render(broll_overlays)
        broll_applied = bool(broll_overlays) and ok
        if not ok and broll_overlays:
            logger.warning("Render with B-roll failed; rendering the clip without it")
            ok = render(None)
        if not ok:
            return False
        level = "none"

    report["broll_applied"] = broll_applied
    report["fx_level"] = level
    report["applied_warp"] = applied_warp if level == "full" else []
    report["face_track"] = result.face_track if (level == "full" and result is not None) else fx.face_track
    report["fx_plan"] = plan
    timings = dict(getattr(result, "timings", None) or {})
    _capture_fx_event("fx_render", {
        "intensity": plan.style.intensity,
        "template": caption_template,
        "fx_level": level,
        "cues": len(plan.cues),
        "warp": bool(report["applied_warp"]),
        "duration_ms": round((time.monotonic() - started) * 1000.0, 1),
        "p1_ms": timings.get("p1"),
        "p2_ms": timings.get("p2"),
        "p3_ms": timings.get("p3"),
        "analysis_ms": timings.get("analysis"),
        "library_fingerprint": getattr(result, "library_fingerprint", "") or "",
    })
    logger.info("fx_render level=%s cues=%d warp=%s", level, len(plan.cues), bool(report["applied_warp"]))
    return True


def _segment_fx_input(segment: Dict[str, Any]) -> Optional["FxRenderInput"]:
    """The ready plan ``create_video_clips`` stored on a segment, or None (legacy render). Lenient read."""
    raw = segment.get("edit_fx")
    if not raw:
        return None
    from .edit_fx.compat import known_fields, log_dropped
    from .edit_fx.contracts import FxPlan

    try:
        if isinstance(raw, FxPlan):
            plan = raw
        else:
            clean, dropped = known_fields(FxPlan, dict(raw))
            log_dropped("batch.edit_fx", dropped)
            plan = FxPlan.model_validate(clean)
    except Exception as exc:  # noqa: BLE001 - an unreadable plan renders the clip without FX
        logger.warning("fx_segment_plan_unreadable (%s); rendering without effects", exc)
        return None
    files = {
        str(cue_id): Path(path)
        for cue_id, path in (segment.get("fx_reaction_files") or {}).items()
        if path
    }
    return FxRenderInput(
        plan=plan,
        words=list(segment.get("fx_words") or []),
        reaction_files=files,
        face_track=segment.get("face_track") or None,
    )


def save_fx_render(
    clip_path: Path,
    *,
    style: Dict[str, Any],
    beats: Optional[Dict[str, Any]],
    report: Dict[str, Any],
    template: str,
) -> Dict[str, Any]:
    """Write the FX sidecar keys of a rendered clip and return its ``generated_clips.fx_meta``.

    The stored plan is the plan as rendered (clamped, with baseline pushes) and ``edit_fx_warp`` the warp the mp4
    actually carries (AI-H4). ``vertical_layout`` is recorded too, so the studio replays the clip's framing.
    """
    from .clip_source_map import save_clip_edit_fx

    plan = report.get("fx_plan")
    level = report.get("fx_level") or "none"
    plan_json = plan.model_dump(mode="json") if hasattr(plan, "model_dump") else dict(plan or {})
    save_clip_edit_fx(
        clip_path,
        style=dict(style or {}),
        beats=beats,
        plan=plan_json,
        face_track=report.get("face_track"),
        fx_level=level,
        applied_warp=list(report.get("applied_warp") or []),
    )
    save_clip_vertical_layout(clip_path, report.get("vertical_layout"))
    return {
        "fx_level": level,
        "timing": plan_json.get("timing") or "none",
        "intensity": (plan_json.get("style") or {}).get("intensity") or "off",
        "template": template,
    }


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
    max_clip_seconds: float = 60.0,
    fx_enabled: bool = False,
) -> List[Dict[str, Any]]:
    """Create optimized video clips from segments with template support.

    ``fx_enabled`` (dynamic edits re-render): a segment carrying ``edit_fx`` (a plan
    ``VideoService.create_video_clips`` already resolved, with ``fx_words``,
    ``fx_reaction_files`` and an optional ``face_track``) renders through the fx
    chain and records its FX sidecar keys. This function never plans: no
    resolver, no beat model.
    """
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

            start_seconds, end_seconds = segment_bounds(segment)

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

            # A planned (workflow) clip carries its own cap; highlights use the preset's.
            resolved = resolve_render_ranges(
                video_path,
                segment,
                cleanup_settings,
                float(segment.get("max_seconds") or max_clip_seconds),
            )
            keep_ranges = resolved.keep_ranges
            output_offsets = resolved.output_offsets
            clip_text = resolved.text
            start_seconds, end_seconds = resolved.bounds

            layout_report: Dict[str, Any] = {}
            point_labels = point_labels_for(segment.get("workflow_meta"), resolved.group_output_starts)
            fx_input = _segment_fx_input(segment) if fx_enabled else None
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
                hook_title=burned_hook_title(segment),
                extend_to_sentence=False,
                layout_report=layout_report,
                broll_overlays=segment.get("broll_overlays"),
                **({"output_offsets": output_offsets} if output_offsets is not None else {}),
                **({"point_labels": point_labels} if point_labels else {}),
                **({"fx": fx_input} if fx_input is not None else {}),
            )

            if success:
                from .media.ffmpeg import output_offsets_for_ranges
                save_clip_source_ranges(
                    clip_path, keep_ranges, output_offsets or output_offsets_for_ranges(keep_ranges)
                )
                save_clip_broll(
                    clip_path,
                    segment.get("broll") if layout_report.get("broll_applied") else [],
                )
                save_clip_vertical_layout(clip_path, layout_report.get("vertical_layout"))
                if layout_report.get("caption_y") is not None:
                    save_clip_caption_settings(
                        clip_path, {"position_y": layout_report["caption_y"], "position_is_center": True}
                    )
                fx_meta = None
                if fx_input is not None:
                    fx_meta = save_fx_render(
                        clip_path,
                        style=segment.get("edit_style") or {},
                        beats=segment.get("edit_beats"),
                        report=layout_report,
                        template=caption_template,
                    )
                elif fx_enabled:
                    # Re-rendered legacy (explicit off + legacy template): the new sidecar carries no FX keys.
                    from .clip_source_map import clear_clip_edit_fx

                    clear_clip_edit_fx(clip_path)
                # Crossfades overlap neighbouring ranges, so the file is
                # shorter than the kept source; store what actually plays.
                cleaned_duration = rendered_clip_duration(clip_path, keep_ranges, output_offsets)
                clip_info = {
                    "clip_id": i + 1,
                    "filename": clip_filename,
                    "path": str(clip_path),
                    "start_time": segment["start_time"],
                    "end_time": segment["end_time"],
                    "duration": cleaned_duration,
                    "text": clip_text,
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
                    "output_offsets": output_offsets,
                    "group_output_starts": resolved.group_output_starts,
                }
                if fx_meta is not None:
                    clip_info["fx_meta"] = fx_meta
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
    max_clip_seconds: float = 60.0,
    fx_enabled: bool = False,
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
        max_clip_seconds=max_clip_seconds,
        **({"fx_enabled": True} if fx_enabled else {}),
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
