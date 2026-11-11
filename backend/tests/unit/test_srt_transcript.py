"""SRT subtitles become the word-timed transcript cache every later stage reads."""
from datetime import timedelta

import pytest
import srt

from src.media.common import TRANSCRIPT_CACHE_SCHEMA_VERSION
from src.media.srt_transcript import (
    SrtSubtitleError,
    format_srt_for_analysis,
    load_srt_transcript_cache,
    parse_srt_bytes,
    srt_to_transcript_cache,
)


def cues(*rows):
    return [
        srt.Subtitle(
            index=index + 1,
            start=timedelta(seconds=start),
            end=timedelta(seconds=end),
            content=text,
        )
        for index, (start, end, text) in enumerate(rows)
    ]


def cache_for(*rows, **kwargs):
    return srt_to_transcript_cache(cues(*rows), **kwargs)


def test_words_are_timed_inside_their_cue_and_never_overlap():
    cache = cache_for((0.0, 2.0, "hello brave world"))

    words = cache["words"]
    assert [word["text"] for word in words] == ["hello", "brave", "world"]
    assert words[0]["start"] == 0
    assert words[-1]["end"] == 2000
    for previous, current in zip(words, words[1:]):
        assert current["start"] >= previous["end"]


def test_last_word_ends_at_the_cue_end():
    cache = cache_for((1.5, 4.5, "one two three"))

    assert cache["utterances"][0]["end"] == 4500
    assert cache["words"][-1]["end"] == 4500


def test_words_are_distributed_proportionally_over_the_cue():
    cache = cache_for((0.0, 3.0, "a b c"))

    starts = [word["start"] for word in cache["words"]]
    assert starts == [0, 1000, 2000]


def test_markup_is_stripped_and_blank_cues_skipped():
    cache = cache_for((0.0, 1.0, "<i>Hi</i>"), (1.0, 2.0, "   "), (2.0, 3.0, "{\\an8}there"))

    assert [utterance["text"] for utterance in cache["utterances"]] == ["Hi", "there"]
    assert cache["text"] == "Hi\nthere"


def test_multiline_cue_content_is_one_line():
    cache = cache_for((0.0, 2.0, "first line\nsecond line"))

    assert cache["utterances"][0]["text"] == "first line second line"


def test_overlapping_cues_are_trimmed_to_the_next_start():
    cache = cache_for((0.0, 5.0, "long cue"), (3.0, 6.0, "next cue"))

    first, second = cache["utterances"]
    assert first["end"] <= second["start"]
    assert cache["words"][0]["end"] <= cache["words"][1]["start"]


def test_zero_duration_cue_still_gets_one_millisecond_words():
    cache = cache_for((2.0, 2.0, "instant"))

    word = cache["words"][0]
    assert word["end"] > word["start"]


def test_cues_past_the_video_duration_are_dropped_and_clamped():
    cache = cache_for(
        (0.0, 4.0, "kept"),
        (4.5, 9.0, "clipped"),
        (20.0, 22.0, "gone"),
        duration_seconds=5.0,
    )

    assert [utterance["text"] for utterance in cache["utterances"]] == ["kept", "clipped"]
    assert cache["words"][-1]["end"] == 5000


def test_no_space_language_splits_per_character():
    cache = cache_for((0.0, 2.0, "你好世界"), language_code="zh")

    assert [word["text"] for word in cache["words"]] == ["你", "好", "世", "界"]


def test_cache_uses_the_current_schema_version_and_language():
    cache = cache_for((0.0, 1.0, "hello"), language_code="en")

    assert cache["version"] == TRANSCRIPT_CACHE_SCHEMA_VERSION
    assert cache["language_code"] == "en"
    assert cache["words"][0]["confidence"] == 1.0
    assert cache["words"][0]["speaker"] is None


def test_parse_rejects_text_that_is_not_a_subtitle():
    with pytest.raises(SrtSubtitleError):
        parse_srt_bytes(b"this file has no cues at all")


def test_parse_accepts_utf8_bom_and_utf16():
    body = "1\n00:00:00,000 --> 00:00:01,000\nhello\n"
    assert parse_srt_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
    assert parse_srt_bytes(body.encode("utf-16"))


def test_load_from_disk_reads_and_converts(tmp_path):
    path = tmp_path / "captions.srt"
    path.write_text("1\n00:00:00,500 --> 00:00:02,500\nhello there\n", encoding="utf-8")

    cache = load_srt_transcript_cache(path, duration_seconds=10.0)

    assert [word["text"] for word in cache["words"]] == ["hello", "there"]
    assert cache["words"][0]["start"] == 500


def test_load_from_disk_reports_a_missing_file(tmp_path):
    with pytest.raises(SrtSubtitleError):
        load_srt_transcript_cache(tmp_path / "missing.srt")


def test_analysis_lines_look_like_speech_provider_lines():
    cache = cache_for((0.0, 2.0, "hello brave world"), (2.0, 4.0, "second cue"))

    lines = format_srt_for_analysis(cache)

    assert lines[0].startswith("[00:00 - 00:02] ")
    assert "hello brave world" in lines[0]
    assert "second cue" in lines[-1]

def test_joined_analysis_text_is_what_the_analyzer_parses():
    # The pipeline hands the analysis step one newline-joined string, exactly
    # like a speech provider does; a list of lines crashes _parse_transcript_lines.
    from src.ai import _parse_transcript_lines

    cache = cache_for((0.0, 2.0, "hello brave world"), (2.0, 4.0, "second cue"))
    text = "\n".join(format_srt_for_analysis(cache))

    assert isinstance(text, str)
    lines = _parse_transcript_lines(text)
    assert [line["text"] for line in lines] == ["hello brave world", "second cue"]
    assert lines[0]["start_seconds"] == 0.0
