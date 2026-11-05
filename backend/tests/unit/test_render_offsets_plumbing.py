"""Explicit output offsets reach every consumer of a planned clip's timeline."""

import json
from types import SimpleNamespace

import pytest

from src import video_utils
from src.clip_source_map import load_clip_output_offsets, load_clip_source_ranges
from src.media import captions, speaker_framing
from src.services import video_service
from src.services.video_service import VideoService


def _words(*items):
    return [
        {"text": text, "start": int(start * 1000), "end": int(end * 1000), "confidence": 0.99, "speaker": "A"}
        for text, start, end in items
    ]


WORDS = _words(
    ("I", 20.0, 20.2), ("lost", 20.2, 20.5), ("forty", 20.5, 20.8), ("grand.", 20.8, 21.3),
    ("And", 30.0, 30.3), ("that", 30.3, 30.6), ("changed", 30.6, 31.0), ("everything.", 31.0, 31.6),
)
RANGES = [(29.9, 31.95), (19.9, 21.65)]   # out of source order, like a trailer
OFFSETS = [0.0, 2.01]                     # 0.04s declick at the junction


@pytest.fixture
def video_path(tmp_path, monkeypatch):
    path = tmp_path / "source.mp4"
    path.touch()
    path.with_suffix(".transcript_cache.json").write_text(
        json.dumps({"version": 2, "words": WORDS, "utterances": []}), encoding="utf-8"
    )
    monkeypatch.setattr("src.media.timeline.ffprobe_duration", lambda _path: 60.0)
    monkeypatch.setattr("src.media.ffmpeg.ffprobe_duration", lambda _path: 0.0)
    return path


@pytest.fixture
def render_spies(monkeypatch, tmp_path):
    """create_optimized_clip with every ffmpeg stage replaced by a recording spy."""
    calls = {}

    def record(name, result):
        def spy(*args, **kwargs):
            calls[name] = (args, kwargs)
            return result(*args, **kwargs) if callable(result) else result
        return spy

    def render_final(source, final_path, *_args, **_kwargs):
        final_path.write_bytes(b"clip")
        return True, 1080, 1920

    monkeypatch.setattr(video_utils, "get_config", lambda: SimpleNamespace(speaker_framing=True))
    monkeypatch.setattr(video_utils, "render_source_ranges_ffmpeg", record("render", True))
    monkeypatch.setattr(video_utils, "ffprobe_video_size", lambda _path: (1920, 1080))
    monkeypatch.setattr(video_utils, "build_clip_speaker_turns", record("speakers", ([], [])))
    monkeypatch.setattr(video_utils, "build_vertical_filter_plan", lambda *_a, **_k: ("crop", "fill", None))
    monkeypatch.setattr(video_utils, "detect_burned_caption_band", lambda _path: None)
    monkeypatch.setattr(video_utils, "build_assemblyai_ass_subtitles", record("captions", True))
    monkeypatch.setattr(video_utils, "render_reframed_clip_ffmpeg", render_final)
    return calls


def _render(video_path, tmp_path, output_offsets):
    kwargs = {} if output_offsets is None else {"output_offsets": output_offsets}
    return video_utils.create_optimized_clip(
        video_path, 19.9, 31.95, tmp_path / "out.mp4", True, None, None, None, "default", "vertical",
        RANGES, "A hook", extend_to_sentence=False, **kwargs,
    )


def test_offsets_reach_the_stitch_speaker_turns_and_captions(video_path, tmp_path, render_spies):
    assert _render(video_path, tmp_path, OFFSETS)

    assert render_spies["render"][1] == {"output_offsets": OFFSETS}
    assert render_spies["speakers"][1] == {"output_offsets": OFFSETS}
    assert render_spies["captions"][1]["output_offsets"] == OFFSETS


def test_legacy_render_calls_are_unchanged(video_path, tmp_path, render_spies):
    assert _render(video_path, tmp_path, None)

    assert render_spies["render"][1] == {}
    assert render_spies["speakers"][1] == {}
    assert "output_offsets" not in render_spies["captions"][1]


def test_mismatched_offsets_fail_the_render(video_path, tmp_path, render_spies):
    assert not _render(video_path, tmp_path, [0.0])
    assert "render" not in render_spies


def test_caption_words_follow_explicit_offsets(video_path, monkeypatch):
    seen = []
    real = captions.get_words_for_keep_ranges

    def spy(transcript, keep, offsets=None):
        seen.append(offsets)
        return real(transcript, keep, offsets)

    monkeypatch.setattr(captions, "get_words_for_keep_ranges", spy)
    captions.build_assemblyai_ass_subtitles(
        video_path, 19.9, 31.95, 1080, 1920, video_path.with_suffix(".ass"),
        keep_ranges=RANGES, output_offsets=OFFSETS,
    )

    assert seen == [OFFSETS]
    text = video_path.with_suffix(".ass").read_text(encoding="utf-8")
    # "I" plays right after the first beat: 2.01 + (20.0 - 19.9) = 2.11s.
    assert "0:00:02.11" in text


