"""TRANSCRIPTION_PROVIDER=auto: word-timed YouTube captions, else Whisper."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.config import Config
from src.media import transcription


def auto_captions(lines):
    """A json3 payload shaped like YouTube auto-captions (one event per line)."""
    events = []
    for start_ms, duration_ms, words in lines:
        segs = []
        for index, (offset_ms, text) in enumerate(words):
            seg = {"utf8": text if index == 0 else f" {text}"}
            if index:
                seg["tOffsetMs"] = offset_ms
            segs.append(seg)
        events.append({"tStartMs": start_ms, "dDurationMs": duration_ms, "segs": segs})
    return {"events": events}


def manual_captions(lines):
    """Manual captions: one segment per line, no per-word offsets."""
    return {
        "events": [
            {"tStartMs": start, "dDurationMs": duration, "segs": [{"utf8": text}]}
            for start, duration, text in lines
        ]
    }


SAMPLE = auto_captions(
    [
        (1000, 2000, [(0, "this"), (400, "is"), (900, "a"), (1300, "test")]),
        (3000, 2000, [(0, "of"), (500, "word"), (1000, "timing")]),
    ]
)


def test_config_accepts_auto(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "Auto")
    assert Config().transcription_provider == "auto"


@pytest.mark.parametrize("value", ["whisper", "youtube_captions", "assemblyai"])
def test_config_keeps_existing_providers(monkeypatch, value):
    monkeypatch.setenv("TRANSCRIPTION_PROVIDER", value)
    assert Config().transcription_provider == value


def test_conversion_gives_word_timings_in_seconds():
    result = transcription.youtube_json3_to_whisper_result(SAMPLE, "en")
    words = [w for segment in result["segments"] for w in segment["words"]]
    assert [w["word"] for w in words] == ["this", "is", "a", "test", "of", "word", "timing"]
    assert words[0]["start"] == 1.0 and words[1]["start"] == 1.4
    # The last word of a line ends where the line does.
    assert words[3]["end"] == 3.0
    assert result["language"] == "en"
    assert "this is a test of word timing" in result["text"]


def test_words_never_overlap_the_next_word():
    overlapping = auto_captions(
        [
            (0, 4000, [(0, "one"), (1000, "two"), (2000, "three")]),
            (1500, 2000, [(0, "four"), (500, "five")]),
        ]
    )
    words = [
        w
        for segment in transcription.youtube_json3_to_whisper_result(overlapping)["segments"]
        for w in segment["words"]
    ]
    for earlier, later in zip(words, words[1:]):
        assert earlier["end"] <= later["start"]


def test_manual_captions_without_word_timing_are_rejected():
    payload = manual_captions([(0, 2000, "hello there"), (2000, 2000, "general kenobi")])
    assert transcription.youtube_json3_to_whisper_result(payload) is None


def test_empty_and_newline_only_captions_are_rejected():
    assert transcription.youtube_json3_to_whisper_result({"events": []}) is None
    assert transcription.youtube_json3_to_whisper_result(
        {"events": [{"tStartMs": 0, "segs": [{"utf8": "\n"}]}]}
    ) is None


def test_long_gaps_split_analysis_segments():
    payload = auto_captions(
        [
            (0, 1000, [(0, "before"), (400, "gap")]),
            (20000, 1000, [(0, "after"), (400, "gap")]),
        ]
    )
    assert len(transcription.youtube_json3_to_whisper_result(payload)["segments"]) == 2


def test_picks_the_original_track_not_a_translation():
    def track(url):
        return [{"ext": "json3", "url": url}, {"ext": "vtt", "url": url + "&fmt=vtt"}]

    info = {
        "language": "es",
        "automatic_captions": {
            "en": track("https://example.com/timedtext?lang=es&tlang=en"),
            "es": track("https://example.com/timedtext?lang=es"),
        },
    }
    assert transcription._pick_youtube_auto_caption_url(info, None) == "https://example.com/timedtext?lang=es"
    # Asking for English never returns the machine translation of a Spanish video.
    assert transcription._pick_youtube_auto_caption_url(info, "en_us") == "https://example.com/timedtext?lang=es"


def test_no_track_when_only_translations_exist():
    info = {
        "automatic_captions": {
            "en": [{"ext": "json3", "url": "https://example.com/timedtext?tlang=en"}]
        }
    }
    assert transcription._pick_youtube_auto_caption_url(info, None) is None


def auto_config(**overrides):
    values = dict(transcription_provider="auto", transcription_language="", whisper_model="small")
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"x")
    return path


def patch_provider(monkeypatch, config, captions, duration=30.0):
    monkeypatch.setattr(transcription, "get_config", lambda: config)
    monkeypatch.setattr(
        transcription, "transcribe_with_youtube_word_captions", lambda url, language=None: captions
    )
    monkeypatch.setattr(transcription, "ffprobe_duration", lambda path: duration)
    whisper_calls = []
    monkeypatch.setattr(
        transcription,
        "_get_transcript_with_whisper",
        lambda path, cfg: whisper_calls.append(path) or "[00:00 - 00:01] from whisper",
    )
    return whisper_calls


def test_auto_uses_captions_and_writes_the_word_timing_cache(monkeypatch, video):
    captions = transcription.youtube_json3_to_whisper_result(SAMPLE, "en")
    whisper_calls = patch_provider(monkeypatch, auto_config(), captions)

    text = transcription.get_video_transcript(video, source_url="https://youtu.be/aaaaaaaaaaa")

    assert whisper_calls == []
    assert text == "[00:01 - 00:05] this is a test of word timing"
    cache = json.loads(video.with_suffix(".transcript_cache.json").read_text())
    assert cache["version"] == transcription.TRANSCRIPT_CACHE_SCHEMA_VERSION
    assert [w["text"] for w in cache["words"]][:2] == ["this", "is"]
    assert cache["words"][0]["start"] == 1000 and cache["words"][1]["start"] == 1400


def test_auto_falls_back_to_whisper_without_captions(monkeypatch, video):
    whisper_calls = patch_provider(monkeypatch, auto_config(), None)
    text = transcription.get_video_transcript(video, source_url="https://youtu.be/aaaaaaaaaaa")
    assert text.endswith("from whisper") and whisper_calls == [video]


def test_auto_uses_whisper_for_uploaded_files(monkeypatch, video):
    captions = transcription.youtube_json3_to_whisper_result(SAMPLE, "en")
    whisper_calls = patch_provider(monkeypatch, auto_config(), captions)
    transcription.get_video_transcript(video, source_url=None)
    assert whisper_calls == [video]


def test_auto_rejects_captions_longer_than_the_file(monkeypatch, video):
    long_captions = auto_captions([(100000, 2000, [(0, "far"), (500, "away")])])
    captions = transcription.youtube_json3_to_whisper_result(long_captions, "en")
    whisper_calls = patch_provider(monkeypatch, auto_config(), captions, duration=30.0)
    transcription.get_video_transcript(video, source_url="https://youtu.be/aaaaaaaaaaa")
    assert whisper_calls == [video]
    assert not video.with_suffix(".transcript_cache.json").exists()


def test_existing_providers_never_reach_the_caption_path(monkeypatch, video):
    config = auto_config(transcription_provider="whisper")
    whisper_calls = patch_provider(monkeypatch, config, None)

    def fail(*args, **kwargs):
        raise AssertionError("auto-only path used by another provider")

    monkeypatch.setattr(transcription, "transcribe_with_youtube_word_captions", fail)
    transcription.get_video_transcript(video, source_url="https://youtu.be/aaaaaaaaaaa")
    assert whisper_calls == [video]


def test_caption_fetch_failure_returns_none_instead_of_raising(monkeypatch):
    import yt_dlp

    class Broken:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            raise RuntimeError("HTTP Error 429")

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(yt_dlp, "YoutubeDL", Broken)
    assert transcription.transcribe_with_youtube_word_captions("https://youtu.be/aaaaaaaaaaa") is None
