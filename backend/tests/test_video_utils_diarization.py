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
        self, mock_transcription_config, mock_transcriber
    ):
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
        with patch(
            "src.video_utils._submit_and_wait_for_assemblyai_transcript",
            return_value=transcript,
        ):
            with tempfile.TemporaryDirectory() as temp_dir:
                video_path = Path(temp_dir) / "sample.mp4"
                video_path.touch()
                result = video_utils.get_video_transcript(video_path)

        self.assertIn("Speaker A: Hello there.", result)
        mock_transcription_config.assert_called_once()
        self.assertTrue(mock_transcription_config.call_args.kwargs["speaker_labels"])

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

<<<<<<< HEAD
    def test_assemblyai_speech_models_value_maps_legacy_aliases(self):
        # AssemblyAI deprecated the singular speech_model; the plural list must
        # only contain universal-3-pro and universal-2.
        self.assertEqual(
            video_utils._assemblyai_speech_models_value("nano"), ["universal-2"]
        )
        self.assertEqual(
            video_utils._assemblyai_speech_models_value("universal-2"),
            ["universal-2"],
        )
        # Everything else (best, universal, default) prefers universal-3-pro
        # with universal-2 as the fallback.
        self.assertEqual(
            video_utils._assemblyai_speech_models_value("best"),
            ["universal-3-pro", "universal-2"],
        )
        self.assertEqual(
            video_utils._assemblyai_speech_models_value("universal"),
            ["universal-3-pro", "universal-2"],
        )
        self.assertEqual(
            video_utils._assemblyai_speech_models_value(None),
            ["universal-3-pro", "universal-2"],
        )

    def test_get_video_transcript_dispatches_to_whisper(self):
        whisper_result = {
            "text": "Hello there. General Kenobi.",
            "segments": [
                {
                    "start": 0.0,
                    "end": 2.2,
                    "text": "Hello there.",
                    "words": [
                        {"word": "Hello", "start": 0.0, "end": 0.4, "probability": 0.98},
                    ],
                },
                {
                    "start": 2.2,
                    "end": 4.6,
                    "text": "General Kenobi.",
                    "words": [
                        {"word": "General", "start": 2.2, "end": 3.0, "probability": 0.99},
                        {"word": "Kenobi.", "start": 3.0, "end": 4.6, "probability": 0.99},
                    ],
                },
            ],
        }

        mock_config = SimpleNamespace(
            transcription_provider="whisper", whisper_model="base"
        )
        with patch("src.video_utils.get_config", return_value=mock_config), patch(
            "src.video_utils.transcribe_with_whisper", return_value=whisper_result
        ) as mock_transcribe:
            with tempfile.TemporaryDirectory() as temp_dir:
                video_path = Path(temp_dir) / "sample.mp4"
                video_path.touch()
                result = video_utils.get_video_transcript(video_path)

        self.assertIn("Hello there.", result)
        self.assertIn("General Kenobi.", result)
        mock_transcribe.assert_called_once()

    def test_format_transcript_for_analysis_handles_whisper_dict(self):
        whisper_result = {
            "text": "Hello there. General Kenobi.",
            "segments": [
                {
                    "start": 0.0,
                    "end": 2.2,
                    "text": " Hello there.",
                    "words": [
                        {"word": "Hello", "start": 0.0, "end": 0.4, "probability": 0.98},
                    ],
                },
                {
                    "start": 2.2,
                    "end": 4.6,
                    "text": " General Kenobi.",
                    "words": [
                        {"word": "General", "start": 2.2, "end": 3.0, "probability": 0.99},
                    ],
                },
            ],
        }
        formatted = video_utils.format_transcript_for_analysis(whisper_result)
        self.assertEqual(
            formatted,
            [
                "[00:00 - 00:02] Hello there.",
                "[00:02 - 00:04] General Kenobi.",
            ],
        )

    def test_cache_transcript_data_handles_whisper_dict(self):
        whisper_result = {
            "text": "Hello there.",
            "segments": [
                {
                    "start": 0.0,
                    "end": 0.9,
                    "text": "Hello there.",
                    "words": [
                        {"word": "Hello", "start": 0.0, "end": 0.4, "probability": 0.98},
                        {"word": "there.", "start": 0.4, "end": 0.9, "probability": 0.97},
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / "sample.mp4"
            video_path.touch()
            video_utils.cache_transcript_data(video_path, whisper_result)
            cache_path = video_path.with_suffix(".transcript_cache.json")
            payload = json.loads(cache_path.read_text())

        self.assertEqual(
            payload["version"], video_utils.TRANSCRIPT_CACHE_SCHEMA_VERSION
        )
        # Whisper seconds are stored as milliseconds in the cache.
        self.assertEqual(payload["words"][0]["text"], "Hello")
        self.assertEqual(payload["words"][0]["start"], 0)
        self.assertEqual(payload["words"][0]["end"], 400)
        self.assertEqual(payload["utterances"][0]["text"], "Hello there.")
        self.assertIsNone(payload["utterances"][0]["speaker"])
=======
    def test_format_transcript_for_analysis_splits_long_diarized_utterances(self):
        words = []
        for index, token in enumerate(
            [
                "This",
                "is",
                "a",
                "very",
                "long",
                "utterance",
                "that",
                "should",
                "be",
                "split",
                "into",
                "multiple",
                "segments.",
            ]
        ):
            start = index * 5000
            words.append(
                SimpleNamespace(
                    text=token,
                    start=start,
                    end=start + 1500,
                    confidence=0.99,
                    speaker="A",
                )
            )

        transcript = SimpleNamespace(
            utterances=[
                SimpleNamespace(
                    start=0,
                    end=65000,
                    speaker="A",
                    text=" ".join(word.text for word in words),
                    words=words,
                )
            ],
            words=words,
        )

        formatted = video_utils.format_transcript_for_analysis(transcript)

        self.assertGreater(len(formatted), 1)
        self.assertEqual(
            formatted[0],
            "[00:00 - 00:36] Speaker A: This is a very long utterance that should",
        )

    def test_get_transcript_text_in_range_reconstructs_exact_words(self):
        transcript_data = {
            "words": [
                {"text": "Hello", "start": 0, "end": 400, "confidence": 1.0},
                {"text": "world.", "start": 401, "end": 900, "confidence": 1.0},
                {"text": "Again", "start": 901, "end": 1300, "confidence": 1.0},
            ]
        }

        text = video_utils.get_transcript_text_in_range(transcript_data, 0.0, 0.95)

        self.assertEqual(text, "Hello world. Again")
>>>>>>> ce494c7 (fix(backend): ground clip transcripts to real spans)


if __name__ == "__main__":
    unittest.main()
