import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.config import Config, set_config_override
from src.repositories.task_checkpoint_repository import checkpoint_input_key
from src.services import video_service as module
from src.services.video_service import VideoService


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    config = Config()
    config.temp_dir = str(tmp_path)
    config.media_storage = "local"
    config.transcription_provider = "assemblyai"
    config.transcription_language = "en"
    config.max_clips = 2
    set_config_override(config)
    source = tmp_path / "audio.m4a"
    source.write_bytes(b"audio")
    render = tmp_path / "render.mp4"
    render.write_bytes(b"video")
    words = {"words": [{"text": "Hello", "start": 0, "end": 500}], "text": "Hello"}
    segments = [{"start_time": "00:00", "end_time": "00:20", "text": "Hello",
                 "relevance_score": .9, "virality": {"total_score": 80}}]

    async def transcribe(*_args, **_kwargs):
        source.with_suffix(".transcript_cache.json").write_text(json.dumps(words))
        return "[00:00 - 00:20] Hello"

    transcription = AsyncMock(side_effect=transcribe)
    analysis = AsyncMock(return_value=SimpleNamespace(
        summary="A useful conversation", key_topics=["hello"], most_relevant_segments=segments,
    ))
    monkeypatch.setattr(module, "async_get_youtube_video_info", AsyncMock(return_value={"duration": 120}))
    monkeypatch.setattr(module, "cached_full_media", lambda *_: None)
    monkeypatch.setattr(module, "proxy_configured", lambda *_: True)
    monkeypatch.setattr(module, "download_analysis_source", lambda *a, **kw: (source, True))
    monkeypatch.setattr(module, "cleanup_downloaded_files", lambda *a, **kw: None)
    monkeypatch.setattr(module, "persist_cache_sidecar", lambda *_: None)
    monkeypatch.setattr(module, "MediaStorage", lambda: SimpleNamespace(save_source_metadata=lambda *_: None))
    monkeypatch.setattr(module, "build_clip_signal_summary", lambda *_: None)
    monkeypatch.setattr(VideoService, "prepare_source", AsyncMock(side_effect=lambda path: path))
    monkeypatch.setattr(VideoService, "_get_file_duration", lambda _: 120)
    monkeypatch.setattr(VideoService, "generate_transcript", transcription)
    monkeypatch.setattr(VideoService, "analyze_transcript", analysis)
    monkeypatch.setattr(VideoService, "_refine_segment_openings", lambda s, *_: s)
    monkeypatch.setattr(VideoService, "_drop_static_segments", lambda _, s: s)
    monkeypatch.setattr(VideoService, "download_video", AsyncMock(return_value=None))
    state = {"checkpoint": {}, "fail_sections": True, "downloaded_plans": []}

    def materialize(_url, _id, selected, **_kwargs):
        state["downloaded_plans"].append(copy.deepcopy(selected))
        if state["fail_sections"]:
            raise RuntimeError("Tunnel connection failed: 502 NO_HOST_CONNECTION")
        return render

    monkeypatch.setattr(module, "materialize_clip_source", materialize)

    async def save(patch):
        state["checkpoint"].update(copy.deepcopy(patch))

    async def run():
        return await VideoService.process_video_complete(
            "https://youtu.be/abcdefghijk", "youtube", task_id="task-1",
            checkpoint=copy.deepcopy(state["checkpoint"]), checkpoint_callback=save,
        )

    yield SimpleNamespace(run=run, state=state, source=source, words=words,
                          transcription=transcription, analysis=analysis, config=config)
    set_config_override(None)


@pytest.mark.asyncio
async def test_section_failure_reuses_paid_stages_and_restores_missing_word_timings(pipeline):
    p = pipeline
    with pytest.raises(Exception, match="Failed to download selected segments"):
        await p.run()
    assert p.state["checkpoint"]["transcript_data"]["words"] == p.words["words"]
    assert p.state["checkpoint"]["analysis"]
    assert p.state["checkpoint"]["candidates"]
    assert "segments" not in p.state["checkpoint"]
    p.source.with_suffix(".transcript_cache.json").unlink()
    p.state["fail_sections"] = False
    result = await p.run()
    p.transcription.assert_awaited_once()
    p.analysis.assert_awaited_once()
    assert p.state["downloaded_plans"][0] == p.state["downloaded_plans"][1]
    assert json.loads(p.source.with_suffix(".transcript_cache.json").read_text())["words"] == p.words["words"]
    assert p.state["checkpoint"]["segments"] == result["segments_to_render"]


