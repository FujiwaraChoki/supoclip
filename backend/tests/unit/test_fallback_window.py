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
