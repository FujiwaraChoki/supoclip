"""Real ffmpeg runs proving subtitle/font paths survive filtergraph parsing.

Windows temp paths contain a drive colon and backslashes; on POSIX a directory
name with a colon and a space reproduces the same parsing hazards.
"""
import os
import shutil
import subprocess

import pytest

from src.media.ffmpeg import subtitles_filter_fragment

ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 120
PlayResY: 120

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BorderStyle, Outline, Alignment
Style: Default,Arial,24,&H00FFFFFF,&H00000000,1,1,2

[Events]
Format: Layer, Start, End, Style, Text
Dialogue: 0,0:00:00.00,0:00:01.00,Default,hi
"""

TRICKY_DIR = "caps dir" if os.name == "nt" else "caps dir:v2"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required")
def test_subtitles_fragment_renders_with_tricky_paths(tmp_path):
    work = tmp_path / TRICKY_DIR
    fonts = work / "fonts"
    fonts.mkdir(parents=True)
    ass_path = work / "captions.ass"
    ass_path.write_text(ASS, encoding="utf-8")
    output = tmp_path / "frame.png"

    result = subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=120x120:d=1",
            "-vf", subtitles_filter_fragment(ass_path, fonts),
            "-frames:v", "1", str(output),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr[-800:]
    assert output.exists()
