"""Storage contracts: durable outputs, private uploads and temporary inputs."""

import asyncio
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlsplit

from botocore.exceptions import ClientError
import pytest

from src.config import Config
from src.media_storage import MediaStorage, PART_BYTES, media_work, owner_key


class MemoryS3:
    def __init__(self):
        self.objects = {}
        self.deleted = []

    def upload_file(self, filename, bucket, key, ExtraArgs):
        self.objects[key] = Path(filename).read_bytes()

    def download_file(self, bucket, key, filename):
        if key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "GetObject")
        Path(filename).write_bytes(self.objects[key])

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)
        self.objects.pop(Key, None)

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {"ContentLength": len(self.objects[Key])}

    def get_paginator(self, name):
        def paginate(Bucket, Prefix):
            yield {"Contents": [{"Key": key, "LastModified": datetime.now(timezone.utc)}
                for key in list(self.objects) if key.startswith(Prefix)]}
        return SimpleNamespace(paginate=paginate)


@pytest.fixture
def storage(tmp_path):
    config = Config()
    config.media_storage = "s3"
    config.media_s3_bucket = "private-media"
    config.temp_dir = str(tmp_path)
    return MediaStorage(config, MemoryS3())


def test_persisted_clip_and_timing_metadata_survive_local_deletion(storage, tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"rendered video")
    path.with_suffix(".source_map.json").write_text('{"source_ranges": [{"start": 1, "end": 3}]}')
    reference = storage.persist_clip(str(path), "task")
    storage.delete(str(path))
    restored = storage.local_path(reference)
    assert restored.read_bytes() == b"rendered video"
    assert '"start": 1' in restored.with_suffix(".source_map.json").read_text()
    assert reference.startswith("s3://private-media/media/clips/task/")
    # Re-rendering the same filename must never overwrite a live object.
    assert storage.persist_clip(str(restored), "task") != reference


@pytest.mark.parametrize("reference", [
    "s3://other-bucket/media/clip.mp4", "s3://private-media/other/clip.mp4",
    "s3://private-media/media/../secret", "s3://private-media/media/clip.mp4?x=y",
])
def test_references_cannot_escape_storage_scope(storage, reference):
    with pytest.raises(ValueError):
        storage.parse(reference)


def test_source_ownership_is_checked_before_contacting_s3(storage):
    url = f"upload://s3/{owner_key('owner')}/upload/video.mp4"
    with pytest.raises(ValueError, match="belong"):
        storage.validate_source(url, "someone-else")
    key = storage.parse(storage.source_reference(url))
    storage.client.objects[key] = b"video"
    assert storage.validate_source(url, "owner") == storage.source_reference(url)


def test_storage_references_preserve_special_characters_in_legacy_filenames(storage):
    key = storage.key("clips", "task", "upload", "clip #1?.mp4")
    reference = storage.reference(key)
    assert "%23" in reference and "%3F" in reference
    assert storage.parse(reference) == key


def test_cache_sweep_removes_hidden_partial_downloads_after_a_crash(storage, tmp_path):
    directory = tmp_path / "media-cache" / ("a" * 64)
    directory.mkdir(parents=True)
    access = directory / ".access"
    access.touch()
    old = time.time() - storage.config.media_cache_ttl_seconds - 1
    os.utime(access, (old, old))
    partial = directory / ".video.mp4.interrupted.part"
    partial.write_bytes(b"incomplete download")
    storage.sweep_cache()
    assert not partial.exists()
    assert (directory / ".lease").exists()


def test_multipart_supports_files_larger_than_s3_single_put_limit(storage):
    client = Mock()
    client.create_multipart_upload.return_value = {"UploadId": "multipart-id"}
    client.generate_presigned_url.return_value = "https://private-media/part"
    storage._client = client
    size = 12_000_000_000
    authorization = storage.begin_upload("owner", "original.mp4", size, "video/mp4")
    assert len(authorization["parts"]) == (size + PART_BYTES - 1) // PART_BYTES
    assert client.generate_presigned_url.call_args.kwargs["Params"]["PartNumber"] == len(authorization["parts"])
    assert client.create_multipart_upload.call_args.kwargs["Metadata"]["expected-size"] == str(size)


def test_presigned_requests_use_the_bucket_region_without_a_redirect(storage):
    config = storage.config
    config.media_s3_region = "eu-central-1"
    config.media_s3_access_key = "test-access-key"
    config.media_s3_secret_key = "test-secret-key"
    config.media_s3_session_token = None
    actual = MediaStorage(config)
    url = actual.client.generate_presigned_url("upload_part", Params={
        "Bucket": actual.bucket, "Key": actual.key("sources", "upload", "video.mp4"),
        "UploadId": "test-upload", "PartNumber": 1,
    }, ExpiresIn=900)
    assert urlsplit(url).hostname.endswith("s3.eu-central-1.amazonaws.com")


