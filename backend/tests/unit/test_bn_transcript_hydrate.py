"""
Tests for the BN ElevenLabs transcript → AssemblyAI cache mapping and the
hydrate-from-URL helper. ENG-5686 (BN-side: ENG-5675 / brandninja-monorepo #10109).

The mapping is pure so we test it directly with hand-built payloads. The hydrate
helper wraps an httpx fetch, so we mock that with respx-style monkeypatching
against httpx.Client to keep the test self-contained.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.video_utils import (
    TRANSCRIPT_CACHE_SCHEMA_VERSION,
    hydrate_word_cache_from_bn_transcript,
    map_bn_transcript_to_cache_shape,
)


def _bn_word(text, start, end, speaker_no=0, word_type="word"):
    """Build a BN ElevenLabs `cleanedWords` entry — matches the shape produced
    in packages/tools/plugins/ELEVENLABS/SPEECH_TO_TEXT/tool.ts."""
    return {
        "text": text,
        "type": word_type,
        "start": start,
        "end": end,
        "speakerLabel": f"Speaker {speaker_no}",
        "speakerNo": speaker_no,
        "emoji": None,
        "important": False,
    }


class TestMapBnTranscriptToCacheShape:
    def test_returns_supoclip_cache_schema(self):
        payload = {
            "transcript": "Hello world",
            "words": [_bn_word("Hello", 0.0, 0.5), _bn_word("world", 0.6, 1.0)],
            "language": "en",
        }

        result = map_bn_transcript_to_cache_shape(payload)

        assert result["version"] == TRANSCRIPT_CACHE_SCHEMA_VERSION
        assert result["text"] == "Hello world"
        assert "words" in result
        assert "utterances" in result

    def test_converts_seconds_to_milliseconds(self):
        payload = {
            "transcript": "Hi",
            "words": [_bn_word("Hi", 1.234, 1.567)],
        }

        words = map_bn_transcript_to_cache_shape(payload)["words"]

        assert words[0]["start"] == 1234
        assert words[0]["end"] == 1567

    def test_maps_speaker_no_to_letter(self):
        payload = {
            "transcript": "A B C",
            "words": [
                _bn_word("A", 0.0, 0.1, speaker_no=0),
                _bn_word("B", 0.2, 0.3, speaker_no=1),
                _bn_word("C", 0.4, 0.5, speaker_no=2),
            ],
        }

        speakers = [w["speaker"] for w in map_bn_transcript_to_cache_shape(payload)["words"]]

        assert speakers == ["A", "B", "C"]

    def test_drops_non_word_entries(self):
        payload = {
            "transcript": "Hi there",
            "words": [
                _bn_word("Hi", 0.0, 0.2),
                _bn_word(" ", 0.2, 0.21, word_type="spacing"),
                _bn_word("[applause]", 0.3, 0.5, word_type="audio_event"),
                _bn_word("there", 0.6, 0.9),
            ],
        }

        words = map_bn_transcript_to_cache_shape(payload)["words"]

        assert [w["text"] for w in words] == ["Hi", "there"]

    def test_groups_consecutive_same_speaker_into_one_utterance(self):
        payload = {
            "transcript": "Hello world",
            "words": [
                _bn_word("Hello", 0.0, 0.4, speaker_no=0),
                _bn_word("world", 0.5, 0.9, speaker_no=0),
            ],
        }

        utterances = map_bn_transcript_to_cache_shape(payload)["utterances"]

        assert len(utterances) == 1
        assert utterances[0]["speaker"] == "A"
        assert utterances[0]["text"] == "Hello world"
        assert utterances[0]["start"] == 0
        assert utterances[0]["end"] == 900
        assert len(utterances[0]["words"]) == 2

    def test_starts_new_utterance_on_speaker_change(self):
        payload = {
            "transcript": "Hi there yo",
            "words": [
                _bn_word("Hi", 0.0, 0.2, speaker_no=0),
                _bn_word("there", 0.3, 0.6, speaker_no=1),
                _bn_word("yo", 0.7, 0.9, speaker_no=0),
            ],
        }

        utterances = map_bn_transcript_to_cache_shape(payload)["utterances"]

        # Three runs of one word each = three utterances. Mirrors AssemblyAI's
        # diarized shape where utterances are speaker-bounded.
        assert [(u["speaker"], u["text"]) for u in utterances] == [
            ("A", "Hi"),
            ("B", "there"),
            ("A", "yo"),
        ]

    def test_empty_words_returns_empty_arrays(self):
        result = map_bn_transcript_to_cache_shape({"transcript": "", "words": []})

        assert result["words"] == []
        assert result["utterances"] == []

    def test_missing_words_key_returns_empty_arrays(self):
        result = map_bn_transcript_to_cache_shape({"transcript": "ignored"})

        assert result["words"] == []
        assert result["utterances"] == []

    def test_handles_missing_speaker_no(self):
        # Single-speaker scribe runs don't diarize and may omit speakerNo.
        payload = {
            "transcript": "Mono",
            "words": [{"text": "Mono", "type": "word", "start": 0.0, "end": 0.3}],
        }

        words = map_bn_transcript_to_cache_shape(payload)["words"]

        assert words[0]["speaker"] is None

    def test_drops_words_with_missing_timing(self):
        payload = {
            "transcript": "x",
            "words": [{"text": "x", "type": "word", "start": None, "end": None, "speakerNo": 0}],
        }

        result = map_bn_transcript_to_cache_shape(payload)

        assert result["words"] == []


class TestHydrateWordCacheFromBnTranscript:
    def test_writes_cache_file_when_url_resolves(self, tmp_path):
        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"")  # marker so .with_suffix works against a real path
        expected_cache = tmp_path / "video.transcript_cache.json"

        bn_payload = {
            "transcript": "Hello world",
            "words": [_bn_word("Hello", 0.0, 0.4), _bn_word("world", 0.5, 0.9)],
            "language": "en",
        }

        # Mock httpx.Client used inside the helper.
        with patch("src.video_utils.httpx.Client") as ClientCls:
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.json.return_value = bn_payload
            ClientCls.return_value.__enter__.return_value.get.return_value = mock_response

            ok = hydrate_word_cache_from_bn_transcript(video_path, "https://signed.example/url")

        assert ok is True
        assert expected_cache.exists()
        cached = json.loads(expected_cache.read_text())
        assert cached["text"] == "Hello world"
        assert len(cached["words"]) == 2
        assert cached["words"][0]["start"] == 0
        assert cached["words"][0]["end"] == 400

    def test_skips_when_cache_already_exists(self, tmp_path):
        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"")
        cache_path = tmp_path / "video.transcript_cache.json"
        cache_path.write_text('{"version": 2, "words": [], "utterances": [], "text": ""}')

        # The fetch should never happen — if it does, this fails.
        with patch("src.video_utils.httpx.Client") as ClientCls:
            ok = hydrate_word_cache_from_bn_transcript(video_path, "https://signed.example/url")

        assert ok is True
        ClientCls.assert_not_called()

    def test_returns_false_when_fetch_fails(self, tmp_path):
        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"")

        with patch("src.video_utils.httpx.Client") as ClientCls:
            ClientCls.return_value.__enter__.return_value.get.side_effect = Exception("S3 unreachable")

            ok = hydrate_word_cache_from_bn_transcript(video_path, "https://signed.example/url")

        assert ok is False
        # No cache file written → caller falls back to AssemblyAI.
        assert not (tmp_path / "video.transcript_cache.json").exists()

    def test_returns_false_when_payload_has_no_usable_words(self, tmp_path):
        # E.g. audio-less video → scribe returns only audio_event markers; let
        # the AssemblyAI fallback try.
        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"")

        with patch("src.video_utils.httpx.Client") as ClientCls:
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.json.return_value = {"transcript": "", "words": []}
            ClientCls.return_value.__enter__.return_value.get.return_value = mock_response

            ok = hydrate_word_cache_from_bn_transcript(video_path, "https://signed.example/url")

        assert ok is False
        assert not (tmp_path / "video.transcript_cache.json").exists()
