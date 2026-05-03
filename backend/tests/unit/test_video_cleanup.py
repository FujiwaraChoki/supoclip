from pathlib import Path

import pytest

from src.services.video_service import VideoService
from src.clip_source_map import load_clip_source_ranges, save_clip_source_ranges
from src.video_utils import (
    build_clip_keep_ranges,
    build_keep_ranges_from_source_ranges,
    create_optimized_clip,
    create_clips_from_segments,
    get_words_for_keep_ranges,
)


def test_build_clip_keep_ranges_removes_pauses_and_filler_words(monkeypatch):
    transcript_data = {
        "words": [
            {"text": "Hey", "start": 0, "end": 300},
            {"text": "um", "start": 300, "end": 500},
            {"text": "let's", "start": 1700, "end": 2100},
            {"text": "go", "start": 2100, "end": 2400},
        ]
    }
    monkeypatch.setattr(
        "src.video_utils.load_cached_transcript_data",
        lambda _video_path: transcript_data,
    )

    keep_ranges = build_clip_keep_ranges(
        Path("/tmp/demo.mp4"),
        0.0,
        2.4,
        {
            "cut_long_pauses": True,
            "pause_threshold_ms": 900,
            "remove_filler_words": True,
            "filtered_words": [],
        },
    )

    assert keep_ranges == [(0.0, 0.3), (1.7, 2.4)]


def test_build_clip_keep_ranges_removes_boundary_silence(monkeypatch):
    transcript_data = {
        "words": [
            {"text": "Hello", "start": 1000, "end": 1300},
            {"text": "there", "start": 1300, "end": 1600},
        ]
    }
    monkeypatch.setattr(
        "src.video_utils.load_cached_transcript_data",
        lambda _video_path: transcript_data,
    )

    keep_ranges = build_clip_keep_ranges(
        Path("/tmp/demo.mp4"),
        0.0,
        2.8,
        {
            "cut_long_pauses": True,
            "pause_threshold_ms": 900,
            "remove_filler_words": False,
            "filtered_words": [],
        },
    )

    assert keep_ranges == [(1.0, 1.6)]


def test_get_words_for_keep_ranges_retimes_words_into_output_timeline():
    transcript_data = {
        "words": [
            {"text": "first", "start": 0, "end": 300},
            {"text": "second", "start": 1000, "end": 1300},
            {"text": "third", "start": 1300, "end": 1600},
        ]
    }

    words = get_words_for_keep_ranges(
        transcript_data,
        [(0.0, 0.3), (1.0, 1.6)],
    )

    assert [word["text"] for word in words] == ["first", "second", "third"]
    assert [word["confidence"] for word in words] == [1.0, 1.0, 1.0]
    assert [word["start"] for word in words] == pytest.approx([0.0, 0.3, 0.6])
    assert [word["end"] for word in words] == pytest.approx([0.3, 0.6, 0.9])


def test_build_keep_ranges_from_source_ranges_recomputes_each_range(monkeypatch):
    calls: list[tuple[float, float]] = []

    def fake_build_clip_keep_ranges(_video_path, clip_start, clip_end, _settings):
        calls.append((clip_start, clip_end))
        return [(clip_start + 0.1, clip_end - 0.1)]

    monkeypatch.setattr(
        "src.video_utils.build_clip_keep_ranges",
        fake_build_clip_keep_ranges,
    )

    keep_ranges = build_keep_ranges_from_source_ranges(
        Path("/tmp/demo.mp4"),
        [(10.0, 11.0), (13.0, 15.0)],
        {"cut_long_pauses": True},
    )

    assert calls == [(10.0, 11.0), (13.0, 15.0)]
    assert keep_ranges == [(10.1, 10.9), (13.1, 14.9)]


@pytest.mark.asyncio
async def test_create_single_clip_keeps_timing_fields_consistent(monkeypatch, tmp_path):
    async def fake_run_in_thread(_fn, *_args, **_kwargs):
        return True

    monkeypatch.setattr("src.services.video_service.run_in_thread", fake_run_in_thread)
    monkeypatch.setattr(
        "src.services.video_service.build_clip_keep_ranges",
        lambda *_args, **_kwargs: [(10.0, 11.0), (12.0, 14.0)],
    )

    clip = await VideoService.create_single_clip(
        video_path=Path("/tmp/demo.mp4"),
        segment={"start_time": "00:10", "end_time": "00:20", "text": "hello"},
        clip_index=0,
        output_dir=tmp_path,
        add_subtitles=False,
        cleanup_settings={"cut_long_pauses": True},
    )

    assert clip is not None
    assert clip["duration"] == pytest.approx(3.0)
    assert clip["start_time"] == "00:10"
    assert clip["end_time"] == "00:20"


