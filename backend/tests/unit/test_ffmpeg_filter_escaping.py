import shutil
import subprocess
from pathlib import Path

import pytest

from src.media.ffmpeg import (
    ffmpeg_escape_filter_path,
    subtitles_filter_fragment,
)


def test_escapes_windows_drive_and_separators_for_both_parse_levels():
    escaped = ffmpeg_escape_filter_path(Path(r"C:\Users\O'Brien\Temp\clip.ass"))

    assert escaped == (
        r"C\\:\\\\Users\\\\O\\\'Brien\\\\Temp\\\\clip.ass"
    )


def test_escapes_filtergraph_separators():
    escaped = ffmpeg_escape_filter_path(Path("/tmp/a,b;[c]/x.ass"))

    assert escaped == r"/tmp/a\,b\;\[c\]/x.ass"


ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 64
PlayResY: 64

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,Hi
"""


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_ffmpeg_opens_subtitles_from_awkward_directory(tmp_path):
    awkward = tmp_path / "it's [a], b; c:d \\ e"
    awkward.mkdir()
    ass_path = awkward / "captions.ass"
    ass_path.write_text(ASS)
    fonts_dir = awkward / "fonts"
    fonts_dir.mkdir()

    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x64:d=0.2",
            "-vf",
            subtitles_filter_fragment(ass_path, fonts_dir),
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
