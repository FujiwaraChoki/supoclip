import pytest

from src import ai
from src.config import Config
from src.services.video_service import VideoService


def test_config_clamps_min_virality(monkeypatch):
    for raw, want in [("150", 100), ("-5", 0), ("65", 65)]:
        monkeypatch.setenv("MIN_VIRALITY_SCORE", raw)
        assert Config().min_virality_score == want
    monkeypatch.delenv("MIN_VIRALITY_SCORE")
    assert Config().min_virality_score == 80


def test_floor_drops_low_and_keeps_order():
    segs = [{"virality_score": v, "id": i} for i, v in enumerate([90, 79, 85, 80])]
    out = VideoService._apply_virality_floor(segs, 80)
    assert [s["id"] for s in out] == [0, 2, 3]


def test_floor_zero_survivors_raises():
    with pytest.raises(ValueError, match="No moments scored at least 80% virality"):
        VideoService._apply_virality_floor([{"virality_score": 0}], 80)


def test_per_window_target_math():
    single = ai.build_transcript_analysis_prompt("[00:00 - 00:10] hi", clip_count=6)
    assert "Choose 10 segments total." in single
    multi = ai.build_transcript_analysis_prompt(
        "[00:00 - 00:10] hi", window=(0, 720), clip_count=10, window_count=3
    )
    assert "Choose 8 segments total." in multi


def test_system_prompt_has_retention_section_and_formats():
    prompt = ai.build_transcript_system_prompt("auto")
    assert "RETENTION-FIRST SELECTION" in prompt
    assert ai.TRANSCRIPT_ANALYSIS_CACHE_VERSION == "windowed-rerank-v7-retention"
