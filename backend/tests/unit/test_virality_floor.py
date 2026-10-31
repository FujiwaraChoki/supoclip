from src import ai
from src.config import Config
from src.services.video_service import VideoService


def test_config_clamps_min_virality(monkeypatch):
    for raw, want in [("150", 100), ("-5", 0), ("65", 65)]:
        monkeypatch.setenv("MIN_VIRALITY_SCORE", raw)
        assert Config().min_virality_score == want
    monkeypatch.delenv("MIN_VIRALITY_SCORE")
    assert Config().min_virality_score == 80


def test_floor_prefers_qualifying_segments_in_order():
    segs = [{"virality_score": v, "id": i} for i, v in enumerate([90, 79, 85, 80])]
    out = VideoService._apply_virality_floor(segs, 80, 3)
    assert [s["id"] for s in out] == [0, 2, 3]


def test_floor_fills_with_best_below_floor_up_to_limit():
    segs = [{"virality_score": v, "id": i} for i, v in enumerate([40, 92, 58, 51, 81])]
    out = VideoService._apply_virality_floor(segs, 80, 4)
    # 80%+ first (original order), then the best below-floor by score.
    assert [s["id"] for s in out] == [1, 4, 2, 3]


def test_floor_with_no_qualifying_segment_still_returns_best():
    segs = [{"virality_score": v, "id": i} for i, v in enumerate([40, 58, 51])]
    out = VideoService._apply_virality_floor(segs, 80, 5)
    assert [s["id"] for s in out] == [1, 2, 0]


def test_floor_never_exceeds_limit():
    segs = [{"virality_score": 90, "id": i} for i in range(8)]
    assert len(VideoService._apply_virality_floor(segs, 80, 5)) == 5
    assert len(VideoService._apply_virality_floor(segs, 0, 5)) == 5


def test_floor_empty_input_returns_empty():
    assert VideoService._apply_virality_floor([], 80, 5) == []


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
    assert ai.TRANSCRIPT_ANALYSIS_CACHE_VERSION == "windowed-rerank-v8-retention"


def test_system_prompt_does_not_cap_scores():
    prompt = ai.build_transcript_system_prompt("auto")
    assert "Score honestly" not in prompt
    assert "50-70 range" not in prompt
