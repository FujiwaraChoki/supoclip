import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import video_utils


class VideoUtilsDiarizationTests(unittest.TestCase):
    def test_format_transcript_for_analysis_uses_diarized_utterances(self):
        transcript = SimpleNamespace(
            utterances=[
                SimpleNamespace(
                    start=0,
                    end=2200,
                    speaker="A",
                    text="Hello there.",
                ),
                SimpleNamespace(
                    start=2200,
                    end=4600,
                    speaker="B",
                    text="General Kenobi.",
                ),
            ],
            words=[],
        )

        formatted = video_utils.format_transcript_for_analysis(transcript)

        self.assertEqual(
            formatted,
            [
                "[00:00 - 00:02] Speaker A: Hello there.",
                "[00:02 - 00:04] Speaker B: General Kenobi.",
            ],
        )

    def test_cache_transcript_data_stores_speakers_and_utterances(self):
        transcript = SimpleNamespace(
            text="Hello there.",
            words=[
                SimpleNamespace(
                    text="Hello",
                    start=0,
                    end=400,
                    confidence=0.98,
                    speaker="A",
                ),
                SimpleNamespace(
                    text="there.",
                    start=401,
                    end=900,
                    confidence=0.97,
                    speaker="A",
                ),
            ],
            utterances=[
                SimpleNamespace(
                    text="Hello there.",
                    start=0,
                    end=900,
                    speaker="A",
                    words=[
                        SimpleNamespace(
                            text="Hello",
                            start=0,
                            end=400,
                            confidence=0.98,
                            speaker="A",
                        ),
                        SimpleNamespace(
                            text="there.",
                            start=401,
                            end=900,
                            confidence=0.97,
                            speaker="A",
                        ),
                    ],
                )
            ],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / "sample.mp4"
            video_path.touch()

            video_utils.cache_transcript_data(video_path, transcript)

            cache_path = video_path.with_suffix(".transcript_cache.json")
            payload = json.loads(cache_path.read_text())

        self.assertEqual(payload["version"], video_utils.TRANSCRIPT_CACHE_SCHEMA_VERSION)
        self.assertEqual(payload["words"][0]["speaker"], "A")
        self.assertEqual(payload["utterances"][0]["speaker"], "A")
        self.assertEqual(payload["utterances"][0]["words"][0]["speaker"], "A")

    @patch("src.video_utils.aai.Transcriber")
    @patch("src.video_utils.aai.TranscriptionConfig")
    def test_get_video_transcript_enables_speaker_labels(
        self, mock_transcription_config, _mock_transcriber
    ):
        runtime_config = SimpleNamespace(
            transcript_provider="assemblyai",
            assembly_ai_api_key="assembly-test-key",
            assembly_ai_http_timeout_seconds=900,
        )
        transcript = SimpleNamespace(
            status=video_utils.aai.TranscriptStatus.completed,
            error=None,
            text="Hello there.",
            words=[
                SimpleNamespace(
                    text="Hello",
                    start=0,
                    end=400,
                    confidence=0.98,
                    speaker="A",
                )
            ],
            utterances=[
                SimpleNamespace(
                    start=0,
                    end=2200,
                    speaker="A",
                    text="Hello there.",
                    words=[],
                )
            ],
        )
        with (
            patch("src.video_utils.get_config", return_value=runtime_config),
            patch(
                "src.video_utils._prepare_audio_for_transcription",
                side_effect=lambda path, _: path,
            ),
            patch(
                "src.video_utils._submit_and_wait_for_assemblyai_transcript",
                return_value=transcript,
            ),
        ):
            with tempfile.TemporaryDirectory() as temp_dir:
                video_path = Path(temp_dir) / "sample.mp4"
                video_path.touch()
                result = video_utils.get_video_transcript(video_path)

        self.assertIn("Speaker A: Hello there.", result)
        mock_transcription_config.assert_called_once()
        self.assertTrue(mock_transcription_config.call_args.kwargs["speaker_labels"])

    def test_transcribe_with_whisper_returns_word_timestamps(self):
        runtime_config = SimpleNamespace(
            whisper_model="small",
            whisper_language="en",
        )
        transcribe_calls = {}

        class FakeModel:
            def transcribe(self, path, **kwargs):
                transcribe_calls["path"] = path
                transcribe_calls["kwargs"] = kwargs
                return {
                    "text": " Hello world.",
                    "segments": [
                        {
                            "start": 0.0,
                            "end": 1.2,
                            "text": " Hello world.",
                            "words": [
                                {
                                    "word": " Hello",
                                    "start": 0.0,
                                    "end": 0.4,
                                    "probability": 0.98,
                                },
                                {
                                    "word": " world.",
                                    "start": 0.4,
                                    "end": 1.2,
                                    "probability": 0.96,
                                },
                            ],
                        }
                    ],
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / "sample.mp4"
            video_path.touch()

            with (
                patch("src.video_utils._load_whisper_model", return_value=FakeModel()),
                patch(
                    "src.video_utils._prepare_audio_for_transcription",
                    side_effect=lambda path, _: path,
                ),
            ):
                transcript = video_utils._transcribe_with_whisper(
                    video_path, runtime_config
                )

        self.assertEqual(transcribe_calls["path"], str(video_path))
        self.assertTrue(transcribe_calls["kwargs"]["word_timestamps"])
        self.assertEqual(transcribe_calls["kwargs"]["language"], "en")
        self.assertEqual(transcript.text, "Hello world.")
        self.assertEqual(transcript.words[0].text, "Hello")
        self.assertEqual(transcript.words[0].start, 0)
        self.assertEqual(transcript.words[1].end, 1200)
        self.assertEqual(transcript.utterances[0].text, "Hello world.")

    def test_get_video_transcript_uses_whisper_provider(self):
        runtime_config = SimpleNamespace(
            transcript_provider="whisper",
            whisper_model="small",
            whisper_language=None,
        )
        transcript = video_utils.TranscriptData(
            text="Hello world.",
            words=[
                video_utils.TranscriptWordData(
                    text="Hello",
                    start=0,
                    end=500,
                    confidence=0.98,
                ),
                video_utils.TranscriptWordData(
                    text="world.",
                    start=500,
                    end=1100,
                    confidence=0.97,
                ),
            ],
            utterances=[
                video_utils.TranscriptUtteranceData(
                    text="Hello world.",
                    start=0,
                    end=1100,
                )
            ],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / "sample.mp4"
            video_path.touch()

            with (
                patch("src.video_utils.get_config", return_value=runtime_config),
                patch(
                    "src.video_utils._transcribe_with_whisper",
                    return_value=transcript,
                ) as mock_whisper,
            ):
                result = video_utils.get_video_transcript(video_path)

            cache_path = video_path.with_suffix(".transcript_cache.json")
            payload = json.loads(cache_path.read_text())

        mock_whisper.assert_called_once_with(video_path, runtime_config)
        self.assertEqual(result, "[00:00 - 00:01] Hello world.")
        self.assertEqual(payload["words"][0]["text"], "Hello")
        self.assertEqual(payload["utterances"][0]["text"], "Hello world.")

    def test_transcribe_with_faster_whisper_returns_word_timestamps(self):
        runtime_config = SimpleNamespace(
            whisper_model="small",
            whisper_language="en",
            faster_whisper_device="cuda",
            faster_whisper_compute_type="float16",
        )
        transcribe_calls = {}

        class FakeWord:
            def __init__(self, word, start, end, probability):
                self.word = word
                self.start = start
                self.end = end
                self.probability = probability

        class FakeSegment:
            def __init__(self):
                self.start = 0.0
                self.end = 1.2
                self.text = " Hello world."
                self.words = [
                    FakeWord(" Hello", 0.0, 0.4, 0.98),
                    FakeWord(" world.", 0.4, 1.2, 0.96),
                ]

        class FakeModel:
            def transcribe(self, path, **kwargs):
                transcribe_calls["path"] = path
                transcribe_calls["kwargs"] = kwargs
                return [FakeSegment()], SimpleNamespace(language="en")

        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / "sample.mp4"
            video_path.touch()

            with (
                patch(
                    "src.video_utils._load_faster_whisper_model",
                    return_value=FakeModel(),
                ) as mock_loader,
                patch(
                    "src.video_utils._prepare_audio_for_transcription",
                    side_effect=lambda path, _: path,
                ),
            ):
                transcript = video_utils._transcribe_with_faster_whisper(
                    video_path, runtime_config
                )

        mock_loader.assert_called_once_with("small", "cuda", "float16")
        self.assertEqual(transcribe_calls["path"], str(video_path))
        self.assertTrue(transcribe_calls["kwargs"]["word_timestamps"])
        self.assertEqual(transcribe_calls["kwargs"]["language"], "en")
        self.assertEqual(transcript.text, "Hello world.")
        self.assertEqual(transcript.words[0].text, "Hello")
        self.assertEqual(transcript.words[1].end, 1200)

    def test_get_video_transcript_uses_faster_whisper_provider(self):
        runtime_config = SimpleNamespace(
            transcript_provider="faster_whisper",
            whisper_model="small",
            whisper_language=None,
            faster_whisper_device="auto",
            faster_whisper_compute_type="default",
        )
        transcript = video_utils.TranscriptData(
            text="Hello world.",
            words=[
                video_utils.TranscriptWordData(
                    text="Hello",
                    start=0,
                    end=500,
                    confidence=0.98,
                ),
                video_utils.TranscriptWordData(
                    text="world.",
                    start=500,
                    end=1100,
                    confidence=0.97,
                ),
            ],
            utterances=[
                video_utils.TranscriptUtteranceData(
                    text="Hello world.",
                    start=0,
                    end=1100,
                )
            ],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / "sample.mp4"
            video_path.touch()

            with (
                patch("src.video_utils.get_config", return_value=runtime_config),
                patch(
                    "src.video_utils._transcribe_with_faster_whisper",
                    return_value=transcript,
                ) as mock_faster_whisper,
            ):
                result = video_utils.get_video_transcript(video_path)

            cache_path = video_path.with_suffix(".transcript_cache.json")
            payload = json.loads(cache_path.read_text())

        mock_faster_whisper.assert_called_once_with(video_path, runtime_config)
        self.assertEqual(result, "[00:00 - 00:01] Hello world.")
        self.assertEqual(payload["words"][0]["text"], "Hello")
        self.assertEqual(payload["utterances"][0]["text"], "Hello world.")

    def test_load_cached_transcript_data_supports_legacy_word_only_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / "sample.mp4"
            video_path.touch()
            cache_path = video_path.with_suffix(".transcript_cache.json")
            cache_path.write_text(
                json.dumps(
                    {
                        "words": [
                            {"text": "legacy", "start": 0, "end": 300, "confidence": 1.0}
                        ],
                        "text": "legacy",
                    }
                )
            )

            payload = video_utils.load_cached_transcript_data(video_path)

        self.assertIsNotNone(payload)
        self.assertEqual(payload["words"][0]["text"], "legacy")


if __name__ == "__main__":
    unittest.main()