@pytest.mark.asyncio
async def test_analysis_failure_does_not_repeat_transcription(pipeline):
    p = pipeline
    answer = p.analysis.return_value
    p.analysis.side_effect = [RuntimeError("provider unavailable"), answer]
    with pytest.raises(RuntimeError, match="provider unavailable"):
        await p.run()
    assert p.state["checkpoint"]["transcript"]
    assert "analysis" not in p.state["checkpoint"]
    p.state["fail_sections"] = False
    await p.run()
    p.transcription.assert_awaited_once()
    assert p.analysis.await_count == 2


@pytest.mark.asyncio
async def test_final_plan_is_frozen_even_if_later_visual_review_would_differ(pipeline, monkeypatch):
    p = pipeline
    p.state["fail_sections"] = False
    first = await p.run()
    monkeypatch.setattr(VideoService, "_drop_static_segments", lambda *_: [])
    monkeypatch.setattr(VideoService, "_refine_segment_openings", lambda *_: [])
    second = await p.run()
    assert first["segments_to_render"] == second["segments_to_render"]
    p.analysis.assert_awaited_once()
    p.transcription.assert_awaited_once()


@pytest.mark.asyncio
async def test_empty_analysis_with_saved_fallback_does_not_repeat_ai(pipeline):
    p = pipeline
    p.analysis.return_value.most_relevant_segments = []
    with pytest.raises(Exception, match="Failed to download selected segments"):
        await p.run()
    p.state["fail_sections"] = False
    await p.run()
    p.analysis.assert_awaited_once()


def test_checkpoint_keys_invalidate_changed_inputs_but_not_styling(pipeline):
    config = pipeline.config
    def key(url="upload://one.mp4", mode="balanced", length="auto"):
        return checkpoint_input_key(url, "upload", mode, length, config)
    original = key()
    assert key(url="upload://two.mp4") != original
    assert key(mode="fast") != original
    assert key(length="long") != original
    config.transcription_language = "de"
    assert key() != original


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata_failure", ["geo", "timeout"])
async def test_metadata_failure_still_uses_saved_full_media(pipeline, monkeypatch, metadata_failure):
    from src.youtube_proxy import YouTubeGeoRestrictedError
    p = pipeline
    failure = YouTubeGeoRestrictedError() if metadata_failure == "geo" else TimeoutError("deadline")
    monkeypatch.setattr(module, "async_get_youtube_video_info", AsyncMock(side_effect=failure))
    monkeypatch.setattr(module, "cached_full_media", lambda *_: p.source)
    downloaded = AsyncMock(side_effect=AssertionError("Saved source should be used"))
    monkeypatch.setattr(VideoService, "download_video", downloaded)

    result = await p.run()

    assert result["video_path"] == str(p.source)
    p.transcription.assert_awaited_once()
    downloaded.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata_failure", ["geo", "timeout"])
async def test_metadata_and_audio_failure_still_tries_independent_full_provider(pipeline, monkeypatch, metadata_failure):
    from unittest.mock import Mock
    from src import youtube_source
    from src.youtube_proxy import YouTubeGeoRestrictedError
    p = pipeline
    p.config.youtube_source_cache = False
    failure = YouTubeGeoRestrictedError() if metadata_failure == "geo" else TimeoutError("deadline")
    monkeypatch.setattr(module, "async_get_youtube_video_info", AsyncMock(side_effect=failure))
    monkeypatch.setattr(module, "download_analysis_source", youtube_source.download_analysis_source)
    monkeypatch.setattr(youtube_source, "download_audio_for_job", Mock(side_effect=YouTubeGeoRestrictedError()))
    full = Mock(return_value=p.source)
    monkeypatch.setattr(module, "download_youtube_video", full)

    result = await p.run()

    assert result["video_path"] == str(p.source)
    assert full.call_args.kwargs["skip_proxy"] is True
    p.transcription.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_shares_metadata_route_budget_with_media(pipeline, monkeypatch):
    from unittest.mock import Mock
    p = pipeline
    metadata = AsyncMock(return_value={"duration": 120})
    media = Mock(return_value=(p.source, False))
    monkeypatch.setattr(module, "async_get_youtube_video_info", metadata)
    monkeypatch.setattr(module, "download_analysis_source", media)

    await p.run()

    assert metadata.call_args.kwargs["budget"] is media.call_args.kwargs["budget"]


@pytest.mark.asyncio
async def test_worker_does_not_mask_storage_failure_after_geo_sections(pipeline, monkeypatch):
    from unittest.mock import Mock
    from src.youtube_proxy import YouTubeGeoRestrictedError
    p = pipeline
    monkeypatch.setattr(module, "materialize_clip_source", Mock(side_effect=YouTubeGeoRestrictedError()))
    error = OSError("disk full while restoring media")
    fallback = AsyncMock(side_effect=error)
    monkeypatch.setattr(VideoService, "download_video", fallback)

    with pytest.raises(OSError) as result:
        await p.run()

    assert result.value is error
    assert error.__context__ is None
    fallback.assert_awaited_once()
