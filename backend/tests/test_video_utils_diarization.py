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


class VideoUtilsHardCutReframeTests(unittest.TestCase):
    """Diarization-driven hard-cut reframe helpers (ENG-5595)."""

    def test_get_utterances_for_keep_ranges_rebases_to_clip_time(self):
        transcript_data = {
            "utterances": [
                {"start": 10_000, "end": 12_000, "speaker": "A"},
                {"start": 12_000, "end": 14_000, "speaker": "B"},
                {"start": 30_000, "end": 31_000, "speaker": "A"},
            ]
        }
        # Two kept ranges (seconds) are concatenated into one clip timeline.
        keep_ranges = [(10.0, 14.0), (30.0, 31.0)]

        projected = video_utils.get_utterances_for_keep_ranges(
            transcript_data, keep_ranges
        )

        self.assertEqual(
            projected,
            [
                {"start": 0.0, "end": 2.0, "speaker": "A"},
                {"start": 2.0, "end": 4.0, "speaker": "B"},
                # Second range starts at clip-relative offset 4.0 (= 14-10).
                {"start": 4.0, "end": 5.0, "speaker": "A"},
            ],
        )

    def test_get_utterances_for_keep_ranges_skips_unlabeled(self):
        transcript_data = {
            "utterances": [
                {"start": 0, "end": 1000, "speaker": None},
                {"start": 1000, "end": 2000, "speaker": "A"},
            ]
        }
        projected = video_utils.get_utterances_for_keep_ranges(
            transcript_data, [(0.0, 2.0)]
        )
        self.assertEqual(projected, [{"start": 1.0, "end": 2.0, "speaker": "A"}])

    def test_map_speaker_labels_to_sides_uses_region_motion(self):
        # Sampled every 1s; left motion dominates 0-3s, right motion 3-6s.
        times = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
        left_values = [10.0, 10.0, 10.0, 1.0, 1.0, 1.0]
        right_values = [1.0, 1.0, 1.0, 10.0, 10.0, 10.0]
        utterances = [
            {"start": 0.0, "end": 2.5, "speaker": "A"},
            {"start": 3.0, "end": 5.5, "speaker": "B"},
        ]

        mapping = video_utils.map_speaker_labels_to_sides(
            utterances, times, left_values, right_values
        )

        self.assertEqual(mapping["A"], "left")
        self.assertEqual(mapping["B"], "right")

    def test_build_speaker_timeline_from_utterances_cuts_on_speaker_change(self):
        utterances = [
            {"start": 0.0, "end": 3.0, "speaker": "A"},
            {"start": 3.0, "end": 6.0, "speaker": "B"},
            {"start": 6.0, "end": 9.0, "speaker": "A"},
        ]
        label_to_side = {"A": "left", "B": "right"}

        timeline = video_utils.build_speaker_timeline_from_utterances(
            utterances, label_to_side, min_duration=1.0
        )

        self.assertEqual([seg["speaker"] for seg in timeline], ["left", "right", "left"])
        # Cut boundary lands where the next speaker begins.
        self.assertAlmostEqual(timeline[0]["end"], 3.0)
        self.assertAlmostEqual(timeline[1]["end"], 6.0)

    def test_build_speaker_timeline_debounces_short_turns(self):
        # A short 0.3s interjection from B should be absorbed into A's turn.
        utterances = [
            {"start": 0.0, "end": 4.0, "speaker": "A"},
            {"start": 4.0, "end": 4.3, "speaker": "B"},
            {"start": 4.3, "end": 8.0, "speaker": "A"},
        ]
        label_to_side = {"A": "left", "B": "right"}

        timeline = video_utils.build_speaker_timeline_from_utterances(
            utterances, label_to_side, min_duration=1.5
        )

        self.assertEqual([seg["speaker"] for seg in timeline], ["left"])

    def test_build_speaker_timeline_coalesces_same_side(self):
        utterances = [
            {"start": 0.0, "end": 2.0, "speaker": "A"},
            {"start": 2.0, "end": 4.0, "speaker": "C"},  # C also maps left
        ]
        label_to_side = {"A": "left", "C": "left"}

        timeline = video_utils.build_speaker_timeline_from_utterances(
            utterances, label_to_side, min_duration=1.0
        )

        self.assertEqual(len(timeline), 1)
        self.assertEqual(timeline[0]["speaker"], "left")

    def test_single_speaker_maps_to_one_side_triggers_static_fallback(self):
        # One diarized speaker -> only one side in the mapping. detect_speaker_reframe_plan
        # treats len(set(sides)) < 2 as "no cuts" and falls back to the static crop.
        times = [0.0, 1.0, 2.0, 3.0]
        left_values = [5.0, 5.0, 5.0, 5.0]
        right_values = [1.0, 1.0, 1.0, 1.0]
        utterances = [{"start": 0.0, "end": 3.5, "speaker": "A"}]

        mapping = video_utils.map_speaker_labels_to_sides(
            utterances, times, left_values, right_values
        )

        self.assertEqual(set(mapping.values()), {"left"})
        self.assertLess(len(set(mapping.values())), 2)


