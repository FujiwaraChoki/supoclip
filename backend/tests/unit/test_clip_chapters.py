"""A source's chapters must not reach a clip: MP4 turns them into a source-long text track."""

import json
import shutil
import subprocess

import pytest

from src.media.ffmpeg import (
    build_final_video_encode_args,
    render_ranges_crossfade_ffmpeg,
    render_source_ranges_ffmpeg,
)

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _probe(path):
    return json.loads(
        subprocess.run(
            ["ffprobe", "-v", "error", "-show_chapters", "-show_streams", "-of", "json", str(path)],
            capture_output=True, text=True, check=True,
        ).stdout
    )


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    folder = tmp_path_factory.mktemp("chapters")
    metadata = folder / "meta.txt"
    metadata.write_text(
        ";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=6000\ntitle=Scene 1\n"
        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=6000\nEND=12000\ntitle=Credits\n"
    )
    path = folder / "source.mkv"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=12",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=12", "-i", str(metadata),
         "-map", "0", "-map", "1", "-map_chapters", "2", "-c:v", "libx264", "-preset", "ultrafast",
         "-c:a", "aac", str(path)],
        check=True,
    )
    assert len(_probe(path)["chapters"]) == 2
    return path


def _assert_clean(path, seconds):
    probe = _probe(path)
    assert probe["chapters"] == []
    assert sorted(stream["codec_type"] for stream in probe["streams"]) == ["audio", "video"]
    assert all(abs(float(stream["duration"]) - seconds) < 0.5 for stream in probe["streams"])


def test_single_range_clip_has_no_chapters(source, tmp_path):
    out = tmp_path / "one.mp4"
    assert render_source_ranges_ffmpeg(source, [(1.0, 5.0)], out)
    _assert_clean(out, 4.0)


def test_stitched_clip_has_no_chapters(source, tmp_path):
    out = tmp_path / "two.mp4"
    assert render_source_ranges_ffmpeg(source, [(1.0, 3.0), (7.0, 9.0)], out)
    _assert_clean(out, 4.0)


def test_crossfaded_clip_has_no_chapters(source, tmp_path):
    out = tmp_path / "three.mp4"
    assert render_ranges_crossfade_ffmpeg(source, [(1.0, 3.0), (7.0, 9.0)], out, 0)
    _assert_clean(out, 4.0)


def test_final_encode_drops_chapters():
    assert "-map_chapters" in build_final_video_encode_args()
