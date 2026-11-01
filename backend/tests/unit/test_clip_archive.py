"""Download-all ZIP: access, names/order, missing files and the per-user slot."""

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock
import zipfile

from botocore.exceptions import ClientError
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from src import clip_archive
from src.api.routes import tasks
from src.config import Config
from src.database import get_db
from src.media_storage import MediaStorage


class FakeRedis:
    """Enough of redis.asyncio for SET NX PX, EXISTS and the two scripts."""

    def __init__(self, store):
        self.store = store

    async def set(self, key, value, nx=False, px=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def exists(self, key):
        return int(key in self.store)

    async def eval(self, script, numkeys, key, token, *args):
        if self.store.get(key) != token:
            return 0
        if script == clip_archive._RELEASE:
            del self.store[key]
        return 1

    async def aclose(self):
        pass


class MemoryS3:
    def __init__(self, objects):
        self.objects = objects

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {"ContentLength": len(self.objects[Key])}

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key])}


@pytest.fixture
def redis_store(monkeypatch):
    store = {}
    monkeypatch.setattr(clip_archive, "_redis_client", lambda: FakeRedis(store))
    return store


@pytest.fixture
async def client(tmp_path, monkeypatch, redis_store):
    first = tmp_path / "a.mp4"
    first.write_bytes(b"first clip " * 1000)
    third = tmp_path / "c.mp4"
    third.write_bytes(b"third clip")
    clips = [
        {"id": "clip-1", "hook_title": "Why Sleep Matters!", "file_path": str(first)},
        {"id": "clip-2", "hook_title": "Gone", "file_path": str(tmp_path / "missing.mp4")},
        {"id": "abcdef1234", "hook_title": None, "file_path": str(third)},
    ]
    task = {"id": "task", "user_id": "owner", "status": "completed", "source_title": "Morning Routine"}
    service = SimpleNamespace(
        task_repo=SimpleNamespace(get_task_by_id=AsyncMock(return_value=task)),
        clip_repo=SimpleNamespace(get_clips_by_task=AsyncMock(return_value=clips)),
    )
    monkeypatch.setattr(tasks, "TaskService", lambda db: service)
    monkeypatch.setattr(tasks, "_get_user_id_from_headers", AsyncMock(return_value="owner"))
    monkeypatch.setattr(tasks, "user_is_admin", AsyncMock(return_value=False))

    db = SimpleNamespace(close=AsyncMock())
    app = FastAPI()
    app.include_router(tasks.router)
    app.dependency_overrides[get_db] = lambda: db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http, service, clips, db


async def test_streams_existing_clips_in_order_with_readable_names(client, redis_store):
    http, _service, _clips, db = client

    response = await http.get("/tasks/task/clips/zip")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == (
        "attachment; filename=\"morning-routine-clips.zip\"; filename*=UTF-8''morning-routine-clips.zip"
    )
    db.close.assert_awaited()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.testzip() is None
        assert archive.namelist() == ["01-why-sleep-matters.mp4", "02-clip-abcdef12.mp4"]
        assert archive.read("01-why-sleep-matters.mp4") == b"first clip " * 1000
        assert archive.read("02-clip-abcdef12.mp4") == b"third clip"
    # The slot is freed once the stream ends.
    assert redis_store == {}


async def test_preflight_reports_what_the_download_will_contain(client):
    http, *_ = client

    response = await http.get("/tasks/task/clips/zip/preflight")

    assert response.status_code == 200
    assert response.json() == {
        "filename": "morning-routine-clips.zip",
        "clip_count": 2,
        "total_bytes": 11000 + 10,
    }


async def test_other_users_cannot_download(client, monkeypatch):
    http, *_ = client
    monkeypatch.setattr(tasks, "_get_user_id_from_headers", AsyncMock(return_value="outsider"))

    assert (await http.get("/tasks/task/clips/zip")).status_code == 403
    assert (await http.get("/tasks/task/clips/zip/preflight")).status_code == 403


