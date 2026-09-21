"""
Pure-function tests for the folder-library rendering additions: the
"stacked" number overlay (build_ranking_overlay_ass), per-clip framing
(blur_fill/crop_fill/letterbox), and use_global_sfx's offset + end-aligned
instance. No DB/real ffmpeg involved — same mocked-ffmpeg convention as
test_ranking_templates_render.py; the filter-graph shape was additionally
sanity-checked against a real render by hand.
"""

from pathlib import Path
from unittest.mock import patch

from src.ranking_overlay import build_ranking_overlay_ass
from src.services.ranking_service import RankingService


def test_build_ranking_overlay_ass_marks_rank_one_gold_and_others_neutral():
    ranks = [
        {"rank": 3, "text": "third", "reveal_time": 0.0},
        {"rank": 2, "text": "second", "reveal_time": 5.0},
        {"rank": 1, "text": "first", "reveal_time": 10.0},
    ]
    _, events, _ = build_ranking_overlay_ass(ranks, 1080, 1920, 15.0)

    rank_one_tile = next(e for e in events if e.endswith("}1"))
    other_tiles = [e for e in events if e.endswith("}3") or e.endswith("}2")]
    # Gold (#FFD700) in ASS's &HAABBGGRR& form is &H0000D7FF& — case-insensitive.
    assert "d7ff" in rank_one_tile.lower()
    assert all("d7ff" not in tile.lower() for tile in other_tiles)


def test_build_ranking_overlay_ass_text_reveals_at_rank_start_and_persists():
    ranks = [
        {"rank": 2, "text": "second", "reveal_time": 0.0},
        {"rank": 1, "text": "first", "reveal_time": 5.0},
    ]
    _, events, _ = build_ranking_overlay_ass(ranks, 1080, 1920, 10.0)

    text_events = [e for e in events if "RankText" in e]
    assert any(e.startswith("Dialogue: 1,0:00:00.00,0:00:10.00") for e in text_events)
    assert any(e.startswith("Dialogue: 1,0:00:05.00,0:00:10.00") for e in text_events)


def test_build_ranking_overlay_ass_tiles_visible_for_whole_video_not_just_own_segment():
    ranks = [{"rank": 5, "text": "x", "reveal_time": 8.0}]
    _, events, _ = build_ranking_overlay_ass(ranks, 1080, 1920, 20.0)
    tile_event = next(e for e in events if "RankTile" in e)
    # The tile spans 0 -> total_duration regardless of this rank's own
    # reveal_time — "always visible", per spec, not per-segment.
    assert tile_event.startswith("Dialogue: 1,0:00:00.00,0:00:20.00")


class _FakeResult:
    returncode = 0
    stdout = ""
    stderr = ""


def _render_with_mocked_ffmpeg(inputs, **kwargs):
    commands = []

    def fake_run(command, timeout=1800):
        commands.append(command)
        Path(command[-1]).write_bytes(b"video")
        return _FakeResult()

    with (
        patch("src.services.ranking_service._resolve_local_path", side_effect=lambda p: Path(p)),
        patch("src.services.ranking_service.ffprobe_duration", return_value=2.0),
        patch("src.services.ranking_service.ffprobe_has_audio", return_value=True),
        patch("src.services.ranking_service.run_ffmpeg_command", side_effect=fake_run),
        patch("src.services.ranking_service.find_music_path", return_value=None),
    ):
        RankingService._render_compilation(
            inputs,
            720,
            1280,
            {"position": "center", "color": "teal", "animation": "bounce", "style": "stacked"},
            False,
            -14.0,
            Path("/tmp/does-not-matter.mp4"),
            False,
            None,
            None,
            0.22,
            kwargs.get("use_global_sfx", False),
            kwargs.get("default_framing", "blur_fill"),
        )
    return commands[0]


def _stacked_inputs(n, framing=None):
    inputs = [
        {
            "file_path": f"/tmp/{n}.mp4",
            "display_rank": i + 1,
            "rank_text": f"text {i + 1}",
        }
        for i, n in enumerate("abcdefgh"[:n])
    ]
    if framing:
        for item in inputs:
            item["framing"] = framing
    return inputs


