"""An audio track with no speech is its own terminal failure, not a generic one."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.media import transcription

PROVIDER_NO_SPEECH = "language_detection cannot be performed on files with no spoken audio."


def run_assemblyai(monkeypatch, tmp_path, transcript, language=""):
    runtime = SimpleNamespace(
        assembly_ai_api_key="test", assembly_ai_http_timeout_seconds=60,
        transcription_language=language,
    )
    monkeypatch.setattr(transcription, "_prepare_audio_for_transcription", lambda path: path)
    monkeypatch.setattr(
        transcription, "_submit_and_wait_for_assemblyai_transcript", Mock(return_value=transcript)
    )
    cache = Mock()
    monkeypatch.setattr(transcription, "cache_transcript_data", cache)
    monkeypatch.setattr(transcription, "format_transcript_for_analysis", lambda _t: ["[00:00 - 00:01] hello"])
    return transcription._get_transcript_with_assemblyai(tmp_path / "video.mp4", "universal", runtime), cache


def errored(message):
    return SimpleNamespace(status=transcription.aai.TranscriptStatus.error, error=message, words=None)


def test_provider_no_spoken_audio_is_no_speech(monkeypatch, tmp_path):
    with pytest.raises(transcription.NoSpeechDetectedError) as error:
        run_assemblyai(monkeypatch, tmp_path, errored(PROVIDER_NO_SPEECH))
    assert str(error.value) == transcription.NO_SPEECH_MESSAGE
    assert error.value.error_code == "no_speech"


def test_music_only_with_fixed_language_completes_empty_and_is_no_speech(monkeypatch, tmp_path):
    # A configured language skips detection, so music-only audio completes with no words.
    empty = SimpleNamespace(status=transcription.aai.TranscriptStatus.completed, words=[], json_response={})
    with pytest.raises(transcription.NoSpeechDetectedError):
        run_assemblyai(monkeypatch, tmp_path, empty, language="en")


def test_empty_transcript_is_not_cached(monkeypatch, tmp_path):
    empty = SimpleNamespace(status=transcription.aai.TranscriptStatus.completed, words=[], json_response={})
    cache = Mock()
    monkeypatch.setattr(transcription, "cache_transcript_data", cache)
    runtime = SimpleNamespace(assembly_ai_api_key="test", assembly_ai_http_timeout_seconds=60, transcription_language="")
    monkeypatch.setattr(transcription, "_prepare_audio_for_transcription", lambda path: path)
    monkeypatch.setattr(transcription, "_submit_and_wait_for_assemblyai_transcript", Mock(return_value=empty))
    with pytest.raises(transcription.NoSpeechDetectedError):
        transcription._get_transcript_with_assemblyai(tmp_path / "video.mp4", "universal", runtime)
    cache.assert_not_called()


def test_speech_transcript_is_returned(monkeypatch, tmp_path):
    spoken = SimpleNamespace(
        status=transcription.aai.TranscriptStatus.completed, words=[object()], json_response={"language_code": "en"},
    )
    result, cache = run_assemblyai(monkeypatch, tmp_path, spoken)
    assert result == "[00:00 - 00:01] hello"
    cache.assert_called_once()


@pytest.mark.parametrize(
    "message",
    [
        "Internal server error",  # provider fault: may succeed later
        "File does not appear to contain audio. File type is video/mp4 (no audio stream).",  # missing track
        "Transcoding failed. Unsupported codec.",  # malformed media
        "",
        None,
    ],
)
def test_other_provider_errors_stay_generic_transcription_failures(monkeypatch, tmp_path, message):
    with pytest.raises(Exception) as error:
        run_assemblyai(monkeypatch, tmp_path, errored(message))
    assert not isinstance(error.value, transcription.NoSpeechDetectedError)
    assert str(error.value).startswith("Transcription failed:")


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        (PROVIDER_NO_SPEECH, True),
        ("LANGUAGE_DETECTION cannot be performed on files with NO SPOKEN AUDIO", True),
        ("no audio stream", False),
        ("audio is silent or too short", False),
        ("Transcription failed: Internal server error", False),
        ("", False),
        (None, False),
    ],
)
def test_only_the_explicit_no_spoken_audio_rejection_matches(detail, expected):
    assert transcription.is_no_speech_provider_error(detail) is expected


def test_no_speech_message_is_shown_to_the_user():
    from src.services.task_service import user_facing_error

    assert user_facing_error(transcription.NoSpeechDetectedError()) == transcription.NO_SPEECH_MESSAGE