def test_multipart_rejects_truncated_completed_object(storage):
    storage._client = Mock()
    storage.client.head_object.return_value = {"ContentLength": 4, "Metadata": {"expected-size": "10"}}
    url = f"upload://s3/{owner_key('owner')}/upload/video.mp4"
    with pytest.raises(ValueError, match="invalid size"):
        storage.complete_upload("owner", {"video_path": url, "uploadId": "multipart",
            "parts": [{"partNumber": 1, "etag": "etag"}]})
    storage.client.delete_object.assert_called_once()


async def test_render_workspace_releases_download_on_failure(storage):
    key = storage.key("clips", "task", "clip.mp4")
    storage.client.objects[key] = b"clip"
    paths = []

    @media_work
    async def render():
        path = await asyncio.to_thread(storage.local_path, storage.reference(key))
        paths.append(path)
        assert path.exists()
        raise RuntimeError("ffmpeg failed")

    with pytest.raises(RuntimeError):
        await render()
    assert not paths[0].exists()
    assert storage.client.objects[key] == b"clip"


async def test_simultaneous_renders_keep_source_until_last_user_finishes(storage):
    key = storage.key("sources", "owner", "upload", "video.mp4")
    storage.client.objects[key] = b"video"
    reference = storage.reference(key)
    both_ready = asyncio.Event()
    finish_second = asyncio.Event()
    paths = []

    @media_work
    async def first():
        paths.append(await asyncio.to_thread(storage.local_path, reference))
        await both_ready.wait()

    @media_work
    async def second():
        paths.append(await asyncio.to_thread(storage.local_path, reference))
        both_ready.set()
        await finish_second.wait()
        assert paths[1].exists()

    one, two = asyncio.create_task(first()), asyncio.create_task(second())
    await one
    assert paths[0].exists()
    finish_second.set()
    await two
    assert not paths[0].exists()


def test_delete_is_idempotent_and_removes_sidecars(storage, tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"video")
    path.with_suffix(".source_map.json").write_text("{}")
    storage.delete(str(path))
    storage.delete(str(path))
    assert not path.exists()
    assert not path.with_suffix(".source_map.json").exists()
    with pytest.raises(ValueError, match="outside"):
        storage.delete("/etc/passwd")


def test_editor_restore_does_not_reupload_unchanged_media(storage, tmp_path):
    directory = tmp_path / "editor" / ("a" * 64)
    directory.mkdir(parents=True)
    (directory / "clean.mp4").write_bytes(b"editor preview")
    (directory / "draft.json").write_text('{"revision": 1}')
    storage.client.upload_file = Mock(wraps=storage.client.upload_file)
    storage.save_editor(directory)
    assert storage.client.upload_file.call_count == 2
    (directory / "clean.mp4").unlink()
    (directory / "draft.json").unlink()
    storage.restore_editor(directory, media=True)
    storage.save_editor(directory)
    assert storage.client.upload_file.call_count == 2
    assert (directory / "clean.mp4").read_bytes() == b"editor preview"


def test_youtube_word_timings_can_be_restored_without_the_original_download(storage, tmp_path):
    path = tmp_path / "abcdefghijk.mp4"
    cache = path.with_suffix(".transcript_cache.json")
    cache.write_text('{"words": [{"text": "hello", "start": 0, "end": 500}]}')
    storage.save_youtube_metadata("abcdefghijk", path)
    cache.unlink()
    storage.restore_youtube_metadata("abcdefghijk", path)
    assert '"text": "hello"' in cache.read_text()


@pytest.mark.parametrize("relative, expected", [
    ("task/interrupted-upload/clip.source_map.json", True),
    ("clip.source_map.json", False),
    ("task/clip.source_map.json", False),
])
def test_orphan_scan_only_queues_individual_render_directories(storage, relative, expected):
    from src.workers.media_cleanup import orphan_candidates

    key = storage.key("clips", relative)
    def paginate(Bucket, Prefix):
        yield {"Contents": [{"Key": key, "LastModified": datetime.now(timezone.utc) - timedelta(days=2)}]
            if key.startswith(Prefix) else []}
    storage.client.get_paginator = Mock(return_value=SimpleNamespace(paginate=paginate))
    candidates = list(orphan_candidates(storage))
    assert candidates == ([(storage.reference(str(Path(key).parent)), True)] if expected else [])
