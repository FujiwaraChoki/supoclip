from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.api.routes import tasks
from src.services.video_service import VideoService
from src.youtube_utils import canonical_youtube_url, get_youtube_video_id

# The Shorts link from issue #287.
VIDEO_ID = "ai1gIWacf0I"
CANONICAL = f"https://www.youtube.com/watch?v={VIDEO_ID}"


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/shorts/{VIDEO_ID}",
        f"https://youtube.com/shorts/{VIDEO_ID}?si=Q2s9xYz",
        f"https://m.youtube.com/shorts/{VIDEO_ID}/",
        f"https://www.youtube.com/watch?v={VIDEO_ID}",
        f"https://www.youtube.com/watch?feature=share&v={VIDEO_ID}&t=30",
        f"https://youtube.com/watch?v={VIDEO_ID}&list=PL123",
        f"https://m.youtube.com/watch?v={VIDEO_ID}",
        f"https://music.youtube.com/watch?v={VIDEO_ID}&si=abc",
        f"https://youtu.be/{VIDEO_ID}",
        f"https://youtu.be/{VIDEO_ID}?si=abc&t=12",
        f"https://www.youtube.com/live/{VIDEO_ID}?feature=share",
        f"https://www.youtube.com/embed/{VIDEO_ID}?start=10",
        f"https://www.youtube-nocookie.com/embed/{VIDEO_ID}",
        f"https://www.youtube.com/v/{VIDEO_ID}",
        f"http://WWW.YOUTUBE.COM/shorts/{VIDEO_ID}",
        f"  www.youtube.com/shorts/{VIDEO_ID}  ",
    ],
)
def test_supported_youtube_links_resolve_to_one_watch_url(url):
    assert get_youtube_video_id(url) == VIDEO_ID
    assert canonical_youtube_url(url) == CANONICAL
    assert VideoService.determine_source_type(url.strip()) == "youtube"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "upload://s3/owner/file.mp4",
        f"https://example.com/watch?v={VIDEO_ID}",
        # A YouTube link inside another site's query string is not a YouTube source.
        f"https://example.com/?u=https://www.youtube.com/watch?v={VIDEO_ID}",
        f"https://notyoutube.com/shorts/{VIDEO_ID}",
        f"ftp://www.youtube.com/watch?v={VIDEO_ID}",
        "https://www.youtube.com/shorts/tooShort",
        f"https://www.youtube.com/watch?v={VIDEO_ID}x",
        "https://www.youtube.com/@creator",
        "http://[::1",
    ],
)
def test_other_urls_are_not_youtube_videos(url):
    assert get_youtube_video_id(url) is None
    assert canonical_youtube_url(url) is None


@pytest.mark.asyncio
async def test_shorts_submission_is_stored_and_queued_as_a_watch_url(monkeypatch):
    billing = {"max_youtube_duration_seconds": 5400, "max_upload_duration_seconds": 5400}
    monkeypatch.setattr(tasks.BillingService, "assert_can_create_task", AsyncMock(return_value=billing))
    monkeypatch.setattr(tasks, "_get_user_id_from_headers", AsyncMock(return_value="user-1"))
    save_metadata = AsyncMock()
    monkeypatch.setattr(tasks, "_save_task_source_metadata", save_metadata)
    preflight = AsyncMock(return_value={"duration": 42, "title": "Short"})
    monkeypatch.setattr(tasks, "async_get_youtube_video_info", preflight)
    service = SimpleNamespace(
        video_service=VideoService(),
        create_task_with_source=AsyncMock(return_value="task-1"),
    )
    monkeypatch.setattr(tasks, "TaskService", lambda _db: service)
    queue = SimpleNamespace(enqueue_processing_job=AsyncMock(return_value="job-1"))
    request = SimpleNamespace(
        json=AsyncMock(return_value={"source": {"url": f"https://www.youtube.com/shorts/{VIDEO_ID}?si=abc"}}),
        app=SimpleNamespace(state=SimpleNamespace(queue_adapter=queue)),
    )

    result = await tasks.create_task(request, db=None)

    assert result["task_id"] == "task-1"
    assert preflight.await_args.args[0] == CANONICAL
    assert service.create_task_with_source.await_args.kwargs["url"] == CANONICAL
    assert save_metadata.await_args.args[1]["url"] == CANONICAL
    assert CANONICAL in queue.enqueue_processing_job.await_args.args
