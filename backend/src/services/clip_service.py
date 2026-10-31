"""Clip editing and regeneration operations shared by TaskService.

The mixin uses TaskService's repositories, configuration, and cache key contract.
"""

from ..repositories.edit_transaction import serialized_task_edit
from typing import Dict, Any, Optional
import logging
from pathlib import Path
import copy
import json
import tempfile

import redis.asyncio as redis

from ..clip_editor import (
    trim_clip_file,
    split_clip_file,
    merge_clip_files,
    overlay_custom_captions,
)
from ..video_utils import VALID_OUTPUT_FORMATS, parse_timestamp_to_seconds, create_optimized_clip
from ..utils.async_helpers import run_in_thread
from ..clip_duration import clip_duration, normalize_clip_length
from ..media.common import MAX_RENDERED_CLIP_SECONDS
from ..clip_cleanup import normalize_clip_cleanup_settings
from ..broll import broll_overlays_for, shift_broll_placements
from ..clip_source_map import (
    normalize_source_ranges,
    load_clip_broll,
    load_clip_source_ranges,
    save_clip_broll,
    save_clip_source_ranges,
    load_clip_output_offsets,
    slice_source_timeline,
    save_clip_caption_settings,
    load_clip_caption_settings,
    source_range_bounds,
    split_source_ranges,
    trim_source_ranges,
)

from ..media_storage import MediaStorage
from ..youtube_source import (
    load_time_map,
    local_ranges_for,
    localize_segment,
    ranges_from_segments,
    restore_clip_timeline,
)
from ..media.ffmpeg import cap_video_short_side, ffprobe_duration, rendered_clip_duration
from ..stream_source import download_stream_sections
from .video_service import resolve_upload_for_edit

logger = logging.getLogger(__name__)


def fill_output_starts(items: Any, group_output_starts: list) -> None:
    """Set each recap point / trailer beat's start on the rendered clip timeline."""
    for item in items if isinstance(items, list) else []:
        index = item.get("range_index") if isinstance(item, dict) else None
        if isinstance(index, int) and 0 <= index < len(group_output_starts):
            start = group_output_starts[index]
            item["output_start"] = round(float(start), 3) if start is not None else None


# Keys that describe a clip's planned cuts; a clip holding other footage drops them.
PLANNED_META_KEYS = ("planned", "ranges", "junction_fades", "max_seconds", "points", "beats")
# Rounding slack when checking that a played piece lies inside a planned range.
PLAN_EDGE_TOLERANCE_SECONDS = 0.05


def _overlap(piece: tuple, planned_range: tuple) -> float:
    return min(float(piece[1]), float(planned_range[1])) - max(float(piece[0]), float(planned_range[0]))


def _follows_plan(pieces: list, planned: list) -> bool:
    """Every played piece comes from one of the planned ranges (trims and splits only).

    A merge adds footage the plan never chose; such a clip is re-cut like any
    edited clip instead of being pulled back onto its first clip's plan.
    """
    return bool(planned) and all(
        any(
            float(piece[0]) >= float(start) - PLAN_EDGE_TOLERANCE_SECONDS
            and float(piece[1]) <= float(end) + PLAN_EDGE_TOLERANCE_SECONDS
            for start, end in planned
        )
        for piece in pieces
    )