def test_create_clips_from_segments_keeps_timing_fields_consistent(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "src.video_utils.build_clip_keep_ranges",
        lambda *_args, **_kwargs: [(10.0, 11.0), (12.0, 14.0)],
    )
    monkeypatch.setattr("src.video_utils.create_optimized_clip", lambda *_args, **_kwargs: True)

    clips = create_clips_from_segments(
        video_path=Path("/tmp/demo.mp4"),
        segments=[
            {
                "start_time": "00:10",
                "end_time": "00:20",
                "text": "hello",
                "relevance_score": 0.8,
                "reasoning": "test",
            }
        ],
        output_dir=tmp_path,
        add_subtitles=False,
        cleanup_settings={"cut_long_pauses": True},
    )

    assert len(clips) == 1
    assert clips[0]["duration"] == pytest.approx(3.0)
    assert clips[0]["start_time"] == "00:10"
    assert clips[0]["end_time"] == "00:20"


def test_create_clips_from_segments_uses_source_ranges_when_recomputing_cleanup(
    monkeypatch, tmp_path
):
    captured: dict[str, object] = {}

    def fake_build_keep_ranges_from_source_ranges(
        _video_path, source_ranges, cleanup_settings
    ):
        captured["source_ranges"] = source_ranges
        captured["cleanup_settings"] = cleanup_settings
        return [(10.1, 10.9), (13.1, 14.9)]

    monkeypatch.setattr(
        "src.video_utils.build_keep_ranges_from_source_ranges",
        fake_build_keep_ranges_from_source_ranges,
    )
    monkeypatch.setattr(
        "src.video_utils.create_optimized_clip", lambda *_args, **_kwargs: True
    )

    clips = create_clips_from_segments(
        video_path=Path("/tmp/demo.mp4"),
        segments=[
            {
                "start_time": "00:10",
                "end_time": "00:15",
                "source_ranges": [(10.0, 11.0), (13.0, 15.0)],
                "text": "hello",
                "relevance_score": 0.8,
                "reasoning": "test",
            }
        ],
        output_dir=tmp_path,
        add_subtitles=False,
        cleanup_settings={"cut_long_pauses": True},
    )

    assert len(clips) == 1
    assert captured["source_ranges"] == [(10.0, 11.0), (13.0, 15.0)]
    assert captured["cleanup_settings"] == {"cut_long_pauses": True}
    assert clips[0]["duration"] == pytest.approx(2.6)


@pytest.mark.asyncio
async def test_apply_single_transition_copies_clip_source_map(monkeypatch, tmp_path):
    current_clip_path = tmp_path / "clip.mp4"
    current_clip_path.write_bytes(b"clip")
    save_clip_source_ranges(current_clip_path, [(10.0, 11.0), (12.0, 14.0)])

    clip_info = await VideoService.apply_single_transition(
        prev_clip_path=tmp_path / "prev.mp4",
        current_clip_info={
            "filename": "clip.mp4",
            "path": str(current_clip_path),
        },
        clip_index=1,
        output_dir=tmp_path,
    )

    transitioned_path = Path(clip_info["path"])
    assert load_clip_source_ranges(transitioned_path) == [(10.0, 11.0), (12.0, 14.0)]


def test_create_optimized_clip_fast_path_uses_keep_range_start(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    class _CompletedProcess:
        returncode = 0
        stderr = ""

    def fake_run(command, **_kwargs):
        captured["command"] = command
        return _CompletedProcess()

    monkeypatch.setattr("subprocess.run", fake_run)

    success = create_optimized_clip(
        video_path=Path("/tmp/demo.mp4"),
        start_time=10.0,
        end_time=20.0,
        output_path=tmp_path / "clip.mp4",
        add_subtitles=False,
        output_format="original",
        keep_ranges=[(10.5, 20.0)],
    )

    assert success is True
    command = captured["command"]
    assert command[command.index("-ss") + 1] == "10.5"
    assert command[command.index("-t") + 1] == "9.5"
