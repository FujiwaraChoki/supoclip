"""A failed generation download keeps each attempt's redacted reason.

The task page keeps the short "Failed to download ..." message; the reasons go
to the worker log and the exception's traceback note, without proxy
credentials or signed media URLs.
"""

import logging
import traceback
from unittest.mock import AsyncMock, Mock

import pytest

from src import youtube_source
from src.services import video_service as module
from src.services.task_service import user_facing_error
from src.youtube_proxy import (
    DownloadBudgetExceeded,
    YouTubeDownloadFailedError,
    YouTubeGeoRestrictedError,
)

from tests.unit.test_processing_checkpoints import pipeline  # noqa: F401
from tests.unit.test_task_service import build_task_service

URL = "https://www.youtube.com/watch?v=abcdefghijk"
VIDEO_ID = "abcdefghijk"
SECRET = "s3cretpass"
PROXY_FAILURE = f"Tunnel failed via http://user-country-de:{SECRET}@proxy.example:80"
SIGNED_URL = "https://rr1---sn.googlevideo.com/videoplayback?sig=abc&expire=1"


def failing(error):
    """A plain function: the download thread helper reads ``__name__``."""
    def fail(*_args, **_kwargs):
        raise error
    return fail


def assert_redacted(error: YouTubeDownloadFailedError):
    shown = "".join(traceback.format_exception(error))
    assert SECRET not in shown and "googlevideo" not in shown
    assert SECRET not in error.reasons_text() and "googlevideo" not in error.reasons_text()


@pytest.mark.asyncio
async def test_section_fallback_failure_keeps_every_reason(pipeline):  # noqa: F811
    async def full_download(*_args, errors=None, **_kwargs):
        errors.append(f"HTTP Error 403: Forbidden for {SIGNED_URL}")
        errors.append("Sign in to confirm you're not a bot")
        return None

    module.VideoService.download_video.side_effect = full_download
    pipeline.state["fail_sections"] = True

    with pytest.raises(YouTubeDownloadFailedError) as raised:
        await pipeline.run()

    error = raised.value
    assert str(error) == "Failed to download selected segments"
    assert user_facing_error(error) == "Failed to download selected segments"
    assert error.reasons == (
        "Tunnel connection failed: 502 NO_HOST_CONNECTION",
        "HTTP Error 403: Forbidden for [url]",
        "Sign in to confirm you're not a bot",
    )
    assert "Sign in to confirm you're not a bot" in "".join(traceback.format_exception(error))
    assert_redacted(error)


@pytest.mark.asyncio
async def test_section_failure_reason_is_redacted(pipeline, monkeypatch):  # noqa: F811
    monkeypatch.setattr(module, "materialize_clip_source", failing(RuntimeError(PROXY_FAILURE)))

    with pytest.raises(YouTubeDownloadFailedError) as raised:
        await pipeline.run()

    assert raised.value.reasons == ("Tunnel failed via [url]",)
    assert_redacted(raised.value)


@pytest.mark.asyncio
async def test_proxy_route_failure_keeps_audio_and_provider_reasons(pipeline, monkeypatch):  # noqa: F811
    monkeypatch.setattr(module, "resolve_full_youtube_download", lambda *a, **kw: None)

    def analysis_source(*_args, errors=None, download_full, **_kwargs):
        errors.append(PROXY_FAILURE)
        download_full()
        errors.append("Requested format is not available")
        return None, False

    monkeypatch.setattr(module, "download_analysis_source", analysis_source)

    with pytest.raises(YouTubeDownloadFailedError) as raised:
        await pipeline.run()

    assert str(raised.value) == "Failed to download video"
    assert raised.value.reasons == ("Tunnel failed via [url]", "Requested format is not available")
    assert_redacted(raised.value)


@pytest.mark.asyncio
async def test_direct_route_failure_keeps_provider_reasons(pipeline, monkeypatch):  # noqa: F811
    monkeypatch.setattr(module, "proxy_configured", lambda *_: False)

    async def full_download(*_args, errors=None, **_kwargs):
        errors.append("Video unavailable. This video is private")
        return None

    module.VideoService.download_video.side_effect = full_download

    with pytest.raises(YouTubeDownloadFailedError) as raised:
        await pipeline.run()

    assert str(raised.value) == "Failed to download video"
    assert raised.value.reasons == ("Video unavailable. This video is private",)


@pytest.mark.asyncio
async def test_geo_restriction_still_wins_over_generic_failure(pipeline, monkeypatch):  # noqa: F811
    monkeypatch.setattr(module, "materialize_clip_source", failing(YouTubeGeoRestrictedError()))

    with pytest.raises(YouTubeGeoRestrictedError):
        await pipeline.run()


@pytest.mark.asyncio
async def test_byte_budget_still_reports_the_limit(pipeline, monkeypatch):  # noqa: F811
    monkeypatch.setattr(module, "materialize_clip_source", failing(DownloadBudgetExceeded("limit hit")))

    with pytest.raises(Exception, match="limit hit") as raised:
        await pipeline.run()

    assert not isinstance(raised.value, YouTubeDownloadFailedError)


def test_analysis_source_collects_redacted_proxy_reason(monkeypatch):
    monkeypatch.setattr(youtube_source, "download_audio_for_job", Mock(side_effect=RuntimeError(PROXY_FAILURE)))
    errors = []

    assert youtube_source.download_analysis_source(
        URL, VIDEO_ID, download_full=lambda: None, errors=errors,
    ) == (None, False)
    assert errors == ["Tunnel failed via [url]"]


def test_reasons_are_deduplicated_and_bounded():
    error = YouTubeDownloadFailedError("Failed to download video", ["a", "a", "", *map(str, range(20))])

    assert error.reasons[:2] == ("a", "0")
    assert len(error.reasons) == YouTubeDownloadFailedError.MAX_REASONS
    assert YouTubeDownloadFailedError("Failed to download video").reasons_text() == "no reason reported"


@pytest.mark.asyncio
async def test_task_failure_logs_reasons_but_shows_short_message(caplog):
    service = build_task_service()
    service.video_service.process_video_complete.side_effect = YouTubeDownloadFailedError(
        "Failed to download video", ["Sign in to confirm you're not a bot"],
    )
    progress = AsyncMock()

    with caplog.at_level(logging.ERROR), pytest.raises(YouTubeDownloadFailedError):
        await service.process_task(
            task_id="task-1", url=URL, source_type="youtube", user_id="user-1",
            progress_callback=progress,
        )

    assert service.task_repo.update_task_status.await_args.kwargs["progress_message"] == "Failed to download video"
    assert service.task_repo.update_task_runtime_metadata.await_args.kwargs["error_code"] == "download_error"
    assert progress.await_args.args == (0, "Failed to download video", "error")
    assert any(
        "Failed to download video (Sign in to confirm you're not a bot)" in record.getMessage()
        for record in caplog.records
    )
