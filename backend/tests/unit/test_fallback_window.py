"""The clip a video without speech gets: its first chapter between title cards."""

import shutil
import subprocess

import pytest

from src.media import fallback_window as fw
from src.services.video_service import VideoService

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _video(tmp_path, parts):
    """``parts`` is a list of ("card" | "scene", seconds); cards are dark with a line of text-like bars."""
    inputs, labels = [], []
    for index, (kind, seconds) in enumerate(parts):
        source = (f"color=c=0x202020:s=320x180:r=8:d={seconds},drawbox=x=80:y=84:w=160:h=12:color=white:t=fill"
                  if kind == "card" else f"testsrc2=s=320x180:r=8:d={seconds}")
        inputs += ["-f", "lavfi", "-i", source]
        labels.append(f"[{index}:v]")
    path = tmp_path / "chapters.mp4"
    run = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex",
         f"{''.join(labels)}concat=n={len(parts)}:v=1:a=0[v]", "-map", "[v]",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, text=True,
    )
    assert run.returncode == 0, run.stderr
    return path


def test_a_video_cut_into_chapters_gives_its_first_whole_chapter(tmp_path):
    # Intro, card, a chapter of two scenes, card, the next chapter.
    video = _video(tmp_path, [("scene", 5), ("card", 2), ("scene", 20), ("scene", 18), ("card", 2), ("scene", 30)])
    start, end = fw.fallback_window(video, 0.0, 77.0, 30.0, 60.0)
    assert start == pytest.approx(5.0, abs=0.3) and end == pytest.approx(45.0, abs=0.3)


def test_a_video_without_title_cards_keeps_the_first_seconds(tmp_path):
    video = _video(tmp_path, [("scene", 60)])
    assert fw.fallback_window(video, 0.0, 60.0, 30.0, 60.0) == (0.0, 30.0)


def test_a_chapter_longer_than_a_clip_may_be_keeps_the_first_seconds(tmp_path):
    video = _video(tmp_path, [("scene", 4), ("card", 2), ("scene", 70), ("card", 2), ("scene", 5)])
    assert fw.fallback_window(video, 0.0, 83.0, 30.0, 60.0) == (0.0, 30.0)


def test_a_first_card_late_in_the_video_is_not_a_chapter_opening(tmp_path):
    video = _video(tmp_path, [("scene", 30), ("card", 2), ("scene", 20), ("card", 2), ("scene", 10)])
    assert fw.fallback_window(video, 0.0, 64.0, 30.0, 60.0) == (0.0, 30.0)


def test_an_unreadable_file_keeps_the_first_seconds(tmp_path):
    assert fw.fallback_window(tmp_path / "missing.mp4", 0.0, 90.0, 30.0, 60.0) == (0.0, 30.0)


def test_the_fallback_segment_asks_for_enough_to_find_a_chapter():
    segment = VideoService._build_fallback_segment(video_duration=660.0, transcript="", target_duration=30)
    assert segment["start_time"] == "00:00" and segment["end_time"] == "01:30"
    assert segment["source_ranges"] == [[0.0, 90.0]]
    assert segment["fallback_seconds"] == 30.0 and segment["hook_type"] == "fallback"
    # A source shorter than the target plays exactly its length (#422).
    short = VideoService._build_fallback_segment(video_duration=24.6, transcript="", target_duration=30)
    assert short["source_ranges"] == [[0.0, 24.6]] and short["fallback_seconds"] == 24.6 and short["end_time"] == "00:24"


def test_a_video_that_opens_on_its_first_title_card_starts_there(tmp_path):
    video = _video(tmp_path, [("card", 2), ("scene", 23), ("card", 2), ("scene", 23), ("card", 2), ("scene", 10)])
    start, end = fw.fallback_window(video, 0.0, 62.0, 30.0, 60.0)
    assert start == pytest.approx(0.0, abs=0.3) and end == pytest.approx(25.0, abs=0.3)


def _dark_video(tmp_path, source):
    path = tmp_path / "dark.mp4"
    run = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", source, "-c:v", "libx264", "-preset", "ultrafast",
         "-pix_fmt", "yuv420p", str(path)], capture_output=True, text=True,
    )
    assert run.returncode == 0, run.stderr
    return path


def test_a_fade_to_black_is_not_a_title_card(tmp_path):
    # Black gaps with nothing written on them, 20 s apart.
    video = _dark_video(tmp_path, "testsrc2=s=320x180:r=8:d=60,drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:"
                                  "enable='between(mod(t,20),5,7)'")
    assert fw.title_cards(video, 0.0, 60.0) == []
    assert fw.fallback_window(video, 0.0, 60.0, 30.0, 60.0) == (0.0, 30.0)


def test_dark_footage_that_moves_or_lasts_is_not_a_title_card(tmp_path):
    # A vertical picture pillarboxed in 16:9 (most of the frame is black bars) that dims every 17 s for 8 s.
    video = _dark_video(
        tmp_path,
        "testsrc2=s=100x180:r=8:d=60,eq=brightness=-0.25:enable='lt(mod(t,17),8)',pad=320:180:110:0:black",
    )
    assert fw.title_cards(video, 0.0, 60.0) == []
