"""
Tests for Whisper, faster-whisper, and WhisperX transcription integration and provider dispatching.
"""

from unittest.mock import MagicMock, patch
import json
from pathlib import Path
import pytest

from src.config import Config
from src.video_utils import (
    WhisperWord,
    WhisperUtterance,
    WhisperTranscriptResult,
    get_video_transcript_whisper,
    get_video_transcript_faster_whisper,
    get_video_transcript_whisperx,
    run_transcription_fallback_chain,
    get_video_transcript,
    format_transcript_for_analysis,
    cache_transcript_data,
    load_cached_transcript_data,
)


def test_whisper_data_structures():
    word1 = WhisperWord("Hello", 0, 500, 0.95)
    word2 = WhisperWord("world", 500, 1000, 0.99)
    utterance = WhisperUtterance("Hello world", 0, 1000, [word1, word2])
    result = WhisperTranscriptResult("Hello world", [word1, word2], [utterance])

    assert result.status == "completed"
    assert len(result.words) == 2
    assert len(result.utterances) == 1
    assert result.utterances[0].text == "Hello world"

    formatted = format_transcript_for_analysis(result)
    assert len(formatted) == 1
    assert "Hello world" in formatted[0]


def test_whisper_cache_data(tmp_path: Path):
    video_file = tmp_path / "sample_video.mp4"
    video_file.touch()

    word = WhisperWord("test", 100, 400, 0.9)
    utterance = WhisperUtterance("test", 100, 400, [word])
    transcript = WhisperTranscriptResult("test", [word], [utterance])

    cache_transcript_data(video_file, transcript)

    cached = load_cached_transcript_data(video_file)
    assert cached is not None
    assert cached["text"] == "test"
    assert len(cached["words"]) == 1
    assert cached["words"][0]["text"] == "test"
    assert cached["words"][0]["start"] == 100
    assert cached["words"][0]["end"] == 400


def test_config_transcription_providers(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "faster-whisper")
    monkeypatch.setenv("WHISPER_MODEL_SIZE", "small")

    config = Config()
    assert config.transcription_provider == "faster_whisper"
    assert config.whisper_model == "small"

    monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "whisperx")
    config = Config()
    assert config.transcription_provider == "whisperx"


@patch("src.video_utils._prepare_audio_for_transcription")
@patch("whisper.load_model")
def test_get_video_transcript_whisper(mock_load_model, mock_prepare_audio, tmp_path: Path):
    video_file = tmp_path / "mock_video.mp4"
    video_file.touch()

    mock_prepare_audio.return_value = tmp_path / "mock_audio.mp3"

    mock_model = MagicMock()
    mock_model.transcribe.return_value = {
        "text": "Welcome to SupoClip with local Whisper.",
        "segments": [
            {
                "start": 0.0,
                "end": 2.5,
                "text": " Welcome to SupoClip with local Whisper.",
                "words": [
                    {"word": " Welcome", "start": 0.0, "end": 0.5, "probability": 0.98},
                    {"word": " to", "start": 0.5, "end": 0.8, "probability": 0.99},
                    {"word": " SupoClip", "start": 0.8, "end": 1.4, "probability": 0.95},
                    {"word": " with", "start": 1.4, "end": 1.7, "probability": 0.97},
                    {"word": " local", "start": 1.7, "end": 2.0, "probability": 0.96},
                    {"word": " Whisper.", "start": 2.0, "end": 2.5, "probability": 0.98},
                ],
            }
        ],
    }
    mock_load_model.return_value = mock_model

    result = get_video_transcript_whisper(video_file, model_name="base")

    assert "Welcome to SupoClip" in result
    mock_model.transcribe.assert_called_once()

    cached = load_cached_transcript_data(video_file)
    assert cached is not None
    assert len(cached["words"]) == 6
    assert cached["words"][0]["text"] == "Welcome"


