import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.download_progress import (
    DownloadRecovery,
    report_download_recovery,
    run_download_in_thread,
)


@pytest.mark.asyncio
async def test_reports_recovery_while_download_is_blocked_on_the_event_loop():
    release = threading.Event()
    event_loop_thread = threading.get_ident()
    updates = []

    def download():
        assert threading.get_ident() != event_loop_thread
        report_download_recovery(DownloadRecovery.BACKUP)
        assert release.wait(3), "Recovery notice must arrive before download completes"
        return "video.mp4"

    async def progress(percent, message, status):
        assert threading.get_ident() == event_loop_thread
        updates.append((percent, message, status))
        release.set()

    try:
        result = await run_download_in_thread(download, progress_callback=progress, progress=65)
    finally:
        release.set()
    assert result == "video.mp4"
    assert updates == [(65, DownloadRecovery.BACKUP.value, "processing")]


@pytest.mark.asyncio
async def test_concurrent_jobs_keep_recovery_updates_separate():
    barrier = threading.Barrier(2)
    callbacks = [AsyncMock(), AsyncMock()]

    def download(recovery):
        barrier.wait(timeout=3)
        report_download_recovery(recovery)

    await asyncio.gather(
        run_download_in_thread(download, DownloadRecovery.AUDIO, progress_callback=callbacks[0]),
        run_download_in_thread(download, DownloadRecovery.BACKUP, progress_callback=callbacks[1]),
    )
    callbacks[0].assert_awaited_once_with(10, DownloadRecovery.AUDIO.value, "processing")
    callbacks[1].assert_awaited_once_with(10, DownloadRecovery.BACKUP.value, "processing")


@pytest.mark.asyncio
async def test_flushes_notices_before_raising_download_failure():
    callback = AsyncMock()

    def download():
        report_download_recovery(DownloadRecovery.BACKUP)
        report_download_recovery(DownloadRecovery.BACKUP)
        report_download_recovery(DownloadRecovery.ANOTHER_BACKUP)
        raise OSError("download failed")

    with pytest.raises(OSError, match="download failed"):
        await run_download_in_thread(download, progress_callback=callback)
    assert [call.args[1] for call in callback.await_args_list] == [
        DownloadRecovery.BACKUP.value, DownloadRecovery.ANOTHER_BACKUP.value,
    ]


@pytest.mark.asyncio
async def test_cancelling_download_drops_late_notices():
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    callback = AsyncMock()

    def download():
        started.set()
        try:
            release.wait(3)
            report_download_recovery(DownloadRecovery.BACKUP)
        finally:
            finished.set()

    operation = asyncio.create_task(run_download_in_thread(download, progress_callback=callback))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 3)
    # Run a second operation, so any leaked listener has a chance to fire.
    await run_download_in_thread(lambda: report_download_recovery(DownloadRecovery.AUDIO))
    callback.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_failover_emits_only_public_notices(monkeypatch, tmp_path):
    from src import youtube_utils

    config = SimpleNamespace(
        youtube_proxy_url="http://proxy.example:823",
        youtube_download_provider="yt_dlp",
        rapidapi_youtube_api_key="test-key",
        rapidapi_youtube_timeout_seconds=30,
        apify_api_token="test-token",
        youtube_download_max_bytes=1000,
    )
    monkeypatch.setattr(youtube_utils, "get_config", lambda: config)
    monkeypatch.setattr(youtube_utils, "YouTubeDownloader", lambda: SimpleNamespace(temp_dir=tmp_path))
    monkeypatch.setattr(youtube_utils, "_resolve_apify_quality", lambda *args: "720")
    monkeypatch.setattr(youtube_utils, "_get_local_video_dimensions", lambda *args: (1280, 720))

    def fail(*args, **kwargs):
        raise OSError("provider-private-error")

    def recover(*args, **kwargs):
        path = tmp_path / "video.mp4"
        path.write_bytes(b"video")
        return path

    monkeypatch.setattr(youtube_utils, "download_video_via_rapidapi", fail)
    monkeypatch.setattr(youtube_utils, "download_youtube_video_with_apify", recover)
    callback = AsyncMock()
    result = await run_download_in_thread(
        youtube_utils.download_youtube_video, "https://www.youtube.com/watch?v=abcdefghijk",
        skip_proxy=True, progress_callback=callback,
    )
    assert result.read_bytes() == b"video"
    assert [call.args[1] for call in callback.await_args_list] == [
        DownloadRecovery.BACKUP.value, DownloadRecovery.ANOTHER_BACKUP.value,
    ]
