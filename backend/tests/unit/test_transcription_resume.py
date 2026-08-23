import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src import video_utils


def test_assemblyai_job_checkpoint_lifecycle(tmp_path: Path):
    video_file = tmp_path / "test_video.mp4"
    video_file.touch()

    # Initially no checkpoint
    assert video_utils._load_assemblyai_job_checkpoint(video_file) is None

    # Save checkpoint
    video_utils._save_assemblyai_job_checkpoint(video_file, "job_12345", "universal")
    checkpoint = video_utils._load_assemblyai_job_checkpoint(video_file)
    assert checkpoint is not None
    assert checkpoint["transcript_id"] == "job_12345"
    assert checkpoint["speech_model"] == "universal"

    # Clear checkpoint
    video_utils._clear_assemblyai_job_checkpoint(video_file)
    assert video_utils._load_assemblyai_job_checkpoint(video_file) is None


def test_assemblyai_resumes_completed_job(tmp_path: Path):
    video_file = tmp_path / "test_video.mp4"
    video_file.touch()
    video_utils._save_assemblyai_job_checkpoint(video_file, "job_completed_999", "universal")

    mock_transcript = SimpleNamespace(
        status=video_utils.aai.TranscriptStatus.completed,
        error=None,
        id="job_completed_999",
        text="Resumed transcript text",
        words=[],
        utterances=[],
    )

    mock_transcriber = MagicMock()
    mock_transcriber._client = MagicMock()

    with patch("src.video_utils.aai.api.get_transcript", return_value={}) as mock_api_get:
        with patch("src.video_utils.aai.Transcript.from_response", return_value=mock_transcript):
            result = video_utils._submit_and_wait_for_assemblyai_transcript(
                mock_transcriber,
                video_file,
                config_obj=MagicMock(),
                timeout_seconds=30,
            )

            assert result.status == video_utils.aai.TranscriptStatus.completed
            assert result.id == "job_completed_999"
            # Ensure transcriber.submit was NEVER called because we resumed an existing completed job
            mock_transcriber.submit.assert_not_called()
            # Checkpoint should be cleared upon completion
            assert video_utils._load_assemblyai_job_checkpoint(video_file) is None


def test_assemblyai_falls_back_when_resumed_job_errors(tmp_path: Path):
    video_file = tmp_path / "test_video.mp4"
    video_file.touch()
    video_utils._save_assemblyai_job_checkpoint(video_file, "job_errored_111", "universal")

    errored_transcript = SimpleNamespace(
        status=video_utils.aai.TranscriptStatus.error,
        error="File corrupted",
        id="job_errored_111",
    )
    new_transcript = SimpleNamespace(
        status=video_utils.aai.TranscriptStatus.completed,
        error=None,
        id="job_new_222",
    )

    mock_transcriber = MagicMock()
    mock_transcriber._client = MagicMock()
    mock_transcriber.submit.return_value = SimpleNamespace(id="job_new_222", _client=mock_transcriber._client)

    with patch("src.video_utils.aai.api.get_transcript", return_value={}):
        with patch(
            "src.video_utils.aai.Transcript.from_response",
            side_effect=[errored_transcript, new_transcript],
        ):
            result = video_utils._submit_and_wait_for_assemblyai_transcript(
                mock_transcriber,
                video_file,
                config_obj=MagicMock(),
                timeout_seconds=30,
            )

            assert result.status == video_utils.aai.TranscriptStatus.completed
            # Should have submitted a new job after the old one was in error
            mock_transcriber.submit.assert_called_once()


def test_transcript_checkpoint_lifecycle(tmp_path: Path):
    video_file = tmp_path / "test_whisper.mp4"
    video_file.touch()

    assert video_utils._load_transcript_checkpoint(video_file) is None

    words = [
        video_utils.WhisperWord(text="Hello", start=0, end=500),
        video_utils.WhisperWord(text="World", start=500, end=1000),
    ]
    utterances = [
        video_utils.WhisperUtterance(text="Hello World", start=0, end=1000, words=words)
    ]
    full_text_parts = ["Hello World"]

    video_utils._save_transcript_checkpoint(
        video_file,
        words=words,
        utterances=utterances,
        full_text_parts=full_text_parts,
        last_end_seconds=1.0,
    )

    checkpoint = video_utils._load_transcript_checkpoint(video_file)
    assert checkpoint is not None
    assert checkpoint["last_end_seconds"] == 1.0
    assert len(checkpoint["words"]) == 2
    assert checkpoint["full_text_parts"] == ["Hello World"]

    video_utils._clear_transcript_checkpoint(video_file)
    assert video_utils._load_transcript_checkpoint(video_file) is None
