import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.config import Config
from src.video_utils import (
    get_video_transcript_openai_compatible,
    run_transcription_fallback_chain,
)


def test_openai_compatible_provider_normalization():
    assert Config._normalize_transcription_provider("openai_compatible") == "openai_compatible"
    assert Config._normalize_transcription_provider("openai-compatible") == "openai_compatible"
    assert Config._normalize_transcription_provider("openai_whisper") == "openai_compatible"
    assert Config._normalize_transcription_provider("remote-whisper") == "openai_compatible"
    assert Config._normalize_transcription_provider("custom_whisper") == "openai_compatible"


def test_openai_compatible_transcription_success(tmp_path):
    dummy_video = tmp_path / "dummy_video.mp4"
    dummy_video.touch()
    dummy_audio = tmp_path / "dummy_video.transcription.mp3"
    dummy_audio.write_bytes(b"dummy audio content")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "text": "Hello world! This is remote transcription on GPU.",
        "segments": [
            {
                "id": 0,
                "start": 0.0,
                "end": 2.5,
                "text": "Hello world!",
                "words": [
                    {"word": "Hello", "start": 0.0, "end": 1.0},
                    {"word": "world!", "start": 1.0, "end": 2.5},
                ],
            },
            {
                "id": 1,
                "start": 2.5,
                "end": 5.0,
                "text": "This is remote transcription on GPU.",
                "words": [],
            },
        ],
    }

    with patch("requests.post", return_value=mock_response) as mock_post:
        with patch("src.video_utils._prepare_audio_for_transcription", return_value=dummy_audio):
            result = get_video_transcript_openai_compatible(
                dummy_video,
                base_url="http://remote-gpu:8000/v1",
                api_key="secret-key",
                model_name="Systran/faster-whisper-large-v3",
            )

            # Verify POST called with correct endpoint
            mock_post.assert_called_once()
            args, kwargs = mock_post.call_args
            assert args[0] == "http://remote-gpu:8000/v1/audio/transcriptions"
            assert kwargs["headers"]["Authorization"] == "Bearer secret-key"
            assert kwargs["data"]["model"] == "Systran/faster-whisper-large-v3"
            assert kwargs["data"]["response_format"] == "verbose_json"

            # Verify formatted lines
            assert "[00:00 - 00:02] Hello world!" in result
            assert "[00:02 - 00:05] This is remote transcription on GPU." in result


def test_openai_compatible_fallback_chain(tmp_path):
    dummy_video = tmp_path / "dummy_video.mp4"
    dummy_video.touch()
    dummy_audio = tmp_path / "dummy_video.transcription.mp3"
    dummy_audio.write_bytes(b"dummy audio content")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "text": "Fallback success",
        "segments": [
            {"id": 0, "start": 0.0, "end": 3.0, "text": "Fallback success", "words": []},
        ],
    }

    with patch("src.video_utils.get_video_transcript_faster_whisper", side_effect=RuntimeError("CUDA OOM")):
        with patch("requests.post", return_value=mock_response):
            with patch("src.video_utils._prepare_audio_for_transcription", return_value=dummy_audio):
                result = run_transcription_fallback_chain(
                    dummy_video,
                    chain=["faster_whisper", "openai_compatible"],
                )
                assert "[00:00 - 00:03] Fallback success" in result
