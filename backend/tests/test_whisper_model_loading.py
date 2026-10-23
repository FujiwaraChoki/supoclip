"""Concurrent transcription jobs must not overwrite shared Whisper checkpoints."""

import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.media import transcription


class WhisperModelLoadingTests(unittest.TestCase):
    def test_concurrent_jobs_load_one_shared_model(self):
        ready = Barrier(6)
        model = object()

        def load_model(name):
            time.sleep(0.05)
            return model

        loader = Mock(side_effect=load_model)

        def request_model():
            ready.wait(timeout=5)
            return transcription._get_whisper_model("base")

        with (
            patch.object(transcription, "_WHISPER_AVAILABLE", True),
            patch.object(transcription, "_WHISPER_MODEL_CACHE", {}),
            patch.object(transcription, "_whisper", SimpleNamespace(load_model=loader)),
            ThreadPoolExecutor(max_workers=6) as pool,
        ):
            futures = [pool.submit(request_model) for _ in range(6)]
            results = [future.result(timeout=5) for future in futures]

        loader.assert_called_once_with("base")
        self.assertTrue(all(result is model for result in results))

    def test_failed_load_releases_lock_and_can_be_retried(self):
        model = object()
        loader = Mock(side_effect=[RuntimeError("download interrupted"), model])
        cache = {}
        with (
            patch.object(transcription, "_WHISPER_AVAILABLE", True),
            patch.object(transcription, "_WHISPER_MODEL_CACHE", cache),
            patch.object(transcription, "_whisper", SimpleNamespace(load_model=loader)),
        ):
            with self.assertRaisesRegex(RuntimeError, "download interrupted"):
                transcription._get_whisper_model("base")
            self.assertNotIn("base", cache)
            self.assertIs(transcription._get_whisper_model("base"), model)
            self.assertIs(transcription._get_whisper_model("base"), model)

        self.assertEqual(loader.call_count, 2)
