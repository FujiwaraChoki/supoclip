from unittest.mock import patch
from pathlib import Path

import pytest

from src import video_utils


def test_build_ass_subtitles_preserves_background_template(tmp_path):
    video_path = tmp_path / "source.mp4"
    video_path.with_suffix(".transcript_cache.json").write_text(
        """
        {
          "version": 2,
          "words": [
            {"text": "quiet", "start": 0, "end": 400, "confidence": 0.99},
            {"text": "caption", "start": 400, "end": 900, "confidence": 0.99}
          ],
          "utterances": [],
          "text": "quiet caption"
        }
        """,
        encoding="utf-8",
    )
    ass_path = tmp_path / "captions.ass"

    success = video_utils.build_assemblyai_ass_subtitles(
        video_path,
        clip_start=0.0,
        clip_end=1.0,
        video_width=1080,
        video_height=1920,
        output_ass_path=ass_path,
        caption_template="minimal",
        keep_ranges=[(0.0, 1.0)],
    )

    content = ass_path.read_text(encoding="utf-8")
    assert success is True
    assert "Style: Default" in content
    assert ",3," in content
    assert "\\fad(150,150)" in content


def test_prepare_audio_for_transcription_extracts_compact_mp3(tmp_path):
    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"video")
    commands = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, timeout=900):
        commands.append(command)
        Path(command[-1]).write_bytes(b"audio")
        return Result()

    with patch("src.video_utils.run_ffmpeg_command", side_effect=fake_run):
        audio_path = video_utils._prepare_audio_for_transcription(video_path)

    assert audio_path.name == "source.assemblyai.mp3"
    assert audio_path.read_bytes() == b"audio"
    assert commands[0][0] == "ffmpeg"
    assert "-vn" in commands[0]
    assert "64k" in commands[0]


def test_apply_transition_effect_builds_ffmpeg_xfade_command(tmp_path):
    commands = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, timeout=900):
        commands.append(command)
        return Result()

    with (
        patch("src.video_utils.ffprobe_duration", side_effect=[4.0, 4.0]),
        patch("src.video_utils.ffprobe_video_size", return_value=(1080, 1920)),
        patch("src.video_utils.run_ffmpeg_command", side_effect=fake_run),
    ):
        success = video_utils.apply_transition_effect(
            tmp_path / "clip1.mp4",
            tmp_path / "clip2.mp4",
            tmp_path / "transition.mp4",
            tmp_path / "out.mp4",
        )

    assert success is True
    command = commands[0]
    filter_graph = command[command.index("-filter_complex") + 1]
    assert "xfade=transition=fade:duration=1.500:offset=0" in filter_graph
    assert "trim=start=2.500:end=4.000" in filter_graph
    assert "trim=start=1.500:end=4.000" in filter_graph
    assert "-map" in command


def test_create_clips_with_transitions_keeps_standalone_clip_exports(tmp_path):
    clips_info = [{"filename": "clip-1.mp4", "path": str(tmp_path / "clip-1.mp4")}]

    with (
        patch(
            "src.video_utils.create_clips_from_segments", return_value=clips_info
        ) as mock_create,
        patch(
            "src.video_utils.get_available_transitions",
            side_effect=AssertionError("should not load transitions"),
        ),
    ):
        result = video_utils.create_clips_with_transitions(
            tmp_path / "source.mp4",
            [{"start_time": "00:00", "end_time": "00:10", "text": "hook"}],
            tmp_path,
            font_family="TikTokSans-Regular",
            font_size=24,
            font_color="#FFFFFF",
            caption_template="default",
            output_format="vertical",
            add_subtitles=True,
        )

    assert result == clips_info
    mock_create.assert_called_once()


def test_build_assemblyai_ass_subtitles_uses_cached_word_timings(tmp_path):
    video_path = tmp_path / "source.mp4"
    cache_path = video_path.with_suffix(".transcript_cache.json")
    cache_path.write_text(
        """
        {
          "version": 2,
          "words": [
            {"text": "hello", "start": 1000, "end": 1300, "confidence": 0.99},
            {"text": "world", "start": 1300, "end": 1800, "confidence": 0.99}
          ],
          "utterances": [],
          "text": "hello world"
        }
        """,
        encoding="utf-8",
    )

    ass_path = tmp_path / "captions.ass"
    success = video_utils.build_assemblyai_ass_subtitles(
        video_path,
        clip_start=1.0,
        clip_end=2.0,
        video_width=1080,
        video_height=1920,
        output_ass_path=ass_path,
        font_family="TikTokSans-Regular",
        font_size=32,
        font_color="#FFFFFF",
        caption_template="default",
        keep_ranges=[(1.0, 2.0)],
    )

    content = ass_path.read_text(encoding="utf-8")
    assert success is True
    assert "PlayResX: 1080" in content
    assert "hello world" in content
    assert "Dialogue:" in content