async def test_no_existing_clip_files_is_not_found(client):
    http, service, clips, _db = client
    service.clip_repo.get_clips_by_task.return_value = [clips[1]]

    for path in ("/tasks/task/clips/zip", "/tasks/task/clips/zip/preflight"):
        response = await http.get(path)
        assert response.status_code == 404
        assert response.json()["detail"] == "This generation has no clip files to download."


async def test_second_concurrent_download_is_rejected(client, redis_store):
    http, *_ = client
    redis_store["clip_archive:owner"] = "another-download"

    response = await http.get("/tasks/task/clips/zip")
    assert response.status_code == 429
    assert (await http.get("/tasks/task/clips/zip/preflight")).status_code == 429
    # Someone else's slot is left alone.
    assert redis_store == {"clip_archive:owner": "another-download"}


def test_entry_names_are_ordered_unique_and_keep_non_latin_titles():
    names = clip_archive.archive_entry_names([
        {"id": "1", "hook_title": "Same title"},
        {"id": "2", "hook_title": "Same title"},
        {"id": "3", "hook_title": "睡眠が大事な理由"},
        {"id": "4", "hook_title": "  ../../etc/passwd  "},
        {"id": "5", "hook_title": "!!!"},
    ])

    assert names == [
        "01-same-title.mp4",
        "02-same-title.mp4",
        "03-睡眠が大事な理由.mp4",
        "04-etc-passwd.mp4",
        "05-clip-5.mp4",
    ]
    assert len(set(names)) == len(names)


def test_content_disposition_falls_back_to_ascii():
    header = clip_archive.content_disposition(clip_archive.archive_filename("Café über 睡眠"))

    assert header == (
        "attachment; filename=\"cafe-uber-clips.zip\"; "
        "filename*=UTF-8''caf%C3%A9-%C3%BCber-%E7%9D%A1%E7%9C%A0-clips.zip"
    )


async def test_object_storage_clips_are_streamed_in_chunks(tmp_path):
    config = Config()
    config.media_storage = "s3"
    config.media_s3_bucket = "private-media"
    config.temp_dir = str(tmp_path)
    payload = bytes(range(256)) * 2000  # 512 KB
    storage = MediaStorage(config, MemoryS3({"media/clips/task/a.mp4": payload}))
    clips = [
        {"id": "1", "hook_title": "Stored", "file_path": "s3://private-media/media/clips/task/a.mp4"},
        {"id": "2", "hook_title": "Missing", "file_path": "s3://private-media/media/clips/task/b.mp4"},
    ]

    entries = clip_archive.resolve_entries(clips, storage)
    assert [(entry.name, entry.size) for entry in entries] == [("01-stored.mp4", len(payload))]

    chunks = [
        chunk async for chunk in clip_archive.stream_archive(
            entries, lambda entry: clip_archive.open_entry(entry, storage), chunk_bytes=64 * 1024,
        )
    ]

    # Bytes leave as each chunk is read, not once the whole clip is in memory.
    assert len(chunks) >= 6
    with zipfile.ZipFile(io.BytesIO(b"".join(chunks))) as archive:
        info = archive.getinfo("01-stored.mp4")
        assert info.compress_type == zipfile.ZIP_DEFLATED
        assert info.flag_bits & 0x08  # sizes follow the data
        assert archive.read("01-stored.mp4") == payload
    # No local copy is staged in the media cache.
    assert not (tmp_path / "media-cache").exists()


async def test_slot_is_released_when_the_stream_fails(tmp_path, redis_store):
    good = tmp_path / "a.mp4"
    good.write_bytes(b"ok")
    entries = [
        clip_archive.ArchiveEntry("01-a.mp4", str(good), 2),
        clip_archive.ArchiveEntry("02-b.mp4", str(tmp_path / "deleted-meanwhile.mp4"), 2),
    ]
    slot = await clip_archive.ArchiveSlot("owner").acquire()
    assert "clip_archive:owner" in redis_store

    with pytest.raises(FileNotFoundError):
        async for _chunk in clip_archive.stream_with_slot(entries, None, slot):
            pass

    assert redis_store == {}
