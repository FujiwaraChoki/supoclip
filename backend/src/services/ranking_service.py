"""
Ranking service - orchestrates the Ranking/Compilation tool's pipeline:
N independent input videos -> one rendered compilation, the inverse
cardinality of clipping's one-source -> N-clips pipeline (see
TaskService/VideoService for that side). Reuses the same final-encode
primitives (build_final_video_encode_args, build_audio_output_args,
enforce_size_cap) and the same GPU resource-slot convention as clipping, but
the concatenation-of-independent-files step itself is new — nothing in
video_utils.py/clip_editor.py stitches more than one source together.
"""

from __future__ import annotations

import json
import logging
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..clip_editor import EXPORT_PRESETS
from ..config import get_config
from ..ranking_overlay import (
    build_rank_list_ass,
    build_rank_number_ass,
    build_ranking_hook_ass,
    build_ranking_overlay_ass,
)
from ..ranking_templates import get_template
from ..repositories.clip_repository import ClipRepository
from ..repositories.ranking_folder_repository import RankingFolderRepository
from ..repositories.ranking_repository import RankingRepository
from ..repositories.task_repository import TaskRepository
from ..utils.async_helpers import run_in_thread
from ..video_utils import (
    ass_fonts_dir,
    build_audio_output_args,
    build_final_video_encode_args,
    enforce_size_cap,
    ffmpeg_escape_filter_path,
    ffmpeg_escape_filter_value,
    ffprobe_duration,
    ffprobe_has_audio,
    find_music_path,
    find_sfx_path,
    OUTPUT_FPS,
    round_to_even,
    run_ffmpeg_command,
    seconds_to_mmss,
)
from ..workers.resource_locks import resource_slot

logger = logging.getLogger(__name__)

# A duration variance beyond this fraction of the longest clip surfaces a
# progress-message notice — per spec, "not an error, just a notice".
_DURATION_VARIANCE_NOTICE_THRESHOLD = 0.6


def _resolve_local_path(file_path: str) -> Path:
    from .video_service import VideoService

    return VideoService.resolve_local_video_path(file_path)