def _unplanned_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Workflow metadata of a clip that no longer plays its planned cuts (series title, part title, ...)."""
    return {key: value for key, value in meta.items() if key not in PLANNED_META_KEYS}


def _range_groups(pieces: list, planned: list) -> list[int]:
    """Index of the planned range each played piece came from.

    Cleanup only cuts inside a planned range, so a piece overlaps the range
    it was cut from most; trailer ranges may play out of source order.
    """
    return [
        max(range(len(planned)), key=lambda index: _overlap(piece, planned[index]))
        for piece in pieces
    ]


def _narrow_planned_meta(meta: Dict[str, Any], pieces: list, offsets: list) -> Optional[Dict[str, Any]]:
    """Workflow metadata for a clip that now plays only ``pieces`` (after a trim or split).

    ``ranges`` keeps the planned ranges these pieces still cover, trimmed to
    them, with the fades between them. Recap points and trailer beats keep
    their place on the new timeline (``output_start``) or go with their range.
    None when the pieces include footage outside the plan (a merged clip).
    """
    planned = normalize_source_ranges(meta.get("ranges"))
    if not planned or not pieces:
        return dict(meta)
    if not _follows_plan(pieces, planned):
        return None
    fades = [float(value) for value in meta.get("junction_fades") or []]
    groups = _range_groups(pieces, planned)
    kept: list[int] = []
    narrowed: list[tuple[float, float]] = []
    starts: Dict[int, float] = {}
    for group, (start, end) in enumerate(planned):
        members = [index for index, owner in enumerate(groups) if owner == group]
        if not members:
            continue
        kept.append(group)
        narrowed.append((
            max(start, min(pieces[index][0] for index in members)),
            min(end, max(pieces[index][1] for index in members)),
        ))
        starts[group] = max(0.0, float(offsets[members[0]])) if members[0] < len(offsets) else 0.0
    result = dict(meta)
    result["ranges"] = [[round(start, 6), round(end, 6)] for start, end in narrowed]
    result["junction_fades"] = [
        fades[kept[index] - 1] if kept[index] - 1 == kept[index - 1] and kept[index] <= len(fades) else 0.0
        for index in range(1, len(kept))
    ]
    for key in ("points", "beats"):
        if isinstance(meta.get(key), list):
            result[key] = [
                {
                    **item,
                    "range_index": kept.index(item["range_index"]),
                    "output_start": round(starts[item["range_index"]], 3),
                }
                for item in meta[key]
                if isinstance(item, dict) and item.get("range_index") in kept
            ]
    return result


class ClipEditingMixin:
    @serialized_task_edit
    async def delete_clip(self, task_id: str, clip_id: str) -> None:
        clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise ValueError("Clip not found")
        await self.clip_repo.delete_clip(self.db, clip_id)
        await self.clip_repo.reorder_task_clips(self.db, task_id)

    @serialized_task_edit
    async def update_task_settings(
        self,
        task_id: str,
        font_family: Optional[str],
        font_size: Optional[int],
        font_color: Optional[str],
        caption_template: str,
        include_broll: bool,
        apply_to_existing: bool,
        cleanup_settings: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Update task-level settings and optionally regenerate all clips."""
        await self.task_repo.update_task_settings(
            self.db,
            task_id,
            font_family,
            font_size,
            font_color,
            caption_template,
            include_broll,
        )

        if apply_to_existing:
            await self.regenerate_all_clips_for_task(
                task_id,
                font_family,
                font_size,
                font_color,
                caption_template,
                cleanup_settings=cleanup_settings,
            )

        return await self.get_task_with_clips(task_id) or {}


    @serialized_task_edit
    async def regenerate_all_clips_for_task(
        self,
        task_id: str,
        font_family: Optional[str],
        font_size: Optional[int],
        font_color: Optional[str],
        caption_template: str,
        cleanup_settings: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Regenerate all clips in a task using existing segment boundaries."""
        task = await self.task_repo.get_task_by_id(self.db, task_id)
        if not task:
            raise ValueError("Task not found")

        source_url = task.get("source_url")
        source_type = task.get("source_type")
        metadata = await self._load_task_source_settings(task_id)
        output_format = metadata.get("output_format", "vertical")
        add_subtitles = metadata.get("add_subtitles", True)
        cleanup_payload = cleanup_settings or {
            "cut_long_pauses": metadata.get("cut_long_pauses"),
            "pause_threshold_ms": metadata.get("pause_threshold_ms"),
            "remove_filler_words": metadata.get("remove_filler_words"),
            "filtered_words": metadata.get("filtered_words"),
        }
        normalized_cleanup_settings = normalize_clip_cleanup_settings(
            cleanup_payload.get("cut_long_pauses"),
            cleanup_payload.get("pause_threshold_ms"),
            cleanup_payload.get("remove_filler_words"),
            cleanup_payload.get("filtered_words"),
        )
        existing_cleanup_settings = normalize_clip_cleanup_settings(
            metadata.get("cut_long_pauses"),
            metadata.get("pause_threshold_ms"),
            metadata.get("remove_filler_words"),
            metadata.get("filtered_words"),
        )
        should_recompute_cleanup = (
            cleanup_settings is not None
            and normalized_cleanup_settings != existing_cleanup_settings
        )

        if not source_url or not source_type:
            raise ValueError("Task source URL is missing; cannot regenerate clips")

        clips = await self.clip_repo.get_clips_by_task(self.db, task_id)
        if not clips:
            return

        download_quality, output_short_side = await self._media_limits(task.get("user_id"))
        include_broll = bool(task.get("include_broll"))
        segments = []
        for clip in clips:
            source_ranges = await run_in_thread(self._get_clip_source_ranges, clip)
            # Same timeline, same cutaways. Recomputed cleanup moves the words,
            # so those clips get a fresh plan instead.
            broll = (
                await run_in_thread(self._get_clip_broll, clip)
                if include_broll and not should_recompute_cleanup
                else []
            )
            meta = clip.get("workflow_meta") or {}
            if meta.get("planned") and normalize_source_ranges(meta.get("ranges")):
                planned_segment = await run_in_thread(
                    self._planned_regeneration_segment,
                    clip,
                    source_ranges,
                    broll,
                    should_recompute_cleanup,
                )
                if planned_segment is not None:
                    segments.append(planned_segment)
                    continue
            # Workflow clips re-cut like any edited clip (a merged part) keep
            # their role and part number; a series still needs every part.
            workflow_fields = (
                {
                    "workflow_role": clip.get("workflow_role"),
                    "part_number": clip.get("part_number"),
                    "workflow_meta": _unplanned_meta(meta),
                    "required": clip.get("workflow_role") in ("part", "recap", "trailer"),
                }
                if clip.get("workflow_role") or meta
                else {}
            )
            bounds = source_range_bounds(source_ranges)
            if bounds:
                start_time = self._seconds_to_mmss(bounds[0])
                end_time = self._seconds_to_mmss(bounds[1])
            else:
                start_time = clip["start_time"]
                end_time = clip["end_time"]

            segments.append(
                {
                    "start_time": start_time,
                    "end_time": end_time,
                    **(
                        {"source_ranges": source_ranges}
                        if should_recompute_cleanup
                        else {"keep_ranges": source_ranges}
                    ),
                    "text": clip.get("text") or "",
                    "relevance_score": clip.get("relevance_score", 0.5),
                    "reasoning": clip.get("reasoning")
                    or "Regenerated with updated settings",
                    "virality_score": clip.get("virality_score", 0),
                    "hook_score": clip.get("hook_score", 0),
                    "engagement_score": clip.get("engagement_score", 0),
                    "value_score": clip.get("value_score", 0),
                    "shareability_score": clip.get("shareability_score", 0),
                    "hook_type": clip.get("hook_type"),
                    "hook_title": clip.get("hook_title"),
                    "broll": broll,
                    **workflow_fields,
                }
            )

        video_path: Path
        if source_type == "youtube":
            downloaded = await self.video_service.download_video(
                source_url,
                quality=download_quality,
                task_id=task_id,
                ranges=ranges_from_segments(segments),
            )
            if not downloaded:
                raise ValueError("Failed to download source video for regeneration")
            video_path = Path(downloaded)
        elif source_type in {"twitch", "kick"}:
            # Its own stream-work operation; cleaned below without touching
            # any other edit of this task.
            video_path = await run_in_thread(
                download_stream_sections, source_url, segments,
                task.get("source_duration_seconds"), task_id,
            )
        else:
            video_path = await resolve_upload_for_edit(self.video_service, source_url)
            if not video_path.exists():
                raise ValueError("Source video file no longer exists")

        try:
            # Cap the working file before localizing timestamps. The path does
            # not change, so the timeline sidecar still applies.
            video_path = await self.video_service.prepare_source(video_path)
            time_map = await run_in_thread(load_time_map, video_path)
            render_segments = [
                localize_segment(segment, time_map) if time_map else segment
                for segment in segments
            ]
            clips_info = await self.video_service.create_video_clips(
                video_path,
                render_segments,
                font_family,
                font_size,
                font_color,
                caption_template,
                output_format,
                add_subtitles,
                normalized_cleanup_settings,
                max_short_side=output_short_side,
                # Merged and trimmed clips can run past a short preset; only
                # cap them as tightly as they were before presets existed.
                max_clip_seconds=max(
                    clip_duration(normalize_clip_length(metadata.get("clip_length"))).maximum,
                    MAX_RENDERED_CLIP_SECONDS,
                ),
                include_broll=include_broll,
            )
            if time_map:
                for clip_info in clips_info:
                    index = int(clip_info.get("clip_id") or 0) - 1
                    if 0 <= index < len(segments):
                        await run_in_thread(
                            restore_clip_timeline, clip_info, time_map, segments[index]
                        )
        finally:
            if source_type in {"youtube", "twitch", "kick"}:
                self._cleanup_source_video(video_path, source_type, source_url)

        rendered_indexes = {int(clip_info.get("clip_id") or 0) - 1 for clip_info in clips_info}
        missing_required = [
            segment for index, segment in enumerate(segments)
            if segment.get("required") and index not in rendered_indexes
        ]
        if not clips_info or missing_required:
            # Replacing the rows with nothing (or a series without a part)
            # would lose clips the user has.
            raise ValueError("Could not regenerate the clips. Your existing clips are unchanged.")

        await self.clip_repo.delete_clips_by_task(self.db, task_id)

        clip_ids = []
        for i, clip_info in enumerate(clips_info):
            index = int(clip_info.get("clip_id") or 0) - 1
            segment = segments[index] if 0 <= index < len(segments) else {}
            workflow_fields: Dict[str, Any] = {}
            if "workflow_role" in segment and not segment.get("planned"):
                workflow_fields = {
                    "workflow_role": segment.get("workflow_role"),
                    "part_number": segment.get("part_number"),
                    "workflow_meta": segment.get("workflow_meta"),
                }
            elif segment.get("planned"):
                meta = copy.deepcopy(segment.get("workflow_meta") or {})
                group_output_starts = clip_info.get("group_output_starts") or []
                fill_output_starts(meta.get("points"), group_output_starts)
                fill_output_starts(meta.get("beats"), group_output_starts)
                bounds = source_range_bounds(normalize_source_ranges(meta.get("ranges")))
                if bounds:
                    clip_info["start_time"] = self._seconds_to_mmss(bounds[0])
                    clip_info["end_time"] = self._seconds_to_mmss(bounds[1])
                workflow_fields = {
                    "workflow_role": segment.get("workflow_role"),
                    "part_number": segment.get("part_number"),
                    "workflow_meta": meta,
                }
            clip_id = await self.clip_repo.create_clip(
                self.db,
                task_id=task_id,
                filename=clip_info["filename"],
                file_path=clip_info["path"],
                start_time=clip_info["start_time"],
                end_time=clip_info["end_time"],
                duration=clip_info["duration"],
                text=clip_info.get("text") or "",
                relevance_score=clip_info.get("relevance_score", 0.5),
                reasoning=clip_info.get("reasoning")
                or "Regenerated with updated settings",
                clip_order=i + 1,
                virality_score=clip_info.get("virality_score", 0),
                hook_score=clip_info.get("hook_score", 0),
                engagement_score=clip_info.get("engagement_score", 0),
                value_score=clip_info.get("value_score", 0),
                shareability_score=clip_info.get("shareability_score", 0),
                hook_type=clip_info.get("hook_type"),
                hook_title=clip_info.get("hook_title"),
                **workflow_fields,
            )
            clip_ids.append(clip_id)

        await self.task_repo.update_task_clips(self.db, task_id, clip_ids)

    def _planned_regeneration_segment(
        self,
        clip: Dict[str, Any],
        source_ranges: list,
        broll: list,
        recompute_cleanup: bool,
    ) -> Optional[Dict[str, Any]]:
        """Re-render a workflow clip on its planned cuts, never the AI-bounds path.

        With unchanged cleanup the stored ranges and their offsets play again.
        Changed cleanup (or a lost source map) cleans the planned ranges anew,
        trimmed to what the clip still plays after the user's edits. None when
        the clip holds footage outside its plan (merged): it re-renders as an
        edited clip.
        """
        file_path = clip.get("file_path")
        metadata_path = (
            MediaStorage().local_path(file_path, metadata_only=True)
            if isinstance(file_path, str) and file_path
            else None
        )
        stored = load_clip_source_ranges(metadata_path) if metadata_path else None
        offsets = (
            load_clip_output_offsets(metadata_path, source_ranges)
            if stored
            else [0.0] * len(source_ranges)
        )
        meta = _narrow_planned_meta(clip.get("workflow_meta") or {}, source_ranges, offsets)
        if meta is None:
            return None
        planned = normalize_source_ranges(meta.get("ranges"))
        # The plan was accepted at its budget; new cleanup settings may leave
        # more pause in it, and cutting the end would drop words between parts.
        planned_seconds = sum(end - start for start, end in planned)
        segment: Dict[str, Any] = {
            "start_time": self._seconds_to_mmss(planned[0][0]),
            "end_time": self._seconds_to_mmss(planned[0][1]),
            "planned": True,
            "required": clip.get("workflow_role") in ("part", "recap", "trailer"),
            "max_seconds": max(float(meta.get("max_seconds") or 0.0), planned_seconds),
            "workflow_role": clip.get("workflow_role"),
            "part_number": clip.get("part_number"),
            "workflow_meta": meta,
            "text": clip.get("text") or "",
            "relevance_score": clip.get("relevance_score", 0.5),
            "reasoning": clip.get("reasoning") or "Regenerated with updated settings",
            "virality_score": clip.get("virality_score", 0),
            "hook_score": clip.get("hook_score", 0),
            "engagement_score": clip.get("engagement_score", 0),
            "value_score": clip.get("value_score", 0),
            "shareability_score": clip.get("shareability_score", 0),
            "hook_type": clip.get("hook_type"),
            "hook_title": clip.get("hook_title"),
            "broll": broll,
        }
        if recompute_cleanup or not stored:
            segment["source_ranges"] = planned
            segment["junction_fades"] = meta.get("junction_fades") or []
        else:
            segment["keep_ranges"] = source_ranges
            segment["output_offsets"] = offsets
            segment["range_groups"] = _range_groups(source_ranges, planned)
        return segment


    @serialized_task_edit
    async def trim_clip(
        self,
        task_id: str,
        clip_id: str,
        start_offset: float,
        end_offset: float,
    ) -> Dict[str, Any]:
        clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise ValueError("Clip not found")

        input_path = await run_in_thread(MediaStorage().local_path, clip["file_path"])
        if not input_path.exists():
            raise ValueError("Clip file not found")

        output_path = await run_in_thread(
            trim_clip_file, input_path, Path(self.config.temp_dir) / "clips", start_offset, end_offset
        )
        source_ranges = await run_in_thread(self._get_clip_source_ranges, clip)
        offsets = load_clip_output_offsets(input_path, source_ranges)
        total = max(offset + b - a for offset, (a, b) in zip(offsets, source_ranges))
        trimmed_ranges, trimmed_offsets = slice_source_timeline(source_ranges, start_offset, total - end_offset, offsets)
        clip_duration = max(
            0.1, await run_in_thread(rendered_clip_duration, output_path, trimmed_ranges)
        )
        bounds = source_range_bounds(trimmed_ranges)
        if not bounds:
            raise ValueError("Trimmed clip has no remaining source mapping")
        start_seconds, end_seconds = bounds
        save_clip_source_ranges(output_path, trimmed_ranges, trimmed_offsets)
        if settings := load_clip_caption_settings(input_path):
            save_clip_caption_settings(output_path, settings)
        if broll := load_clip_broll(input_path):
            await self._carry_broll(broll, [(output_path, max(0.0, start_offset))])

        new_start = self._seconds_to_mmss(start_seconds)
        new_end = self._seconds_to_mmss(end_seconds)

        await self.clip_repo.update_clip(
            self.db,
            clip_id,
            output_path.name,
            str(output_path),
            new_start,
            new_end,
            clip_duration,
            clip.get("text") or "",
        )
        return (await self.clip_repo.get_clip_by_id(self.db, clip_id)) or {}


    @serialized_task_edit
    async def split_clip(
        self, task_id: str, clip_id: str, split_time: float
    ) -> Dict[str, Any]:
        clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise ValueError("Clip not found")

        input_path = await run_in_thread(MediaStorage().local_path, clip["file_path"])
        if not input_path.exists():
            raise ValueError("Clip file not found")

        first_path, second_path = await run_in_thread(
            split_clip_file, input_path, Path(self.config.temp_dir) / "clips", split_time
        )

        clamped_split = max(0.2, min(split_time, float(clip["duration"]) - 0.2))
        source_ranges = await run_in_thread(self._get_clip_source_ranges, clip)
        offsets = load_clip_output_offsets(input_path, source_ranges)
        total = max(offset + b - a for offset, (a, b) in zip(offsets, source_ranges))
        first_ranges, first_offsets = slice_source_timeline(source_ranges, 0, clamped_split, offsets)
        second_ranges, second_offsets = slice_source_timeline(source_ranges, clamped_split, total, offsets)
        first_bounds = source_range_bounds(first_ranges)
        second_bounds = source_range_bounds(second_ranges)
        if not first_bounds or not second_bounds:
            raise ValueError("Split clip has invalid source mapping")
        save_clip_source_ranges(first_path, first_ranges, first_offsets)
        save_clip_source_ranges(second_path, second_ranges, second_offsets)
        if settings := load_clip_caption_settings(input_path):
            save_clip_caption_settings(first_path, settings)
            save_clip_caption_settings(second_path, settings)
        if broll := load_clip_broll(input_path):
            await self._carry_broll(broll, [(first_path, 0.0), (second_path, clamped_split)])
        first_duration = max(
            0.1, await run_in_thread(rendered_clip_duration, first_path, first_ranges)
        )
        second_duration = max(
            0.1, await run_in_thread(rendered_clip_duration, second_path, second_ranges)
        )

        await self.clip_repo.update_clip(
            self.db,
            clip_id,
            first_path.name,
            str(first_path),
            self._seconds_to_mmss(first_bounds[0]),
            self._seconds_to_mmss(first_bounds[1]),
            first_duration,
            clip.get("text") or "",
        )

        workflow_fields: Dict[str, Any] = {}
        meta = clip.get("workflow_meta") or {}
        if clip.get("workflow_role") or meta:
            # The second half is still part N of the series (or a piece of the
            # recap); its metadata describes only what it plays.
            second_meta = (
                _narrow_planned_meta(meta, second_ranges, second_offsets)
                if meta.get("planned")
                else dict(meta)
            )
            if second_meta is None:
                # A merged clip's second half may be footage its plan never held.
                second_meta = _unplanned_meta(meta)
            second_meta["split"] = 2
            workflow_fields = {
                "workflow_role": clip.get("workflow_role"),
                "part_number": clip.get("part_number"),
                "workflow_meta": second_meta,
            }

        await self.clip_repo.make_room_for_clip(
            self.db, task_id, clip.get("clip_order", 1) + 1
        )
        await self.clip_repo.create_clip(
            self.db,
            task_id=task_id,
            filename=second_path.name,
            file_path=str(second_path),
            start_time=self._seconds_to_mmss(second_bounds[0]),
            end_time=self._seconds_to_mmss(second_bounds[1]),
            duration=second_duration,
            text=clip.get("text") or "",
            relevance_score=clip.get("relevance_score", 0.5),
            reasoning=clip.get("reasoning") or "Split from original clip",
            clip_order=clip.get("clip_order", 1) + 1,
            virality_score=clip.get("virality_score", 0),
            hook_score=clip.get("hook_score", 0),
            engagement_score=clip.get("engagement_score", 0),
            value_score=clip.get("value_score", 0),
            shareability_score=clip.get("shareability_score", 0),
            hook_type=clip.get("hook_type"),
            hook_title=clip.get("hook_title"),
            **workflow_fields,
        )

        await self.clip_repo.reorder_task_clips(self.db, task_id)
        return {"message": "Clip split successfully"}


    @serialized_task_edit
    async def merge_clips(self, task_id: str, clip_ids: list[str]) -> Dict[str, Any]:
        if len(clip_ids) < 2 or len(set(clip_ids)) != len(clip_ids):
            raise ValueError("At least two distinct clips are required to merge")

        clips = []
        for clip_id in clip_ids:
            clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
            if not clip or clip["task_id"] != task_id:
                raise ValueError("One or more clips not found")
            clips.append(clip)

        ordered = sorted(clips, key=lambda c: c.get("clip_order", 0))
        input_paths = [await run_in_thread(MediaStorage().local_path, c["file_path"]) for c in ordered]
        merged_path = await run_in_thread(
            merge_clip_files,
            input_paths,
            Path(self.config.temp_dir) / "clips",
        )

        merged_ranges, merged_offsets, cursor = [], [], 0.0
        for clip, path in zip(ordered, input_paths):
            source_ranges = await run_in_thread(self._get_clip_source_ranges, clip)
            merged_ranges.extend(source_ranges)
            merged_offsets += [cursor + offset for offset in load_clip_output_offsets(path, source_ranges)]
            cursor += float(clip["duration"])
        merged_bounds = source_range_bounds(merged_ranges)
        if merged_bounds:
            start_time = self._seconds_to_mmss(merged_bounds[0])
            end_time = self._seconds_to_mmss(merged_bounds[1])
            duration = await run_in_thread(rendered_clip_duration, merged_path, merged_ranges)
            save_clip_source_ranges(merged_path, merged_ranges, merged_offsets)
        else:
            start_time = ordered[0]["start_time"]
            end_time = ordered[-1]["end_time"]
            duration = sum(float(c.get("duration", 0.0)) for c in ordered)
        clip_broll = [load_clip_broll(path) for path in input_paths]
        if any(clip_broll):
            merged_broll = []
            offset = 0.0
            try:
                for path, placements in zip(input_paths, clip_broll):
                    length = await run_in_thread(ffprobe_duration, path)
                    merged_broll += shift_broll_placements(placements, -offset, offset + length)
                    offset += length
                save_clip_broll(merged_path, merged_broll)
            except Exception:
                # The merged file already has the footage; only later re-renders lose it.
                logger.warning("Could not carry B-roll placements into merged clip", exc_info=True)
        text = " ".join((c.get("text") or "").strip() for c in ordered if c.get("text"))

        first = ordered[0]
        await self.clip_repo.update_clip(
            self.db,
            first["id"],
            merged_path.name,
            str(merged_path),
            start_time,
            end_time,
            duration,
            text,
        )

        for clip in ordered[1:]:
            await self.clip_repo.delete_clip(self.db, clip["id"])

        await self.clip_repo.reorder_task_clips(self.db, task_id)
        return {"message": "Clips merged successfully", "clip_id": first["id"]}


    @serialized_task_edit
    async def update_clip_captions(
        self,
        task_id: str,
        clip_id: str,
        caption_text: str,
        position: str,
        highlight_words: list[str],
        *,
        font_size: Optional[int] = None,
        position_y: Optional[float] = None,
    ) -> Dict[str, Any]:
        clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise ValueError("Clip not found")

        input_path = await run_in_thread(MediaStorage().local_path, clip["file_path"])
        if not input_path.exists():
            raise ValueError("Clip file not found")

        task = await self.task_repo.get_task_by_id(self.db, task_id)
        if not task:
            raise ValueError("Task not found")

        transcript_video_path: Optional[Path] = None
        source_url = task.get("source_url")
        source_type = task.get("source_type")
        processing_mode = (
            task.get("processing_mode") or self.config.default_processing_mode
        )
        download_quality, output_short_side = await self._media_limits(task.get("user_id"))
        if source_url and source_type:
            cache_entry = await self.cache_repo.get_cache(
                self.db,
                self._build_cache_key(source_url, source_type, processing_mode, download_quality),
            )
            cached_video_path = cache_entry.get("video_path") if cache_entry else None
            if cached_video_path:
                transcript_video_path = await run_in_thread(MediaStorage().local_path, cached_video_path)
            elif source_type not in {"youtube", "twitch", "kick"}:
                try:
                    transcript_video_path = await resolve_upload_for_edit(
                        self.video_service, source_url
                    )
                except ValueError:
                    transcript_video_path = None

        # Always render from the source; overlaying an already captioned clip
        # stacks old and new captions and compounds quality loss on every save.
        source_ranges = await run_in_thread(self._get_clip_source_ranges, clip)
        downloaded_source: Optional[Path] = None
        if not transcript_video_path or not transcript_video_path.exists():
            if source_type == "youtube" and source_url:
                downloaded = await self.video_service.download_video(
                    source_url,
                    quality=download_quality,
                    task_id=task_id,
                    ranges=source_ranges,
                )
                transcript_video_path = Path(downloaded) if downloaded else None
                downloaded_source = transcript_video_path
            elif source_type in {"twitch", "kick"} and source_url:
                transcript_video_path = await run_in_thread(
                    download_stream_sections, source_url,
                    [{"start_time": start, "end_time": end} for start, end in source_ranges],
                    task.get("source_duration_seconds"), task_id,
                )
                downloaded_source = transcript_video_path
            elif source_url:
                transcript_video_path = await resolve_upload_for_edit(self.video_service, source_url)
        if not transcript_video_path or not transcript_video_path.exists():
            raise ValueError("The source video is no longer available. Upload it again to edit captions.")

        try:
            transcript_video_path = await self.video_service.prepare_source(transcript_video_path)
            settings = await self._load_task_source_settings(task_id)
            local_ranges = await run_in_thread(
                local_ranges_for, transcript_video_path, source_ranges
            )
            bounds = source_range_bounds(local_ranges)
            if not bounds:
                raise ValueError("Clip source timing is unavailable")
            output_dir = Path(self.config.temp_dir) / "clips"
            output_dir.mkdir(parents=True, exist_ok=True)
            # The clip timeline is unchanged, so its cutaways go back on as they were.
            broll = load_clip_broll(input_path)
            broll_overlays = await broll_overlays_for(broll)
            render_report: Dict[str, Any] = {}
            # Workflow clips keep their planned junctions (fades, out-of-order beats).
            output_offsets = None
            if (clip.get("workflow_meta") or {}).get("planned") and len(local_ranges) == len(source_ranges):
                output_offsets = load_clip_output_offsets(input_path, source_ranges)
            offsets_kwargs = {"output_offsets": output_offsets} if output_offsets is not None else {}
            with tempfile.TemporaryDirectory(prefix="caption_edit_", dir=output_dir) as temporary:
                clean_path = Path(temporary) / "clean.mp4"
                rendered = await run_in_thread(
                    create_optimized_clip, transcript_video_path, bounds[0], bounds[1], clean_path,
                    add_subtitles=False,
                    output_format=settings.get("output_format", "vertical"),
                    keep_ranges=local_ranges,
                    hook_title=clip.get("hook_title"),
                    font_family=task.get("font_family") or None,
                    font_size=task.get("font_size") or None,
                    font_color=task.get("font_color") or None,
                    caption_template=task.get("caption_template") or "default",
                    extend_to_sentence=False,
                    layout_report=render_report,
                    broll_overlays=broll_overlays,
                    **offsets_kwargs,
                )
                if not rendered:
                    raise ValueError("Could not prepare the clip for caption editing")
                if not render_report.get("broll_applied"):
                    broll = []
                output_path = await run_in_thread(
                    overlay_custom_captions,
                    clean_path, output_dir, caption_text, position, highlight_words,
                    font_family=task.get("font_family") or None,
                    font_size=font_size if font_size is not None else task.get("font_size") or None,
                    font_color=task.get("font_color") or None,
                    caption_template=task.get("caption_template") or "default",
                    transcript_video_path=transcript_video_path,
                    source_ranges=local_ranges,
                    position_y=position_y,
                )
                if output_short_side:
                    try:
                        await run_in_thread(cap_video_short_side, output_path, output_short_side)
                    except Exception as e:
                        logger.warning(f"Keeping full-size caption edit for clip {clip_id}: {e}")
        finally:
            if downloaded_source is not None:
                self._cleanup_source_video(downloaded_source, source_type, source_url)
        save_clip_source_ranges(output_path, source_ranges, output_offsets)
        save_clip_caption_settings(output_path, {
            "font_size": font_size if font_size is not None else task.get("font_size"),
            "position": position,
            "position_y": position_y if position_y is not None else {"top": 0.18, "middle": 0.52, "bottom": 0.78}[position],
            "highlight_words": highlight_words,
        })
        save_clip_broll(output_path, broll)
        # Re-rendering also corrects durations stored before they were probed.
        rendered_duration = await run_in_thread(
            rendered_clip_duration, output_path, source_ranges, output_offsets
        )

        await self.clip_repo.update_clip(
            self.db,
            clip_id,
            output_path.name,
            str(output_path),
            clip["start_time"],
            clip["end_time"],
            rendered_duration if rendered_duration > 0 else clip["duration"],
            caption_text,
        )
        return (await self.clip_repo.get_clip_by_id(self.db, clip_id)) or {}


    @staticmethod
    def _seconds_to_mmss(seconds: float) -> str:
        total = max(0, int(round(seconds)))
        minutes = total // 60
        secs = total % 60
        return f"{minutes:02d}:{secs:02d}"


    @staticmethod
    async def _carry_broll(broll: list[dict], cuts: list[tuple[Path, float]]) -> None:
        """Store B-roll for clips cut from a longer one (path, start offset).

        The cut files already contain the footage; losing the placements only
        means a later re-render from source drops it, so never fail the edit.
        """
        try:
            for path, offset in cuts:
                length = await run_in_thread(ffprobe_duration, path)
                save_clip_broll(path, shift_broll_placements(broll, offset, length))
        except Exception:
            logger.warning("Could not carry B-roll placements into edited clip", exc_info=True)

    @staticmethod
    def _get_clip_broll(clip: Dict[str, Any]) -> list[dict]:
        file_path = clip.get("file_path")
        if not isinstance(file_path, str) or not file_path:
            return []
        return load_clip_broll(MediaStorage().local_path(file_path, metadata_only=True))

    @staticmethod
    def _get_clip_source_ranges(clip: Dict[str, Any]) -> list[tuple[float, float]]:
        file_path = clip.get("file_path")
        if isinstance(file_path, str) and file_path:
            persisted = load_clip_source_ranges(MediaStorage().local_path(file_path, metadata_only=True))
            if persisted:
                return persisted

        start_seconds = parse_timestamp_to_seconds(clip["start_time"])
        end_seconds = parse_timestamp_to_seconds(clip["end_time"])
        # A workflow clip's planned ranges; its labels span every jump in between.
        # Labels reaching past them (a merge added footage) win.
        planned = normalize_source_ranges((clip.get("workflow_meta") or {}).get("ranges"))
        planned_bounds = source_range_bounds(planned)
        if planned_bounds and start_seconds >= planned_bounds[0] - 1.0 and end_seconds <= planned_bounds[1] + 1.0:
            return planned
        return [(start_seconds, end_seconds)]


    async def _load_task_source_settings(self, task_id: str) -> Dict[str, Any]:
        defaults = {
            "output_format": "vertical",
            "add_subtitles": True,
            "clip_length": "auto",
            **normalize_clip_cleanup_settings(),
        }
        parsed = (
            await self.task_repo.load_source_metadata(self.db, task_id)
            if self.db is not None else {}
        )
        if not parsed:
            redis_client = redis.Redis(
                host=self.config.redis_host,
                port=self.config.redis_port,
                password=self.config.redis_password,
                decode_responses=True,
            )
            try:
                payload = await redis_client.get(f"task_source:{task_id}")
            except Exception as exc:
                logger.warning(
                    "Falling back to default task source settings for task %s: %s",
                    task_id, exc,
                )
                return defaults
            finally:
                try:
                    await redis_client.aclose()
                except Exception:
                    pass

            if not payload:
                return defaults
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                return defaults
            if not isinstance(parsed, dict):
                return defaults

        output_format = parsed.get("output_format", defaults["output_format"])
        if output_format not in VALID_OUTPUT_FORMATS:
            output_format = defaults["output_format"]

        add_subtitles = parsed.get("add_subtitles", defaults["add_subtitles"])
        if not isinstance(add_subtitles, bool):
            add_subtitles = defaults["add_subtitles"]

        return {
            "output_format": output_format,
            "add_subtitles": add_subtitles,
            "clip_length": normalize_clip_length(parsed.get("clip_length")),
            **normalize_clip_cleanup_settings(
                parsed.get("cut_long_pauses"),
                parsed.get("pause_threshold_ms"),
                parsed.get("remove_filler_words"),
                parsed.get("filtered_words"),
            ),
        }