def test_speaker_turns_follow_explicit_offsets(video_path):
    _turns, words = speaker_framing.build_clip_speaker_turns(video_path, RANGES, output_offsets=OFFSETS)

    assert [word["text"] for word in words][:5] == ["And", "that", "changed", "everything.", "I"]
    assert words[4]["start"] == pytest.approx(2.11)


@pytest.mark.asyncio
async def test_broll_plan_uses_explicit_offsets(video_path, monkeypatch):
    planned = {}

    async def fake_plan(words, clip_seconds, *_args):
        planned["words"] = words
        planned["seconds"] = clip_seconds
        return []

    monkeypatch.setattr(video_service, "broll_configured", lambda: True)
    monkeypatch.setattr(video_service, "plan_clip_broll", fake_plan)

    await VideoService.plan_broll(video_path, RANGES, output_offsets=OFFSETS)

    assert planned["words"][4]["start"] == pytest.approx(2.11)
    assert planned["seconds"] == pytest.approx(2.01 + 1.75)


def _planned_segment(**extra):
    return {
        "start_time": "00:29", "end_time": "00:31", "text": "Planner text.", "relevance_score": 1.0,
        "reasoning": "", "planned": True, "source_ranges": [list(item) for item in RANGES],
        "junction_fades": [0.04], "max_seconds": 45.0, "hook_title": "Watch this", **extra,
    }


@pytest.mark.asyncio
async def test_single_clip_render_passes_offsets_everywhere(video_path, tmp_path, monkeypatch):
    seen = {}

    def fake_create(*args, **kwargs):
        seen["create"] = (args, kwargs)
        args[3].touch()
        return True

    async def fake_broll(*args, **kwargs):
        seen["broll"] = kwargs
        return []

    monkeypatch.setattr(video_service, "create_optimized_clip", fake_create)
    monkeypatch.setattr(VideoService, "plan_broll", staticmethod(fake_broll))

    info = await VideoService.create_single_clip(
        video_path, _planned_segment(), 0, tmp_path, max_clip_seconds=30.0, include_broll=True,
    )

    assert seen["create"][0][10] == RANGES
    assert seen["create"][1]["output_offsets"] == [0.0, pytest.approx(2.01)]
    assert seen["broll"]["output_offsets"] == [0.0, pytest.approx(2.01)]
    clip_path = tmp_path / info["filename"]
    assert load_clip_source_ranges(clip_path) == RANGES
    assert load_clip_output_offsets(clip_path, RANGES) == [0.0, pytest.approx(2.01)]
    assert info["group_output_starts"] == [0.0, pytest.approx(2.01)]
    assert info["duration"] == pytest.approx(2.01 + 1.75)
    assert info["text"] == "And that changed everything. I lost forty grand."


def test_batch_render_uses_each_planned_segment_cap_and_offsets(video_path, tmp_path, monkeypatch):
    seen = []

    def fake_create(*args, **kwargs):
        seen.append((args[10], kwargs.get("output_offsets")))
        args[3].touch()
        return True

    monkeypatch.setattr(video_utils, "create_optimized_clip", fake_create)

    clips = video_utils.create_clips_from_segments(
        video_path, [_planned_segment(max_seconds=90.0)], tmp_path, max_clip_seconds=3.0,
    )

    # The 3s batch cap would cut a 90s recap; the planned cap wins.
    assert seen == [(RANGES, [0.0, pytest.approx(2.01)])]
    assert load_clip_output_offsets(tmp_path / clips[0]["filename"], RANGES) == [0.0, pytest.approx(2.01)]
    assert clips[0]["group_output_starts"] == [0.0, pytest.approx(2.01)]


def test_caption_lines_never_straddle_a_planned_jump(video_path):
    transcript = json.loads(video_path.with_suffix(".transcript_cache.json").read_text())

    # The second beat jumps back 10 seconds: its words start a new line.
    assert captions._passage_breaks(transcript, RANGES) == [4]
    # Cleanup cuts inside continuous speech are not passages.
    assert captions._passage_breaks(transcript, [(29.9, 30.62), (30.6, 31.95)]) == []
    assert list(captions._chunk_bounds(8, 3, [4])) == [(0, 3), (3, 4), (4, 7), (7, 8)]
    # Without breaks the lines are the original fixed-size chunks.
    assert list(captions._chunk_bounds(8, 3, [])) == [(0, 3), (3, 6), (6, 8)]


def test_caption_ass_breaks_the_line_at_the_beat_change(video_path):
    path = video_path.with_suffix(".ass")
    captions.build_assemblyai_ass_subtitles(
        video_path, 19.9, 31.95, 1080, 1920, path, keep_ranges=RANGES, output_offsets=OFFSETS,
    )
    dialogue = [line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("Dialogue")]

    assert not any("EVERYTHING" in line.upper() and "LOST" in line.upper() for line in dialogue)