def test_ass_font_name_uses_internal_font_family():
    assert video_utils.ass_font_name("THEBOLDFONT") == "THE BOLD FONT (FREE VERSION)"


def test_extend_keep_ranges_finishes_nearby_sentence(tmp_path):
    video_path = tmp_path / "source.mp4"
    video_path.with_suffix(".transcript_cache.json").write_text(
        """
        {
          "version": 2,
          "words": [
            {"text": "One", "start": 15440, "end": 15760, "confidence": 0.99},
            {"text": "has", "start": 15760, "end": 16040, "confidence": 0.99},
            {"text": "depression", "start": 16040, "end": 16520, "confidence": 0.99},
            {"text": "and", "start": 16520, "end": 16680, "confidence": 0.99},
            {"text": "the", "start": 16680, "end": 16840, "confidence": 0.99},
            {"text": "other", "start": 16840, "end": 17080, "confidence": 0.99},
            {"text": "has", "start": 17080, "end": 17320, "confidence": 0.99},
            {"text": "anger", "start": 17320, "end": 17680, "confidence": 0.99},
            {"text": "issues.", "start": 17680, "end": 18000, "confidence": 0.99}
          ],
          "utterances": [],
          "text": "One has depression and the other has anger issues."
        }
        """,
        encoding="utf-8",
    )

    with patch("src.video_utils.ffprobe_duration", return_value=60.0):
        ranges = video_utils.extend_keep_ranges_to_sentence_boundary(
            video_path,
            [(0.0, 16.0)],
        )

    assert ranges[0][0] == 0.0
    assert ranges[0][1] == pytest.approx(18.35)


def test_burn_ass_subtitles_passes_fontsdir_to_ffmpeg(tmp_path):
    commands = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, timeout=900):
        commands.append(command)
        return Result()

    with patch("src.video_utils.run_ffmpeg_command", side_effect=fake_run):
        success = video_utils.burn_ass_subtitles_ffmpeg(
            tmp_path / "input.mp4",
            tmp_path / "captions.ass",
            tmp_path / "output.mp4",
            fonts_dir=tmp_path / "fonts",
        )

    assert success is True
    video_filter = commands[0][commands[0].index("-vf") + 1]
    assert "fontsdir=" in video_filter
    assert video_filter.endswith(",setsar=1")


def test_ass_font_name_uses_internal_font_family_for_libass():
    assert video_utils.ass_font_name("THEBOLDFONT") == "THE BOLD FONT (FREE VERSION)"


def test_burn_ass_subtitles_passes_selected_fonts_dir(tmp_path):
    commands = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, timeout=900):
        commands.append(command)
        return Result()

    with patch("src.video_utils.run_ffmpeg_command", side_effect=fake_run):
        success = video_utils.burn_ass_subtitles_ffmpeg(
            tmp_path / "input.mp4",
            tmp_path / "captions.ass",
            tmp_path / "output.mp4",
            tmp_path / "fonts",
        )

    assert success is True
    video_filter = commands[0][commands[0].index("-vf") + 1]
    assert f"fontsdir={tmp_path / 'fonts'}" in video_filter
    assert video_filter.endswith(",setsar=1")


def test_build_clip_signal_summary_surfaces_hook_and_audio_peak(monkeypatch, tmp_path):
    transcript = "\n".join(
        [
            "[00:00 - 00:04] Speaker A: This is normal setup",
            "[00:05 - 00:08] Speaker B: Wait what happened?",
            "[00:08 - 00:12] Speaker A: Nobody expected the result!",
        ]
    )
    monkeypatch.setattr(video_utils, "detect_audio_peak_times", lambda _path: [6.0])

    summary = video_utils.build_clip_signal_summary(tmp_path / "source.mp4", transcript)

    assert "Wait what happened?" in summary
    assert "audio energy peak" in summary
    assert "question/hook" in summary
