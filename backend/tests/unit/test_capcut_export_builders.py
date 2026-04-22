"""Tests for the CapCut draft JSON builders in ``src/capcut_export.py``.

These cover the real ``build_draft_content`` / ``build_draft_meta_info`` flows
using the on-disk JSON templates — only the ``package_capcut_draft`` zip/file IO
path is skipped.
"""

from pathlib import Path

import pytest

from src.capcut_export import (
    ClipExportInput,
    SubtitleSegment,
    _build_canvas_material,
    _build_sound_channel_material,
    _build_speed_material,
    _build_text_material,
    _build_text_segment,
    _build_video_material,
    _build_video_segment,
    build_draft_content,
    build_draft_meta_info,
)


def _clip(
    *,
    duration=10.0,
    subtitle_segments=None,
    caption_text=None,
    project_name=None,
):
    return ClipExportInput(
        clip_filename="clip_demo.mp4",
        clip_source_path=Path("/tmp/clip_demo.mp4"),
        duration_seconds=duration,
        caption_text=caption_text,
        subtitle_segments=subtitle_segments or [],
        project_name=project_name,
    )


class TestBuildMaterials:
    def test_video_material_has_path_and_type(self):
        clip = _clip()
        mat = _build_video_material("mat-1", clip, "Resources/clip_demo.mp4")
        assert mat["id"] == "mat-1"
        assert mat["path"] == "Resources/clip_demo.mp4"
        assert mat["type"] == "video"
        assert mat["duration"] == 10 * 1_000_000

    def test_speed_material(self):
        s = _build_speed_material("spd-1")
        assert s["id"] == "spd-1"
        assert s["type"] == "speed"
        assert s["speed"] == 1.0

    def test_canvas_material(self):
        c = _build_canvas_material("cv-1")
        assert c["id"] == "cv-1"
        assert c["type"] == "canvas_color"

    def test_sound_channel_material(self):
        s = _build_sound_channel_material("sc-1")
        assert s["id"] == "sc-1"
        assert s["type"] == "none"

    def test_text_material_wraps_content(self):
        t = _build_text_material("t-1", "Hello <world>")
        assert t["id"] == "t-1"
        assert t["type"] == "text"
        # content field embeds the string
        assert "Hello <world>" in t["content"]


class TestBuildSegments:
    def test_video_segment_applies_extra_refs(self):
        seg = _build_video_segment("seg-1", "mat-1", 5_000_000, ["spd", "cv", "sc"])
        assert seg["id"] == "seg-1"
        assert seg["material_id"] == "mat-1"
        assert seg["target_timerange"]["duration"] == 5_000_000
        # extra_material_refs must contain all three we passed
        assert set(seg["extra_material_refs"]) == {"spd", "cv", "sc"}

    def test_text_segment_default_start_zero(self):
        seg = _build_text_segment("seg-t1", "mat-t1", 3_000_000)
        assert seg["target_timerange"]["start"] == 0
        assert seg["target_timerange"]["duration"] == 3_000_000

    def test_text_segment_with_start_offset(self):
        seg = _build_text_segment("seg-t2", "mat-t2", 2_500_000, 7_000_000)
        assert seg["target_timerange"]["start"] == 7_000_000


class TestBuildDraftContent:
    def test_bare_clip_has_video_track(self):
        draft = build_draft_content(_clip(duration=30.0))
        assert draft["duration"] == 30 * 1_000_000
        assert draft["canvas_config"] == {"width": 1080, "height": 1920, "ratio": "original"}
        # A video track is always present with exactly one segment
        video_tracks = [t for t in draft["tracks"] if t["type"] == "video"]
        assert len(video_tracks) == 1
        assert len(video_tracks[0]["segments"]) == 1

    def test_name_falls_back_to_filename(self):
        draft = build_draft_content(_clip(project_name=None))
        assert draft["name"] == "clip_demo.mp4"

    def test_project_name_overrides_filename(self):
        draft = build_draft_content(_clip(project_name="My Cool Project"))
        assert draft["name"] == "My Cool Project"

    def test_legacy_single_caption_creates_text_track(self):
        draft = build_draft_content(_clip(caption_text="Static caption"))
        text_tracks = [t for t in draft["tracks"] if t["type"] == "text"]
        assert len(text_tracks) == 1
        # Exactly one text segment, spanning the full clip
        segments = text_tracks[0]["segments"]
        assert len(segments) == 1
        assert segments[0]["target_timerange"]["start"] == 0

    def test_subtitle_segments_become_text_track(self):
        subs = [
            SubtitleSegment(text="line one", start_seconds=0.0, end_seconds=1.0),
            SubtitleSegment(text="line two", start_seconds=1.0, end_seconds=2.0),
        ]
        draft = build_draft_content(_clip(duration=3.0, subtitle_segments=subs))
        text_tracks = [t for t in draft["tracks"] if t["type"] == "text"]
        assert len(text_tracks) == 1
        assert len(text_tracks[0]["segments"]) == 2
        # Each subtitle produced exactly one text material
        assert len(draft["materials"]["texts"]) == 2

    def test_blank_subtitle_segments_skipped(self):
        subs = [
            SubtitleSegment(text="", start_seconds=0.0, end_seconds=1.0),
            SubtitleSegment(text="   ", start_seconds=1.0, end_seconds=2.0),
        ]
        draft = build_draft_content(_clip(subtitle_segments=subs))
        text_tracks = [t for t in draft["tracks"] if t["type"] == "text"]
        # All segments blank → no text track at all
        assert text_tracks == []

    def test_subtitle_with_zero_duration_gets_minimum_100ms(self):
        # start == end → builder enforces a floor duration
        subs = [SubtitleSegment(text="same ts", start_seconds=5.0, end_seconds=5.0)]
        draft = build_draft_content(_clip(subtitle_segments=subs))
        seg = draft["tracks"][-1]["segments"][0]
        assert seg["target_timerange"]["duration"] >= 100_000


class TestBuildDraftMetaInfo:
    def test_meta_fields(self):
        meta = build_draft_meta_info(
            project_id="proj-abc", project_name="My Draft", duration_seconds=15.0
        )
        # draft_id is upper-cased
        assert meta["draft_id"] == "PROJ-ABC"
        assert meta["draft_name"] == "My Draft"
        assert meta["tm_duration"] == 15_000_000
        assert meta["tm_draft_create"] > 0
        assert meta["tm_draft_modified"] == meta["tm_draft_create"]
