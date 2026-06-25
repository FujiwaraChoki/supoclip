import json
from types import SimpleNamespace

import pytest

from src.services import video_service as video_service_module
from src.services.video_service import VideoService


class _EmptyAnalysis:
    summary = "No strong standalone segment found"
    key_topics = []
    most_relevant_segments = []


@pytest.mark.asyncio
async def test_process_video_complete_uses_fallback_when_ai_selects_no_segments(
    monkeypatch, tmp_path
):
    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"placeholder")

    config = SimpleNamespace(
        max_video_duration=5400,
        clip_duration=30,
        fast_mode_max_clips=4,
    )
    monkeypatch.setattr(video_service_module, "get_config", lambda: config)
    monkeypatch.setattr(
        VideoService,
        "resolve_local_video_path",
        staticmethod(lambda _url: video_path),
    )
    monkeypatch.setattr(
        VideoService,
        "_get_file_duration",
        staticmethod(lambda _path: 42.0),
    )

    async def fake_generate_transcript(_video_path, processing_mode="balanced"):
        return "[00:00 - 00:01] hello"

    async def fake_analyze_transcript(_transcript, clip_signals=None):
        return _EmptyAnalysis()

    async def fake_run_in_thread(_func, *_args, **_kwargs):
        return None

    monkeypatch.setattr(
        VideoService,
        "generate_transcript",
        staticmethod(fake_generate_transcript),
    )
    monkeypatch.setattr(
        VideoService,
        "analyze_transcript",
        staticmethod(fake_analyze_transcript),
    )
    monkeypatch.setattr(video_service_module, "run_in_thread", fake_run_in_thread)

    result = await VideoService.process_video_complete(
        url="upload://source.mp4",
        source_type="video_url",
        processing_mode="fast",
    )

    assert result["segments_to_render"] == [
        {
            "start_time": "00:00",
            "end_time": "00:30",
            "text": "[00:00 - 00:01] hello",
            "relevance_score": 0.25,
            "reasoning": (
                "AI analysis did not identify a strong standalone segment, "
                "so SupoClip generated the first available portion of the video."
            ),
            "virality_score": 0,
            "hook_score": 0,
            "engagement_score": 0,
            "value_score": 0,
            "shareability_score": 0,
            "hook_type": "fallback",
        }
    ]
    analysis = json.loads(result["analysis_json"])
    assert analysis["most_relevant_segments"] == result["segments_to_render"]


def test_fallback_segment_caps_to_video_duration():
    segment = VideoService._build_fallback_segment(
        video_duration=12.0,
        transcript="short transcript",
        target_duration=30,
    )

    assert segment["start_time"] == "00:00"
    assert segment["end_time"] == "00:12"
    assert segment["hook_type"] == "fallback"
