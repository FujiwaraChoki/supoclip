"""
Pure-function tests for the Countdown/Ranking List templates' render-order
and overlay logic — no DB/ffmpeg involved, mirrors test_clip_cleanup_sensitivity.py's
style. See ranking_templates.py's TEMPLATE_DEFAULTS and ranking_service.py's
process_ranking_complete for what these functions feed into. The
transition-SFX filter-graph construction below is command-shape-only (mocked
ffmpeg, per test_video_utils_effects.py's convention) — it was additionally
verified against a real ffmpeg render by hand (see docs/architecture.md's
Ranking tool section) since a malformed filter graph can pass a mocked
assertion while still failing for real.
"""

from pathlib import Path
from unittest.mock import patch

from src.ranking_overlay import build_rank_list_ass, build_rank_number_ass
from src.ranking_templates import get_all_templates, get_template
from src.services.ranking_service import RankingService


def _inputs(n):
    return [
        {"id": str(i), "rank_position": None, "file_path": f"upload://{i}.mp4"}
        for i in range(n)
    ]


def test_ascending_template_keeps_insertion_order_as_display_rank():
    resolved = RankingService._resolve_ranks(_inputs(3))
    assert [r["display_rank"] for r in resolved] == [1, 2, 3]


def test_manual_rank_override_replaces_sequential_position():
    # RankingRepository.list_inputs already sorts manual ranks first; this
    # only checks _resolve_ranks's per-item digit logic in isolation.
    inputs = _inputs(3)
    inputs[1]["rank_position"] = 1
    resolved = RankingService._resolve_ranks(inputs)
    assert resolved[0]["display_rank"] == 1  # no override -> its index (0) + 1
    assert resolved[1]["display_rank"] == 1  # manual override
    assert resolved[2]["display_rank"] == 3  # no override -> its index (2) + 1


def test_countdown_and_ranking_list_ship_with_descending_render_order():
    countdown = get_template("countdown")
    ranking_list = get_template("ranking_list")
    rapid_fire = get_template("rapid_fire")

    assert countdown["render_order"] == "descending"
    assert ranking_list["render_order"] == "descending"
    assert rapid_fire["render_order"] == "ascending"

    assert ranking_list["list_overlay"] is True
    assert countdown["list_overlay"] is False


def test_descending_render_order_reverses_playback_sequence_not_ranks():
    resolved = RankingService._resolve_ranks(_inputs(3))  # display ranks 1,2,3
    reversed_for_render = list(reversed(resolved))
    assert [r["display_rank"] for r in reversed_for_render] == [3, 2, 1]


def test_all_shipped_templates_load_without_error():
    templates = get_all_templates()
    assert {"rapid_fire", "countdown", "ranking_list"} <= set(templates.keys())
    for template in templates.values():
        assert template["render_order"] in ("ascending", "descending")
        assert isinstance(template["list_overlay"], bool)


def test_rank_number_ass_produces_one_style_and_one_dialogue_event():
    style, events = build_rank_number_ass(
        rank=1,
        number_overlay={"position": "bottom", "color": "teal", "animation": "fade_pop"},
        video_width=1080,
        video_height=1920,
        segment_start=0.0,
        segment_duration=4.0,
    )
    assert style.startswith("Style: Rank,")
    assert len(events) == 1
    assert "0:00:00.00" in events[0]
    assert "0:00:04.00" in events[0]


def test_rank_list_ass_accumulates_revealed_ranks_in_render_order():
    _, first_segment_events = build_rank_list_ass(
        revealed_ranks=[3],
        number_overlay={"position": "bottom"},
        video_width=1080,
        video_height=1920,
        segment_start=0.0,
        segment_duration=5.0,
    )
    _, second_segment_events = build_rank_list_ass(
        revealed_ranks=[3, 2],
        number_overlay={"position": "bottom"},
        video_width=1080,
        video_height=1920,
        segment_start=5.0,
        segment_duration=5.0,
    )
    assert "#3" in first_segment_events[0]
    assert "#2" not in first_segment_events[0]
    assert "#3" in second_segment_events[0] and "#2" in second_segment_events[0]


class _FakeResult:
    returncode = 0
    stdout = ""
    stderr = ""


def _render_with_mocked_ffmpeg(ordered_inputs, transition_sfx=None, background_music=None):
    commands = []

    def fake_run(command, timeout=1800):
        commands.append(command)
        Path(command[-1]).write_bytes(b"video")
        return _FakeResult()

    # backend/music/ ships empty by design (see backend/music/README.md), so
    # a resolvable music bed is faked here rather than requiring a fixture
    # file in that shipped-empty directory.
    fake_music_path = Path("/tmp/fake-music.mp3") if background_music else None
    with (
        patch("src.services.ranking_service._resolve_local_path", side_effect=lambda p: Path(p)),
        patch("src.services.ranking_service.ffprobe_duration", return_value=2.0),
        patch("src.services.ranking_service.ffprobe_has_audio", return_value=True),
        patch("src.services.ranking_service.run_ffmpeg_command", side_effect=fake_run),
        patch("src.services.ranking_service.find_music_path", return_value=fake_music_path),
    ):
        RankingService._render_compilation(
            ordered_inputs,
            720,
            1280,
            {"position": "bottom", "color": "teal", "animation": "fade_pop"},
            False,
            -14.0,
            Path("/tmp/does-not-matter.mp4"),
            False,
            transition_sfx,
            background_music,
        )
    return commands[0]


