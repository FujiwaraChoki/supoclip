"""Pieces a worker on another machine relies on, without PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src import font_registry, local_volume
from src.config import Config
from src.local_volume import volume_id
from src.media_storage import MediaStorage
from src.workers.tasks import WorkerSettings, check_media_volume
from tests.unit.test_media_storage import MemoryS3


def test_processes_sharing_a_disk_agree_on_its_id_and_other_disks_differ(tmp_path, monkeypatch):
    shared, other = tmp_path / "uploads", tmp_path / "render-nvme"
    first = volume_id(shared)
    # Another process (no in-memory cache) reads the same file.
    monkeypatch.setattr(local_volume, "_cache", {})
    assert volume_id(shared) == first
    assert (shared / ".volume-id").read_text() == first
    assert volume_id(other) != first
    assert len(first) == 32


def test_processes_starting_together_on_an_empty_disk_settle_on_one_id(tmp_path, monkeypatch):
    class NoCache(dict):
        def get(self, *_args):
            return None

    monkeypatch.setattr(local_volume, "_cache", NoCache())
    with ThreadPoolExecutor(max_workers=16) as pool:
        ids = set(pool.map(lambda _n: volume_id(tmp_path / "uploads"), range(64)))
    assert len(ids) == 1
    assert [p.name for p in (tmp_path / "uploads").iterdir()] == [".volume-id"]


class FakeRedis:
    def __init__(self, value=None):
        self.value = value

    async def get(self, _key):
        return self.value


def config(tmp_path, storage="local"):
    settings = Config()
    settings.media_storage = storage
    settings.media_s3_bucket = "private-media" if storage == "s3" else None
    settings.temp_dir = str(tmp_path)
    return settings


async def test_worker_with_local_storage_refuses_a_disk_the_api_cannot_read(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.get_config", lambda: config(tmp_path))
    mine = volume_id(tmp_path)
    # Same volume as the API (one machine, or not announced yet): fine.
    await check_media_volume(FakeRedis(mine.encode()))
    await check_media_volume(FakeRedis(mine))
    await check_media_volume(FakeRedis(None))
    with pytest.raises(RuntimeError, match="MEDIA_STORAGE=s3"):
        await check_media_volume(FakeRedis(b"f" * 32))


async def test_worker_with_object_storage_runs_on_any_disk(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.get_config", lambda: config(tmp_path, "s3"))
    await check_media_volume(FakeRedis(b"f" * 32))


def test_video_worker_checks_its_volume_and_cleans_its_own_scratch():
    assert WorkerSettings.on_startup.__name__ == "on_worker_startup"
    assert WorkerSettings.on_shutdown.__name__ == "on_worker_shutdown"


@pytest.fixture
def fonts(tmp_path, monkeypatch):
    """The API machine's font directory, a worker machine's, and one bucket."""
    client = MemoryS3()
    storage = MediaStorage(config(tmp_path, "s3"), client)
    monkeypatch.setattr(font_registry, "_font_storage", lambda: storage)
    api, worker = tmp_path / "api-fonts", tmp_path / "worker-fonts"

    def on(directory):
        monkeypatch.setattr(font_registry, "USER_FONTS_DIR", directory)

    on(api)
    return client, api, worker, on


def upload_font(directory: Path, user: str, name: str, body: bytes) -> Path:
    path = directory / user / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def test_uploaded_font_is_fetched_by_a_worker_on_another_machine(fonts):
    client, api, worker, on = fonts
    font_registry.publish_user_font(upload_font(api, "user-1", "user-1-Brand.ttf", b"brand font"), "user-1")
    assert client.objects["media/fonts/user-1/user-1-Brand.ttf"] == b"brand font"

    on(worker)
    assert font_registry.find_font_path("user-1-Brand", allow_all_user_fonts=True) is None
    fetched = font_registry.fetch_user_font("user-1-Brand", "user-1")
    assert fetched.read_bytes() == b"brand font"
    # The renderer looks fonts up by name across user directories.
    assert font_registry.find_font_path("user-1-Brand", allow_all_user_fonts=True) == fetched
    # Bundled fonts and other users' fonts are not looked for under this user.
    assert font_registry.fetch_user_font("TikTokSans-Regular", "user-1") is None
    assert font_registry.fetch_user_font("user-1-Brand", "someone-else") is None
    assert font_registry.fetch_user_font("../user-1/user-1-Brand", "user-1") is None


def test_worker_copy_follows_a_deleted_or_replaced_font(fonts):
    client, api, worker, on = fonts
    original = upload_font(api, "user-1", "user-1-Brand.ttf", b"first design")
    font_registry.publish_user_font(original, "user-1")
    on(worker)
    cached = font_registry.fetch_user_font("user-1-Brand", "user-1")

    # The user deletes it and uploads another file under the same name.
    on(api)
    original.unlink()
    font_registry.unpublish_user_font("user-1-Brand.ttf", "user-1")
    font_registry.publish_user_font(upload_font(api, "user-1", "user-1-Brand.ttf", b"second, longer design"), "user-1")
    on(worker)
    assert font_registry.fetch_user_font("user-1-Brand", "user-1").read_bytes() == b"second, longer design"

    on(api)
    (api / "user-1" / "user-1-Brand.ttf").unlink()
    font_registry.unpublish_user_font("user-1-Brand.ttf", "user-1")
    on(worker)
    assert font_registry.fetch_user_font("user-1-Brand", "user-1") is None
    assert not cached.exists()


def test_fonts_from_before_object_storage_are_stored_but_a_workers_copies_are_not(fonts):
    client, api, worker, on = fonts
    upload_font(api, "user-1", "user-1-Old.otf", b"uploaded last year")
    upload_font(api, "user-1", "notes.txt", b"not a font")
    assert font_registry.publish_local_user_fonts() == 1
    assert font_registry.publish_local_user_fonts() == 0
    assert set(client.objects) == {"media/fonts/user-1/user-1-Old.otf"}

    on(worker)
    font_registry.fetch_user_font("user-1-Old", "user-1")
    # The user deletes the font. A pass on the worker must not put it back.
    client.objects.clear()
    assert font_registry.publish_local_user_fonts() == 0
    assert client.objects == {}


def test_local_storage_keeps_fonts_on_disk_only(tmp_path, monkeypatch):
    monkeypatch.setattr(font_registry, "_font_storage", lambda: None)
    monkeypatch.setattr(font_registry, "USER_FONTS_DIR", tmp_path)
    path = upload_font(tmp_path, "user-1", "user-1-Brand.ttf", b"font")
    font_registry.publish_user_font(path, "user-1")
    font_registry.unpublish_user_font(path.name, "user-1")
    assert font_registry.publish_local_user_fonts() == 0
    assert font_registry.fetch_user_font("user-1-Brand", "user-1") == path
    assert font_registry.fetch_user_font("user-1-Missing", "user-1") is None