@patch("src.video_utils._prepare_audio_for_transcription")
@patch("faster_whisper.WhisperModel")
def test_get_video_transcript_faster_whisper(mock_whisper_model, mock_prepare_audio, tmp_path: Path):
    video_file = tmp_path / "mock_video.mp4"
    video_file.touch()

    mock_prepare_audio.return_value = tmp_path / "mock_audio.mp3"

    mock_word = MagicMock()
    mock_word.word = "Faster"
    mock_word.start = 0.1
    mock_word.end = 0.6
    mock_word.probability = 0.99

    mock_segment = MagicMock()
    mock_segment.text = " Faster transcription"
    mock_segment.start = 0.0
    mock_segment.end = 1.0
    mock_segment.words = [mock_word]

    mock_model_inst = MagicMock()
    mock_model_inst.transcribe.return_value = ([mock_segment], None)
    mock_whisper_model.return_value = mock_model_inst

    result = get_video_transcript_faster_whisper(video_file, model_name="base")

    assert "Faster transcription" in result
    cached = load_cached_transcript_data(video_file)
    assert cached is not None
    assert cached["words"][0]["text"] == "Faster"
    assert cached["words"][0]["start"] == 100
    assert cached["words"][0]["end"] == 600


@patch("src.video_utils._prepare_audio_for_transcription")
def test_get_video_transcript_whisperx(mock_prepare_audio, tmp_path: Path):
    video_file = tmp_path / "mock_video.mp4"
    video_file.touch()

    mock_prepare_audio.return_value = tmp_path / "mock_audio.mp3"

    mock_whisperx = MagicMock()
    mock_model_inst = MagicMock()
    mock_model_inst.transcribe.return_value = {
        "language": "en",
        "segments": [
            {
                "text": "WhisperX aligned output",
                "start": 0.0,
                "end": 1.5,
                "words": [
                    {"word": "WhisperX", "start": 0.0, "end": 0.8, "score": 0.97},
                    {"word": "aligned", "start": 0.8, "end": 1.2, "score": 0.98},
                    {"word": "output", "start": 1.2, "end": 1.5, "score": 0.99},
                ],
            }
        ],
    }
    mock_whisperx.load_model.return_value = mock_model_inst
    mock_whisperx.load_audio.return_value = MagicMock()
    mock_whisperx.load_align_model.return_value = (MagicMock(), MagicMock())
    mock_whisperx.align.return_value = mock_model_inst.transcribe.return_value

    with patch.dict("sys.modules", {"whisperx": mock_whisperx}):
        result = get_video_transcript_whisperx(video_file, model_name="base")

        assert "WhisperX aligned output" in result
        cached = load_cached_transcript_data(video_file)
        assert cached is not None
        assert cached["words"][0]["text"] == "WhisperX"


def test_get_video_transcript_dispatching(monkeypatch, tmp_path: Path):
    video_file = tmp_path / "mock_video.mp4"
    video_file.touch()

    monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "faster-whisper")
    with patch("src.video_utils.get_video_transcript_faster_whisper") as mock_fw:
        mock_fw.return_value = "fw output"
        res = get_video_transcript(video_file)
        assert res == "fw output"
        mock_fw.assert_called_once()

    monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "whisperx")
    with patch("src.video_utils.get_video_transcript_whisperx") as mock_wx:
        mock_wx.return_value = "wx output"
        res = get_video_transcript(video_file)
        assert res == "wx output"
        mock_wx.assert_called_once()


def test_fallback_chain(monkeypatch, tmp_path: Path):
    video_file = tmp_path / "mock_video.mp4"
    video_file.touch()

    monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "auto")
    monkeypatch.delenv("ASSEMBLY_AI_API_KEY", raising=False)
    monkeypatch.setenv("TRANSCRIPTION_FALLBACK_CHAIN", "faster_whisper,whisperx,whisper")

    with patch("src.video_utils.get_video_transcript_faster_whisper") as mock_fw, \
         patch("src.video_utils.get_video_transcript_whisperx") as mock_wx, \
         patch("src.video_utils.get_video_transcript_whisper") as mock_w:
        
        # Test 1: faster_whisper succeeds
        mock_fw.return_value = "faster_whisper success"
        res = get_video_transcript(video_file)
        assert res == "faster_whisper success"
        mock_fw.assert_called_once()

        # Test 2: faster_whisper fails, falls back to whisperx
        mock_fw.side_effect = RuntimeError("faster-whisper not available")
        mock_wx.return_value = "whisperx success"
        res = get_video_transcript(video_file)
        assert res == "whisperx success"
        mock_wx.assert_called_once()

        # Test 3: faster_whisper & whisperx fail, falls back to whisper
        mock_wx.side_effect = RuntimeError("whisperx not available")
        mock_w.return_value = "whisper fallback success"
        res = get_video_transcript(video_file)
        assert res == "whisper fallback success"
        mock_w.assert_called_once()

