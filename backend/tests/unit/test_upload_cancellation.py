import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.api.routes import media


async def test_cancelled_authorization_aborts_created_upload_before_releasing_slot(monkeypatch):
    started, finish = threading.Event(), threading.Event()
    live = []
    aborted = []
    def begin(*args, **kwargs):
        started.set()
        assert finish.wait(5)
        authorization = {"uploadId": "created-upload", "video_path": "upload://s3/owner/upload/video.mp4"}
        live.append(authorization)
        return authorization
    def abort(owner, authorization):
        aborted.append(authorization)
        live.remove(authorization)
    storage = SimpleNamespace(enabled=True, config=object(), begin_upload=begin, abort_upload=abort)
    monkeypatch.setattr(media, "MediaStorage", lambda: storage)
    monkeypatch.setattr(media, "_get_authenticated_user_id", AsyncMock(return_value="owner"))
    monkeypatch.setattr(media, "reserve_upload", AsyncMock(return_value="reservation"))
    monkeypatch.setattr(media, "bind_upload", AsyncMock())
    completed = AsyncMock()
    monkeypatch.setattr(media, "finish_upload", completed)
    db = SimpleNamespace(rollback=AsyncMock())
    request = SimpleNamespace(json=AsyncMock(return_value={"size": 1, "filename": "video.mp4"}))
    task = asyncio.create_task(media.authorize_upload(request, db))
    assert await asyncio.to_thread(started.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    completed.assert_not_called()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not live
    assert aborted[0]["uploadId"] == "created-upload"
    completed.assert_awaited_once_with(db, "reservation", "aborted")
