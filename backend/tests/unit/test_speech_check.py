from types import SimpleNamespace

import pytest

from src.services import video_service as video_service_module
from src.services.video_service import VideoService
from src.speech_check import (
    LOW_SPEECH_MESSAGE,
    count_spoken_words,
    is_no_speech_error,
    low_speech_details,
)
from src.workflows.errors import WorkflowError


def test_counts_word_timings_and_skips_blank_tokens():
    data = {"words": [{"text": "hello"}, {"text": " "}, {"text": "there"}]}
    assert count_spoken_words(data, "ignored text") == 2


def test_counts_transcript_text_without_timestamps_or_speakers():
    text = "[00:06 - 00:15] Speaker A: Hey there friend\n[01:02:03 - 01:02:05] okay"
    assert count_spoken_words(None, text) == 4
    assert count_spoken_words({"words": []}, "") == 0


def test_flags_sparse_speech_below_the_rate():
    # 1,635 words in 38 minutes of gameplay is about 43 a minute.
    assert low_speech_details(1635, 38 * 60, 45.0) == {"words": 1635, "words_per_minute": 43}
    assert low_speech_details(1635, 38 * 60, 40.0) is None
    assert low_speech_details(0, 600, 40.0) == {"words": 0, "words_per_minute": 0}


def test_unknown_length_flags_only_silence_and_zero_turns_it_off():
    assert low_speech_details(0, None, 40.0) == {"words": 0, "words_per_minute": 0}
    assert low_speech_details(12, None, 40.0) is None
    assert low_speech_details(0, 600, 0) is None


def test_recognizes_assemblyai_no_speech_error():
    error = Exception(
        "Transcription failed: language_detection cannot be performed on files with no spoken audio."
    )
    assert is_no_speech_error(error)
    assert not is_no_speech_error(Exception("Transcription failed: 503"))


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"placeholder")
    config = SimpleNamespace(
        max_video_duration=5400, clip_duration=30, max_clips=10,
        fast_mode_max_clips=4, low_speech_words_per_minute=40.0,
    )
    monkeypatch.setattr(video_service_module, "get_config", lambda: config)
    monkeypatch.setattr(video_service_module, "materialize_stored_upload", _async(None))
    monkeypatch.setattr(video_service_module, "upload_needs_remote_copy", _async(False))
    monkeypatch.setattr(VideoService, "resolve_local_video_path", staticmethod(lambda _url: video_path))
    monkeypatch.setattr(VideoService, "_get_file_duration", staticmethod(lambda _path: 600.0))
    def no_word_timings(_path):
        return None

    monkeypatch.setattr(video_service_module, "load_cached_transcript_data", no_word_timings)

    async def run_in_thread(func, *args, **kwargs):
        # Only the speech count runs; storage and media helpers are skipped.
        if func == VideoService._drop_static_segments:
            return args[1]
        return func() if getattr(func, "__name__", "") == "<lambda>" else None

    monkeypatch.setattr(video_service_module, "run_in_thread", run_in_thread)
    analyzed = []

    async def analyze(transcript, **_kwargs):
        analyzed.append(transcript)
        return SimpleNamespace(summary="", key_topics=[], most_relevant_segments=[])

    monkeypatch.setattr(VideoService, "analyze_transcript", staticmethod(analyze))

    def transcribe_with(result):
        async def transcribe(_path, processing_mode="balanced", source_url=None):
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setattr(VideoService, "generate_transcript", staticmethod(transcribe))

    return SimpleNamespace(transcribe_with=transcribe_with, analyzed=analyzed)


def _async(value):
    async def returns(*_args, **_kwargs):
        return value

    return returns


async def _process(**kwargs):
    return await VideoService.process_video_complete(
        url="upload://source.mp4", source_type="video_url", processing_mode="fast", **kwargs
    )


@pytest.mark.asyncio
async def test_sparse_speech_stops_before_analysis(pipeline):
    pipeline.transcribe_with("[00:00 - 00:05] Speaker A: move move go go go")  # 5 words in 10 min
    with pytest.raises(WorkflowError) as error:
        await _process(speech_check=True)
    assert error.value.code == "low_speech"
    assert error.value.message == LOW_SPEECH_MESSAGE
    assert error.value.details == {"words": 5, "words_per_minute": 0}
    assert pipeline.analyzed == []


@pytest.mark.asyncio
async def test_without_the_check_sparse_speech_still_makes_clips(pipeline):
    pipeline.transcribe_with("[00:00 - 00:05] Speaker A: move move go go go")
    result = await _process()
    assert pipeline.analyzed and result["segments_to_render"]


@pytest.mark.asyncio
async def test_enough_speech_passes_the_check(pipeline):
    pipeline.transcribe_with("[00:00 - 10:00] Speaker A: " + "word " * 500)  # 50 a minute
    result = await _process(speech_check=True)
    assert pipeline.analyzed and result["segments_to_render"]


@pytest.mark.asyncio
async def test_no_spoken_audio_becomes_the_notice(pipeline):
    pipeline.transcribe_with(Exception(
        "Transcription failed: language_detection cannot be performed on files with no spoken audio."
    ))
    with pytest.raises(WorkflowError) as error:
        await _process(speech_check=True)
    assert error.value.code == "low_speech"
    assert error.value.details == {"words": 0, "words_per_minute": 0}

    # Once the user asked for clips anyway, the transcription error is the real one.
    with pytest.raises(Exception, match="no spoken audio"):
        await _process()