class RankingService:
    """Service for ranking/compilation task orchestration."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.task_repo = TaskRepository()
        self.ranking_repo = RankingRepository()
        self.clip_repo = ClipRepository()

    async def process_ranking_complete(
        self,
        task_id: str,
        progress_callback=None,
    ) -> Dict[str, Any]:
        """Full pipeline: load -> order -> render -> export. Mirrors
        TaskService.process_task's progress-callback shape so the existing
        SSE/frontend stage machinery works unchanged."""

        async def report(percent: int, message: str, stage: str, status: str = "processing"):
            if progress_callback:
                await progress_callback(percent, message, status, stage=stage)

        await report(2, "Loading input videos", "load")
        task = await self.task_repo.get_task_by_id(self.db, task_id)
        if not task:
            raise ValueError(f"Ranking task {task_id} not found")

        inputs = await self.ranking_repo.list_inputs(self.db, task_id)
        if len(inputs) < 2:
            raise ValueError("A ranking compilation needs at least 2 input videos")

        durations = [float(i["duration_seconds"] or 0.0) for i in inputs]
        longest = max(durations) if durations else 0.0
        shortest = min(durations) if durations else 0.0
        if longest > 0 and (longest - shortest) / longest > _DURATION_VARIANCE_NOTICE_THRESHOLD:
            await report(
                8,
                "Note: input clip durations vary widely — the compilation will still render.",
                "load",
            )

        ranking_settings = json.loads(task.get("ranking_settings") or "{}")
        template_id = ranking_settings.get("template_id", "rapid_fire")
        template = get_template(template_id)
        number_overlay = {**template["number_overlay"], **(ranking_settings.get("number_overlay") or {})}
        export_preset_name = ranking_settings.get("export_preset", "tiktok")
        preset = EXPORT_PRESETS.get(export_preset_name, EXPORT_PRESETS["tiktok"])
        target_lufs = ranking_settings.get("target_lufs", preset.target_lufs)
        hook_white = ranking_settings.get("hook_white")
        hook_red = ranking_settings.get("hook_red")

        if number_overlay.get("style") == "stacked":
            # "Each rank must have text. Empty text blocks export" — checked
            # again here (not just at the API layer) so a task enqueued
            # before text was finished can't silently render blank ranks.
            missing = [
                i.get("original_filename", "?")
                for i in inputs
                if not (i.get("rank_text") or "").strip()
            ]
            if missing:
                raise ValueError(
                    "Every rank needs text before rendering this template "
                    f"(missing: {', '.join(missing)})"
                )

        await report(20, "Ordering clips", "order")
        ordered = self._resolve_ranks(inputs)
        # display_rank is resolved from the ascending (#1-first) order above;
        # a "descending" template (Countdown, Ranking List) only flips the
        # *playback* sequence to worst-first, the numbers themselves don't
        # change.
        if template.get("render_order") == "descending":
            ordered = list(reversed(ordered))
        list_overlay = bool(template.get("list_overlay"))
        transition_sfx = template.get("transition_sfx")
        background_music = template.get("background_music")
        background_music_volume = template.get("background_music_volume", 0.22)
        use_global_sfx = bool(template.get("use_global_sfx"))

        await report(30, "Rendering compilation", "render")
        config = get_config()
        output_dir = Path(config.temp_dir) / "clips"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"ranking_{task_id}_{uuid.uuid4().hex[:8]}.mp4"
        use_gpu = config.gpu_acceleration_enabled

        async with resource_slot("gpu", 1):
            ok = await run_in_thread(
                self._render_compilation,
                ordered,
                preset.width,
                preset.height,
                number_overlay,
                use_gpu,
                target_lufs,
                output_path,
                list_overlay,
                transition_sfx,
                background_music,
                background_music_volume,
                use_global_sfx,
                config.ranking_default_framing,
                hook_white,
                hook_red,
            )
        if not ok:
            raise RuntimeError("Ranking compilation render failed")

        # Prefer-unused tracking + text memory: a folder clip that was
        # actually used in this rendered ranking gets its use_count bumped
        # and its saved_text refreshed, so the *next* selection from that
        # folder prefers other clips and pre-fills this one's text if it
        # comes up again. Direct-upload inputs (no folder_clip_id) skip this
        # — there's no library row to update.
        folder_clip_ids = [i["folder_clip_id"] for i in inputs if i.get("folder_clip_id")]
        if folder_clip_ids:
            await RankingFolderRepository.mark_used(self.db, folder_clip_ids)
            for item in inputs:
                if item.get("folder_clip_id") and (item.get("rank_text") or "").strip():
                    await RankingFolderRepository.save_text(
                        self.db, item["folder_clip_id"], item["rank_text"].strip()
                    )

        await report(85, "Applying export settings", "export")
        enforce_size_cap(output_path)
        duration = ffprobe_duration(output_path)

        clip_id = await self.clip_repo.create_clip(
            self.db,
            task_id=task_id,
            filename=output_path.name,
            file_path=str(output_path),
            start_time="00:00",
            end_time=seconds_to_mmss(duration),
            duration=duration,
            text=None,
            relevance_score=0.0,
            reasoning="Ranking compilation",
            clip_order=0,
        )
        await self.task_repo.update_task_clips(self.db, task_id, [clip_id])
        await self.task_repo.update_task_status(
            self.db, task_id, "completed", progress=100, progress_message="Compilation ready"
        )
        await report(100, "Compilation ready", "complete", status="completed")

        return {"task_id": task_id, "clip_id": clip_id, "duration": duration}

    @staticmethod
    def _resolve_ranks(inputs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Inputs are already ordered by COALESCE(rank_position, order_index)
        (RankingRepository.list_inputs); this just resolves the *displayed*
        rank digit for each — the manual override if set, else its sequential
        position (auto-numbered top = #1)."""
        resolved = []
        for index, item in enumerate(inputs):
            rank = item["rank_position"] if item["rank_position"] is not None else index + 1
            resolved.append({**item, "display_rank": rank})
        return resolved

    @staticmethod
    def _framing_filter(
        idx: int, mode: str, target_width: int, target_height: int
    ) -> List[str]:
        """Per-clip 9:16 framing. "blur_fill" (the tool default) preserves
        the whole source frame — a scaled/cropped, blurred copy of the same
        clip fills the rest — because these inputs are often unfocused,
        chaotically-framed footage (epic fails, chaotic action) where a
        center-crop ("crop_fill") would cut the actual subject out of frame.
        "letterbox" is the plain scale+pad fallback (the original, only,
        behavior before per-clip framing existed)."""
        if mode == "crop_fill":
            return [
                f"[{idx}:v]scale={target_width}:{target_height}:force_original_aspect_ratio=increase,"
                f"crop={target_width}:{target_height},setsar=1,fps={OUTPUT_FPS},format=yuv420p[v{idx}]"
            ]
        if mode == "blur_fill":
            return [
                f"[{idx}:v]split=2[bg{idx}][fg{idx}]",
                f"[bg{idx}]scale={target_width}:{target_height}:force_original_aspect_ratio=increase,"
                f"crop={target_width}:{target_height},gblur=sigma=25,eq=brightness=-0.08[bgblur{idx}]",
                f"[fg{idx}]scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
                f"setsar=1[fgscaled{idx}]",
                f"[bgblur{idx}][fgscaled{idx}]overlay=(W-w)/2:(H-h)/2:format=auto,"
                f"fps={OUTPUT_FPS},format=yuv420p[v{idx}]",
            ]
        # letterbox
        return [
            f"[{idx}:v]scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
            f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
            f"fps={OUTPUT_FPS},format=yuv420p[v{idx}]"
        ]

    @staticmethod
    def _render_compilation(
        ordered_inputs: List[Dict[str, Any]],
        target_width: int,
        target_height: int,
        number_overlay: Dict[str, Any],
        use_gpu: bool,
        target_lufs: float,
        output_path: Path,
        list_overlay: bool = False,
        transition_sfx: Optional[str] = None,
        background_music: Optional[str] = None,
        background_music_volume: float = 0.22,
        use_global_sfx: bool = False,
        default_framing: str = "blur_fill",
        hook_white: Optional[str] = None,
        hook_red: Optional[str] = None,
    ) -> bool:
        target_width, target_height = round_to_even(target_width), round_to_even(target_height)

        local_paths = [_resolve_local_path(item["file_path"]) for item in ordered_inputs]
        durations = [ffprobe_duration(p) for p in local_paths]
        has_audio_flags = [ffprobe_has_audio(p) for p in local_paths]
        n = len(local_paths)

        filter_parts: List[str] = []
        v_labels: List[str] = []
        a_labels: List[str] = []
        for idx, item in enumerate(ordered_inputs):
            framing = item.get("framing") or default_framing
            filter_parts.extend(
                RankingService._framing_filter(idx, framing, target_width, target_height)
            )
            v_labels.append(f"[v{idx}]")
            if has_audio_flags[idx]:
                filter_parts.append(
                    f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,"
                    f"asetpts=PTS-STARTPTS[a{idx}]"
                )
            else:
                # Silent segments still need an audio stream so `concat`'s
                # a=1 output stays continuous across the whole compilation.
                filter_parts.append(
                    f"anullsrc=r=48000:cl=stereo:d={max(0.1, durations[idx]):.3f}[a{idx}]"
                )
            a_labels.append(f"[a{idx}]")

        concat_inputs = "".join(f"{v}{a}" for v, a in zip(v_labels, a_labels))
        filter_parts.append(f"{concat_inputs}concat=n={n}:v=1:a=1[vraw][araw]")

        # Rank-number overlay: one ASS style shared across every segment, one
        # dialogue event per segment timed to its slice of the concatenated
        # output (see ranking_overlay.py for the "digit in a square tile"
        # rendering, the DESIGN.md-sanctioned centered-text exception).
        stacked = number_overlay.get("style") == "stacked"
        style_line: Optional[str] = None
        list_style_line: Optional[str] = None
        events: List[str] = []
        styles: List[str] = []
        revealed_ranks: List[int] = []
        boundary_times: List[float] = []
        stacked_ranks: List[Dict[str, Any]] = []
        cumulative = 0.0
        for segment_index, (item, duration) in enumerate(zip(ordered_inputs, durations)):
            if segment_index > 0:
                # cumulative is this segment's start time == the cut point
                # between it and the previous segment — where transition SFX
                # (if the template sets one) lands.
                boundary_times.append(cumulative)
            if stacked:
                # All ranks stay on screen from t=0; only the reveal time of
                # each rank's *text* differs, so no per-segment tile event is
                # built here — build_ranking_overlay_ass builds everything
                # once, after this loop, from stacked_ranks.
                stacked_ranks.append(
                    {
                        "rank": item["display_rank"],
                        "text": item.get("rank_text") or "",
                        "reveal_time": cumulative,
                    }
                )
            else:
                style_line, segment_events = build_rank_number_ass(
                    item["display_rank"],
                    number_overlay,
                    target_width,
                    target_height,
                    cumulative,
                    duration,
                )
                events.extend(segment_events)
                revealed_ranks.append(item["display_rank"])
                if list_overlay:
                    list_style_line, list_events = build_rank_list_ass(
                        revealed_ranks,
                        number_overlay,
                        target_width,
                        target_height,
                        cumulative,
                        duration,
                    )
                    events.extend(list_events)
            cumulative += duration

        total_duration = cumulative
        image_overlays: List[Dict[str, Any]] = []
        if stacked:
            styles, events, rank_image_overlays = build_ranking_overlay_ass(
                stacked_ranks, target_width, target_height, total_duration
            )
            image_overlays.extend(rank_image_overlays)
        else:
            styles = [style_line] + ([list_style_line] if list_style_line else [])

        if (hook_white or "").strip() or (hook_red or "").strip():
            hook_style_line, hook_events, hook_image_overlays = build_ranking_hook_ass(
                hook_white or "", hook_red or "", target_width, target_height, total_duration
            )
            styles = styles + [hook_style_line]
            events = events + hook_events
            image_overlays.extend(hook_image_overlays)
        ass_path = Path(tempfile.mkstemp(suffix=".ass", prefix="supoclip_rank_")[1])
        header = (
            "[Script Info]\n"
            "ScriptType: v4.00+\n"
            f"PlayResX: {target_width}\n"
            f"PlayResY: {target_height}\n"
            "WrapStyle: 2\n"
            "ScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
            "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            + "\n".join(styles)
            + "\n\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            + "\n".join(events)
            + "\n"
        )
        ass_path.write_text(header)

        fonts_dir = ass_fonts_dir(None)
        subs_fragment = f"subtitles=filename={ffmpeg_escape_filter_path(ass_path)}"
        if fonts_dir:
            subs_fragment += f":fontsdir={ffmpeg_escape_filter_value(str(fonts_dir))}"
        filter_parts.append(f"[vraw]{subs_fragment}[vfinal]")

        # Transition SFX (a template's `transition_sfx` name, e.g. Countdown's
        # whoosh): mixed in-graph via one extra `-i` of the same short sfx
        # file per instance, each delayed with `adelay`, then `amix`ed into
        # the concatenated track — not a post-render pass (video_utils.py's
        # mix_sfx_into_clip), since that would mean one full re-encode per
        # boundary instead of one. `use_global_sfx` (ranking_classic) pulls
        # the file + offset from Settings -> Ranking instead of a template's
        # own curated `transition_sfx`, applies the "play X% before the cut"
        # offset, and appends one further instance whose tail is timed to
        # land exactly at video end — no black frame, no trailing silence.
        if use_global_sfx:
            ranking_config = get_config()
            sfx_path = (
                find_sfx_path(ranking_config.ranking_sfx_filename)
                if ranking_config.ranking_sfx_filename
                else None
            )
        else:
            sfx_path = find_sfx_path(transition_sfx) if transition_sfx else None

        sfx_delays: List[float] = []
        if sfx_path and use_global_sfx:
            sfx_duration = ffprobe_duration(sfx_path)
            offset_pct = max(0.0, min(100.0, get_config().ranking_sfx_offset_pct))
            sfx_offset_seconds = sfx_duration * offset_pct / 100.0
            sfx_delays = [max(0.0, boundary - sfx_offset_seconds) for boundary in boundary_times]
            sfx_delays.append(max(0.0, total_duration - sfx_duration))
        elif sfx_path:
            sfx_delays = list(boundary_times)

        audio_source_label = "araw"
        next_input_index = n  # next free ffmpeg `-i` slot after the N clip inputs
        if sfx_path and sfx_delays:
            sfx_input_start = next_input_index
            sfx_labels: List[str] = []
            for sfx_index, delay in enumerate(sfx_delays):
                delay_ms = max(0, round(delay * 1000))
                filter_parts.append(
                    f"[{sfx_input_start + sfx_index}:a]aresample=48000,aformat=channel_layouts=stereo,"
                    f"adelay={delay_ms}|{delay_ms},volume=0.8[sfx{sfx_index}]"
                )
                sfx_labels.append(f"[sfx{sfx_index}]")
            filter_parts.append(
                f"[araw]{''.join(sfx_labels)}amix=inputs={1 + len(sfx_labels)}:"
                f"duration=first:dropout_transition=0,volume=2[araw_sfx]"
            )
            audio_source_label = "araw_sfx"
            next_input_index += len(sfx_delays)

        # Background music (a template's `background_music` name): looped
        # via `-stream_loop -1` (any compilation length), trimmed to the
        # total duration, then auto-ducked with `sidechaincompress` keyed off
        # the dialogue/sfx track so it drops under speech instead of needing
        # per-clip volume keyframes.
        music_path = find_music_path(background_music) if background_music else None
        music_input_index: Optional[int] = None
        if music_path:
            music_input_index = next_input_index
            filter_parts.append(
                f"[{music_input_index}:a]atrim=0:{total_duration:.3f},asetpts=PTS-STARTPTS,"
                f"aresample=48000,aformat=channel_layouts=stereo,volume={background_music_volume}[music_raw]"
            )
            # A filtergraph label produced by another filter (unlike a raw
            # `[N:a]` input pad) can only feed one downstream filter — needs
            # `asplit` to fan it out into both the sidechain compressor's
            # key input and the final mix.
            filter_parts.append(f"[{audio_source_label}]asplit=2[audio_main][audio_key]")
            filter_parts.append(
                f"[music_raw][audio_key]sidechaincompress="
                f"threshold=0.05:ratio=10:attack=20:release=400[music_ducked]"
            )
            filter_parts.append(
                f"[audio_main][music_ducked]amix=inputs=2:duration=first:"
                f"dropout_transition=0,volume=2[araw_music]"
            )
            audio_source_label = "araw_music"

        # Emoji overlays: user-typed emoji in rank text / the hook can't be
        # burned in as ASS text (this environment's libass can't rasterise
        # colour emoji — see ranking_overlay.py), so each is pre-rendered to
        # a PNG (via Pillow, which CAN render colour-emoji fonts directly)
        # and composited here as an image overlay, mirroring
        # emoji_reactions.py's approach for the clipping tool's reactions.
        final_video_label = "vfinal"
        emoji_input_base = next_input_index
        for overlay_index, overlay in enumerate(image_overlays):
            start = float(overlay["start"])
            end = float(overlay["end"])
            fade_dur = min(0.25, max(0.05, (end - start) / 4))
            input_index = emoji_input_base + overlay_index
            img_label = f"emoji{overlay_index}"
            next_label = f"vfinal_e{overlay_index}"
            filter_parts.append(
                f"[{input_index}:v]format=rgba,"
                f"fade=t=in:st={start:.3f}:d={fade_dur:.3f}:alpha=1,"
                f"fade=t=out:st={max(start, end - fade_dur):.3f}:d={fade_dur:.3f}:alpha=1[{img_label}]"
            )
            filter_parts.append(
                f"[{final_video_label}][{img_label}]overlay=x={overlay['x']}:y={overlay['y']}:"
                f"enable='between(t,{start:.3f},{end:.3f})'[{next_label}]"
            )
            final_video_label = next_label

        # Loudness normalization has to live inside the filter graph (as an
        # [araw]->[afinal] chain step), not as a `-af` output option — ffmpeg
        # refuses to mix simple (`-af`) and complex (`-filter_complex`)
        # filtering on the same output stream.
        audio_args = build_audio_output_args(True, target_lufs=target_lufs)
        if audio_args and audio_args[0] == "-af":
            filter_parts.append(f"[{audio_source_label}]{audio_args[1]}[afinal]")
            audio_encode_args = audio_args[2:]
        else:
            filter_parts.append(f"[{audio_source_label}]anull[afinal]")
            audio_encode_args = audio_args

        command = ["ffmpeg", "-y"]
        for path in local_paths:
            command += ["-i", str(path)]
        if sfx_path and sfx_delays:
            for _ in sfx_delays:
                command += ["-i", str(sfx_path)]
        if music_path is not None:
            command += ["-stream_loop", "-1", "-i", str(music_path)]
        for overlay in image_overlays:
            command += ["-loop", "1", "-i", str(overlay["path"])]
        command += [
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            f"[{final_video_label}]",
            "-map",
            "[afinal]",
            *build_final_video_encode_args(use_gpu=use_gpu),
            *audio_encode_args,
            "-movflags",
            "+faststart",
            str(output_path),
        ]

        try:
            result = run_ffmpeg_command(command, timeout=1800)
            return result.returncode == 0
        finally:
            ass_path.unlink(missing_ok=True)
