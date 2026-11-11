"""A supplied .srt gets its own analysis and checkpoint identity.

Runs without subtitles must hash exactly as they did before the feature, or
every queued/paid task would look new and be repeated.
"""
import hashlib

from src.ai import TRANSCRIPT_ANALYSIS_CACHE_VERSION
from src.repositories.task_checkpoint_repository import checkpoint_input_key
from src.services.task_service import TaskService


def analysis_key(**kwargs):
    return TaskService._build_cache_key("upload://clip.mp4", "upload", "fast", **kwargs)


def test_analysis_key_is_unchanged_without_subtitles():
    expected = hashlib.sha256(
        f"upload|fast|{TRANSCRIPT_ANALYSIS_CACHE_VERSION}|upload://clip.mp4".encode()
    ).hexdigest()

    assert analysis_key() == expected
    assert analysis_key(quality=None, srt_digest=None) == expected


def test_analysis_key_differs_when_subtitles_are_supplied():
    plain = analysis_key()
    digest = "a" * 64

    assert analysis_key(srt_digest=digest) != plain
    assert analysis_key(srt_digest=digest) == analysis_key(srt_digest=digest)
    assert analysis_key(srt_digest=digest) != analysis_key(srt_digest="b" * 64)


def test_quality_suffix_still_applies_to_youtube():
    expected = hashlib.sha256(
        f"youtube|fast|{TRANSCRIPT_ANALYSIS_CACHE_VERSION}|https://youtu.be/x|q720".encode()
    ).hexdigest()

    assert TaskService._build_cache_key("https://youtu.be/x", "youtube", "fast", "720") == expected


class StubConfig:
    transcription_provider = "whisper"
    transcription_language = ""
    whisper_model = "small"


def test_checkpoint_key_is_unchanged_without_subtitles():
    without_argument = checkpoint_input_key("upload://clip.mp4", "upload", "fast", "auto", StubConfig())
    explicit_none = checkpoint_input_key(
        "upload://clip.mp4", "upload", "fast", "auto", StubConfig(), None
    )

    assert without_argument == explicit_none


def test_checkpoint_key_differs_with_subtitles():
    plain = checkpoint_input_key("upload://clip.mp4", "upload", "fast", "auto", StubConfig())
    digest = "c" * 64

    assert checkpoint_input_key(
        "upload://clip.mp4", "upload", "fast", "auto", StubConfig(), digest
    ) != plain