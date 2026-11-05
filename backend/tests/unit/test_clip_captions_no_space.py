"""Chinese, Japanese and Thai clips store text without spaces; captions still go word by word."""

import json
import shutil
import subprocess

import pytest

from src.clip_editor import _caption_words_with_timings, caption_tokens, overlay_custom_captions

TIMED = [
    {"text": "\u6211\u4eec", "start": 0.2, "end": 0.6},
    {"text": "\u4eca\u5929", "start": 0.6, "end": 1.0},
    {"text": "\u804a", "start": 1.0, "end": 1.2},
    {"text": "\u94b1", "start": 1.2, "end": 1.6},
]
JOINED = "\u6211\u4eec\u4eca\u5929\u804a\u94b1"


def test_unedited_chinese_text_gets_the_spoken_words_back():
    assert caption_tokens(JOINED, TIMED) == ["\u6211\u4eec", "\u4eca\u5929", "\u804a", "\u94b1"]


def test_edited_chinese_text_splits_by_character():
    assert caption_tokens("\u6211\u4eec\u804a\u8d37\u6b3e", TIMED) == list("\u6211\u4eec\u804a\u8d37\u6b3e")


def test_thai_marks_stay_on_their_letter():
    tokens = caption_tokens("\u0e19\u0e49\u0e33\u0e01\u0e34\u0e19", [])
    assert tokens == ["\u0e19\u0e49", "\u0e33", "\u0e01\u0e34", "\u0e19"]


def test_spaced_languages_are_untouched():
    assert caption_tokens("We talk about money", [{"text": "We", "start": 0, "end": 1}]) == ["We", "talk", "about", "money"]
    assert caption_tokens("  ", []) == []


def test_timings_stay_on_the_words():
    words = _caption_words_with_timings(caption_tokens(JOINED, TIMED), TIMED, 2.0)
    assert [word["start"] for word in words] == [0.2, 0.6, 1.0, 1.2]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_caption_edit_on_a_chinese_clip_writes_more_than_one_caption(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x568:rate=30:duration=2",
         "-f", "lavfi", "-i", "sine=duration=2", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(video)],
        check=True,
    )
    video.with_suffix(".transcript_cache.json").write_text(
        json.dumps({
            "version": 2, "language_code": "zh", "text": JOINED, "utterances": [],
            "words": [{**word, "start": int(word["start"] * 1000), "end": int(word["end"] * 1000), "confidence": 1.0}
                      for word in TIMED],
        }),
        encoding="utf-8",
    )
    written = {}
    import src.clip_editor as module
    real = module.build_assemblyai_ass_subtitles

    def spy(*args, **kwargs):
        written["words"] = kwargs["caption_words"]
        return real(*args, **kwargs)

    monkeypatch.setattr(module, "build_assemblyai_ass_subtitles", spy)
    output = overlay_custom_captions(video, tmp_path, JOINED, "bottom", [], transcript_video_path=video, source_ranges=[(0.0, 2.0)])
    assert output.exists()
    assert [word["text"] for word in written["words"]] == [word["text"] for word in TIMED]