class VideoUtilsPerShotReframeTests(unittest.TestCase):
    """Per-shot reframing helpers for heavily-edited clips (ENG-5595)."""

    def test_build_shot_boundaries_segments_at_cuts(self):
        shots = video_utils.build_shot_boundaries([5.0, 12.0], 20.0)
        self.assertEqual(shots, [(0.0, 5.0), (5.0, 12.0), (12.0, 20.0)])

    def test_build_shot_boundaries_ignores_out_of_range_and_dupes(self):
        # cuts at/after duration or <=0 are dropped; duplicates collapsed
        shots = video_utils.build_shot_boundaries([0.0, 5.0, 5.0, 25.0], 20.0)
        self.assertEqual(shots, [(0.0, 5.0), (5.0, 20.0)])

    def test_build_shot_boundaries_no_cuts_is_single_shot(self):
        self.assertEqual(video_utils.build_shot_boundaries([], 10.0), [(0.0, 10.0)])

    def test_weighted_face_center_x_frames_single_face(self):
        # one face centred at x=900 in a 1920-wide frame, crop 606 wide
        crop_x = video_utils.weighted_face_center_x([(900, 540, 10000, 0.9)], 1920, 606)
        self.assertEqual(crop_x, video_utils.clamp_even(900 - 303, 0, 1920 - 606))

    def test_weighted_face_center_x_clamps_and_handles_empty(self):
        self.assertIsNone(video_utils.weighted_face_center_x([], 1920, 606))
        # face hard against the right edge clamps to max offset (even)
        crop_x = video_utils.weighted_face_center_x([(1900, 540, 5000, 0.8)], 1920, 606)
        self.assertEqual(crop_x, video_utils.round_to_even(1920 - 606))

    def test_merge_x_segments_coalesces_near_equal_and_short(self):
        segments = [
            {"start": 0.0, "end": 4.0, "x": 100},
            {"start": 4.0, "end": 8.0, "x": 102},   # within tol of 100 -> merge
            {"start": 8.0, "end": 8.3, "x": 700},    # too short -> absorbed
            {"start": 8.3, "end": 14.0, "x": 700},
        ]
        merged = video_utils.merge_x_segments(segments, tol=5, min_duration=0.6)
        self.assertEqual(
            merged,
            [
                {"start": 0.0, "end": 8.3, "x": 100},
                {"start": 8.3, "end": 14.0, "x": 700},
            ],
        )

    def test_build_step_x_expression_hard_cuts_between_offsets(self):
        segments = [
            {"start": 0.0, "end": 5.0, "x": 100},
            {"start": 5.0, "end": 12.0, "x": 700},
            {"start": 12.0, "end": 20.0, "x": 300},
        ]
        expr = video_utils.build_step_x_expression(segments)
        # t<5 -> 100, t<12 -> 700, else 300 (last segment's end is unused)
        self.assertEqual(
            expr,
            "if(lt(t\\,5.0000)\\,100\\,if(lt(t\\,12.0000)\\,700\\,300))",
        )

    def test_build_step_x_expression_empty(self):
        self.assertIsNone(video_utils.build_step_x_expression([]))


if __name__ == "__main__":
    unittest.main()