def test_transition_sfx_adds_one_extra_input_and_amix_step_per_boundary():
    inputs = [{"file_path": f"/tmp/{n}.mp4", "display_rank": i + 1} for i, n in enumerate("abc")]
    command = _render_with_mocked_ffmpeg(inputs, "whoosh-sfx-1.mp3")

    # 3 clip inputs + 2 boundary-sfx inputs (whoosh-sfx-1.mp3 ships in
    # backend/sfx/, so find_sfx_path resolves it) = 5 `-i` flags.
    assert command.count("-i") == 5
    filter_complex = command[command.index("-filter_complex") + 1]
    assert filter_complex.count("adelay=") == 2
    assert "amix=inputs=3" in filter_complex  # araw + 2 sfx copies
    assert "[araw_sfx]" in filter_complex


def test_no_transition_sfx_by_default_means_no_amix_step():
    inputs = [{"file_path": f"/tmp/{n}.mp4", "display_rank": i + 1} for i, n in enumerate("abc")]
    command = _render_with_mocked_ffmpeg(inputs, None)

    assert command.count("-i") == 3  # just the 3 clip inputs
    filter_complex = command[command.index("-filter_complex") + 1]
    assert "amix" not in filter_complex
    assert "[araw]" in filter_complex


def test_unknown_transition_sfx_name_degrades_to_no_sfx_not_a_crash():
    inputs = [{"file_path": f"/tmp/{n}.mp4", "display_rank": i + 1} for i, n in enumerate("abc")]
    # find_sfx_path returns None for a name that isn't in backend/sfx/ —
    # a bad/renamed template config.json shouldn't break rendering.
    command = _render_with_mocked_ffmpeg(inputs, "does-not-exist.mp3")

    assert command.count("-i") == 3
    filter_complex = command[command.index("-filter_complex") + 1]
    assert "amix" not in filter_complex


def test_background_music_loops_ducks_and_mixes_with_asplit():
    inputs = [{"file_path": f"/tmp/{n}.mp4", "display_rank": i + 1} for i, n in enumerate("abc")]
    command = _render_with_mocked_ffmpeg(inputs, background_music="bed.mp3")

    assert "-stream_loop" in command
    assert command[command.index("-stream_loop") + 1] == "-1"
    assert command.count("-i") == 4  # 3 clips + 1 looped music bed

    filter_complex = command[command.index("-filter_complex") + 1]
    # The dialogue/sfx track feeds both the sidechain key and the final mix,
    # so it must be split, never referenced twice as a bare label — see the
    # ranking_service.py comment on why (ffmpeg errors "Invalid stream
    # specifier" otherwise, caught by hand against a real ffmpeg run).
    assert "asplit=2" in filter_complex
    assert "sidechaincompress" in filter_complex
    assert "[araw_music]" in filter_complex


def test_background_music_and_transition_sfx_compose_without_double_consuming_araw():
    inputs = [{"file_path": f"/tmp/{n}.mp4", "display_rank": i + 1} for i, n in enumerate("abc")]
    command = _render_with_mocked_ffmpeg(
        inputs, transition_sfx="whoosh-sfx-1.mp3", background_music="bed.mp3"
    )

    # 3 clips + 2 sfx (one per boundary) + 1 music bed.
    assert command.count("-i") == 6
    filter_complex = command[command.index("-filter_complex") + 1]
    assert "[araw_sfx]" in filter_complex  # sfx stage ran first
    assert "asplit=2" in filter_complex  # music stage split araw_sfx, not araw
    assert "[araw_music]" in filter_complex


def test_no_background_music_by_default_means_no_stream_loop():
    inputs = [{"file_path": f"/tmp/{n}.mp4", "display_rank": i + 1} for i, n in enumerate("abc")]
    command = _render_with_mocked_ffmpeg(inputs)

    assert "-stream_loop" not in command
    filter_complex = command[command.index("-filter_complex") + 1]
    assert "sidechaincompress" not in filter_complex


def test_rank_list_placement_avoids_top_tile_by_using_bottom_left():
    style, _ = build_rank_list_ass(
        revealed_ranks=[1],
        number_overlay={"position": "top"},
        video_width=1080,
        video_height=1920,
        segment_start=0.0,
        segment_duration=4.0,
    )
    # Alignment is the 19th comma-separated field (0-indexed 18) in an ASS
    # "Style:" line — see the Format header in ranking_service.py's ASS
    # header and ranking_overlay.py's V4+ Styles field order.
    alignment = style.split(",")[18]
    assert alignment == "1"  # bottom-left, not top-left, when the tile is on top
