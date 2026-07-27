from pathlib import Path

import pytest

from src import clip_editor, video_utils


def test_overlay_custom_captions_uses_template_and_cached_word_timings(
    tmp_path, monkeypatch
):
    input_path = tmp_path / "clip.mp4"
    input_path.write_bytes(b"clip")
    source_path = tmp_path / "source.mp4"
    source_path.with_suffix(".transcript_cache.json").write_text(
        """
        {
          "version": 2,
          "words": [
            {"text": "original", "start": 10200, "end": 10600, "confidence": 0.99},
            {"text": "words", "start": 10800, "end": 11400, "confidence": 0.99}
          ],
          "utterances": [],
          "text": "original words"
        }
        """,
        encoding="utf-8",
    )
    captured = {}
    original_builder = video_utils.build_assemblyai_ass_subtitles

    def capture_builder(*args, **kwargs):
        result = original_builder(*args, **kwargs)
        captured["kwargs"] = kwargs
        captured["content"] = Path(kwargs["output_ass_path"]).read_text(
            encoding="utf-8"
        )
        return result

    monkeypatch.setattr(clip_editor, "_ffprobe_size", lambda _path: (1080, 1920))
    monkeypatch.setattr(clip_editor, "_ffprobe_duration", lambda _path: 2.0)
    monkeypatch.setattr(clip_editor, "_run", lambda _command: None)
    monkeypatch.setattr(
        clip_editor, "build_assemblyai_ass_subtitles", capture_builder
    )
    monkeypatch.setattr(video_utils, "emoji_rendering_supported", lambda: False)

    clip_editor.overlay_custom_captions(
        input_path,
        tmp_path / "output",
        "edited caption",
        "top",
        ["caption"],
        caption_template="hormozi",
        transcript_video_path=source_path,
        source_ranges=[(10.0, 12.0)],
    )

    assert captured["kwargs"]["font_family"] is None
    assert captured["kwargs"]["font_size"] is None
    assert captured["kwargs"]["font_color"] is None
    assert captured["kwargs"]["caption_template"] == "hormozi"
    assert captured["kwargs"]["position_y_override"] == 0.18
    caption_words = captured["kwargs"]["caption_words"]
    assert [word["text"] for word in caption_words] == ["edited", "caption"]
    assert [word["start"] for word in caption_words] == pytest.approx([0.2, 0.8])
    assert [word["end"] for word in caption_words] == pytest.approx([0.6, 1.4])
    assert "0:00:00.20" in captured["content"]
    assert "0:00:00.80" in captured["content"]
    assert ",73," in captured["content"]