class _FakeRankingConfig:
    ranking_sfx_filename = "whoosh-sfx-1.mp3"
    ranking_sfx_offset_pct = 20.0


def test_use_global_sfx_adds_offset_boundaries_plus_one_end_aligned_instance():
    inputs = _stacked_inputs(3)
    with patch("src.services.ranking_service.get_config", return_value=_FakeRankingConfig()):
        command = _render_with_mocked_ffmpeg(inputs, use_global_sfx=True)

    # 3 clips + 2 boundary instances + 1 end-aligned instance = 6 `-i` flags.
    assert command.count("-i") == 6
    filter_complex = command[command.index("-filter_complex") + 1]
    assert filter_complex.count("adelay=") == 3
    assert "amix=inputs=4" in filter_complex  # araw + 3 sfx copies


def test_use_global_sfx_with_no_configured_filename_stays_silent():
    class _NoSfxConfig:
        ranking_sfx_filename = None
        ranking_sfx_offset_pct = 10.0

    inputs = _stacked_inputs(3)
    with patch("src.services.ranking_service.get_config", return_value=_NoSfxConfig()):
        command = _render_with_mocked_ffmpeg(inputs, use_global_sfx=True)

    assert command.count("-i") == 3
    filter_complex = command[command.index("-filter_complex") + 1]
    assert "amix" not in filter_complex


def test_legacy_transition_sfx_path_is_unaffected_by_offset_logic():
    """use_global_sfx=False (every pre-existing template) must keep the
    original at-the-boundary-exactly timing — no offset, no end-aligned
    extra instance — so rapid_fire/countdown/ranking_list don't change."""
    inputs = [{"file_path": f"/tmp/{n}.mp4", "display_rank": i + 1} for i, n in enumerate("abc")]
    commands = []

    def fake_run(command, timeout=1800):
        commands.append(command)
        Path(command[-1]).write_bytes(b"video")
        return _FakeResult()

    with (
        patch("src.services.ranking_service._resolve_local_path", side_effect=lambda p: Path(p)),
        patch("src.services.ranking_service.ffprobe_duration", return_value=2.0),
        patch("src.services.ranking_service.ffprobe_has_audio", return_value=True),
        patch("src.services.ranking_service.run_ffmpeg_command", side_effect=fake_run),
        patch("src.services.ranking_service.find_music_path", return_value=None),
    ):
        RankingService._render_compilation(
            inputs,
            720,
            1280,
            {"position": "bottom", "color": "teal", "animation": "fade_pop"},
            False,
            -14.0,
            Path("/tmp/does-not-matter.mp4"),
            False,
            "whoosh-sfx-1.mp3",
            None,
            0.22,
            False,  # use_global_sfx
            "blur_fill",
        )
    command = commands[0]
    assert command.count("-i") == 5  # 3 clips + 2 boundary sfx, no end-aligned extra
    filter_complex = command[command.index("-filter_complex") + 1]
    assert filter_complex.count("adelay=") == 2


def test_blur_fill_is_default_framing():
    inputs = _stacked_inputs(2)
    command = _render_with_mocked_ffmpeg(inputs, default_framing="blur_fill")
    filter_complex = command[command.index("-filter_complex") + 1]
    assert "gblur" in filter_complex
    assert "split=2" in filter_complex


def test_crop_fill_framing_has_no_blur():
    inputs = _stacked_inputs(2, framing="crop_fill")
    command = _render_with_mocked_ffmpeg(inputs)
    filter_complex = command[command.index("-filter_complex") + 1]
    assert "gblur" not in filter_complex
    assert "crop=" in filter_complex


def test_letterbox_framing_matches_original_pad_behavior():
    inputs = _stacked_inputs(2, framing="letterbox")
    command = _render_with_mocked_ffmpeg(inputs)
    filter_complex = command[command.index("-filter_complex") + 1]
    assert "pad=" in filter_complex
    assert "gblur" not in filter_complex


def test_render_stacked_style_dispatches_to_ranking_overlay_without_crashing():
    inputs = _stacked_inputs(5)
    command = _render_with_mocked_ffmpeg(inputs)
    assert command[-1] == "/tmp/does-not-matter.mp4"
