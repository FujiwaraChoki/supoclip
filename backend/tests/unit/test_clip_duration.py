"""Exercise duration presets from AI selection to rendered source ranges."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src import ai, video_utils
from src.clip_duration import CLIP_DURATIONS, clip_duration, normalize_clip_length, validate_clip_length
from src.services.task_service import TaskService
from src.services.video_service import VideoService
from src.services import video_service
from src.clip_source_map import load_clip_source_ranges


def transcript(seconds=1800):
    def label(value):
        return f"{value // 60:02d}:{value % 60:02d}"
    return "\n".join(
        f"[{label(start)} - {label(start + 10)}] Useful context and a complete thought."
        for start in range(0, seconds, 10)
    )


@pytest.mark.parametrize("preset,minimum,maximum", [
    ("auto", 15, 60), ("short", 15, 30), ("medium", 30, 60), ("long", 60, 120),
])
def test_prompts_agree_on_requested_range(preset, minimum, maximum):
    system = ai.build_transcript_system_prompt(preset)
    prompt = ai.build_transcript_analysis_prompt("transcript", clip_length=preset)
    assert f"must be {minimum}-{maximum} seconds" in system
    assert f"shorter than {minimum} seconds or longer than {maximum} seconds" in prompt
    assert "{minimum}" not in system
    if preset == "long":
        assert "15-60 seconds" not in system
        assert "25-50 seconds" not in prompt


@pytest.mark.parametrize("value", [None, True, 30, "", "LONG", "custom", [], {}])
def test_unknown_presets_are_rejected(value):
    with pytest.raises(ValueError):
        validate_clip_length(value)


@pytest.mark.parametrize("preset,start,end", [
    ("short", "00:00", "01:30"),  # repair an overlong model result
    ("medium", "00:30", "00:40"),  # expand surrounding context
    ("long", "00:30", "01:00"),
    ("long", "00:00", "01:30"),  # keep a 90s pick rather than truncating to 60s
])
def test_validation_and_repair_use_the_requested_bounds(preset, start, end):
    text = transcript(180)
    result = ai._validate_segments(
        [ai.TranscriptSegment(start_time=start, end_time=end, text="Useful context and thought.")],
        ai._parse_transcript_lines(text), ai._parse_transcript_spans(text), preset,
    )
    assert len(result) == 1
    start_seconds, end_seconds = ai._segment_bounds_seconds(result[0])
    bounds = clip_duration(preset)
    assert bounds.minimum <= end_seconds - start_seconds <= bounds.maximum
    if start == "00:00" and preset == "long":
        assert end_seconds - start_seconds == 90


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds", [180, 1800])
async def test_single_and_windowed_analysis_keep_long_clips(monkeypatch, seconds):
    agent = object()
    monkeypatch.setattr(ai, "get_transcript_agent", lambda preset: agent)
    async def analyze(_agent, prompt):
        assert "longer than 120 seconds" in prompt
        return SimpleNamespace(output=ai.TranscriptAnalysis(
            most_relevant_segments=[ai.TranscriptSegment(
                start_time="00:00", end_time="01:30", text="Useful context and a complete thought.",
            )], summary="Summary", key_topics=[],
        ))
    monkeypatch.setattr(ai, "_run_transcript_analysis", analyze)
    monkeypatch.setattr(ai, "rerank_candidates", AsyncMock(side_effect=lambda segments, _: segments))
    result = await ai.get_most_relevant_parts_by_transcript(transcript(seconds), clip_length="long")
    assert len(result.most_relevant_segments) == 1
    assert ai._segment_bounds_seconds(result.most_relevant_segments[0]) == (0, 90)


@pytest.mark.parametrize("value", [None, True, 30, "", "LONG", "custom", [], {}])
def test_stored_unknown_presets_fall_back_to_auto(value):
    assert normalize_clip_length(value) == "auto"
    assert normalize_clip_length("long") == "long"


@pytest.mark.parametrize("preset", list(CLIP_DURATIONS))
def test_every_preset_leaves_room_to_finish_a_sentence(preset):
    bounds = clip_duration(preset)
    assert bounds.ideal_maximum <= bounds.maximum - 5


def test_analysis_cache_is_separate_for_every_preset():
    source_key = TaskService._build_cache_key("upload://talk.mp4", "video_url", "fast")
    keys = [TaskService._build_analysis_cache_key(source_key, preset)
            for preset in ("auto", "short", "medium", "long")]
    assert keys[0] == source_key
    assert len(set(keys)) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("preset,expected_end", [("short", 30), ("medium", 60), ("long", 120)])
async def test_single_and_batch_rendering_respect_caps(tmp_path, monkeypatch, preset, expected_end):
    source = tmp_path / "source.mp4"
    source.touch()
    segment = {"start_time": "00:00", "end_time": "02:10", "text": "Useful spoken content.", "relevance_score": 0.8, "reasoning": "A complete thought."}
    rendered = []
    def render(*args, **kwargs):
        rendered.append(args[10])
        assert kwargs["extend_to_sentence"] is False
        args[3].touch()
        return True
    monkeypatch.setattr(video_utils, "create_optimized_clip", render)
    monkeypatch.setattr(video_service, "create_optimized_clip", render)
    cap = clip_duration(preset).maximum
    single = await VideoService.create_single_clip(source, segment, 0, tmp_path, max_clip_seconds=cap)
    batch = video_utils.create_clips_from_segments(source, [segment], tmp_path, max_clip_seconds=cap)
    assert single is not None and len(batch) == 1
    assert rendered == [[(0, expected_end)], [(0, expected_end)]]
    for info in [single, batch[0]]:
        assert info["duration"] == expected_end
        assert load_clip_source_ranges(tmp_path / info["filename"]) == [(0, expected_end)]


@pytest.mark.asyncio
async def test_regenerated_ranges_keep_the_stored_clip_text(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.touch()
    segment = {"start_time": "00:00", "end_time": "00:20", "keep_ranges": [(0, 20)], "text": "Edited caption text.", "relevance_score": 0.8, "reasoning": "Merged by the user."}
    def render(*args, **kwargs):
        args[3].touch()
        return True
    monkeypatch.setattr(video_utils, "create_optimized_clip", render)
    monkeypatch.setattr(video_service, "create_optimized_clip", render)
    monkeypatch.setattr(video_utils, "keep_ranges_transcript_text", lambda *_: "Source words.")
    monkeypatch.setattr(video_service, "keep_ranges_transcript_text", lambda *_: "Source words.")
    single = await VideoService.create_single_clip(source, segment, 0, tmp_path)
    batch = video_utils.create_clips_from_segments(source, [segment], tmp_path)
    assert single["text"] == batch[0]["text"] == "Edited caption text."
