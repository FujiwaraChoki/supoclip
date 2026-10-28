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


def test_capped_source_is_preferred_and_removed_with_the_original(storage, tmp_path):
    url = f"upload://s3/{owner_key('owner')}/upload/video.mp4"
    key = storage.parse(storage.source_reference(url))
    storage.client.objects[key] = b"original-4k"
    capped = storage.capped_source_key(key)
    assert capped.endswith("video.capped.mp4")
    storage.client.objects[capped] = b"capped-1080"
    restored = storage.local_path(storage.reference(key))
    assert restored.read_bytes() == b"capped-1080"
    assert restored.name == "video.mp4"

    directory = tmp_path / "prepared"
    directory.mkdir()
    prepared = directory / "video.mp4"
    prepared.write_bytes(b"fresh-cap")
    (directory / ".reference").write_text(storage.reference(key))
    storage.store_capped_source(prepared)
    assert storage.client.objects[capped] == b"fresh-cap"
    assert storage.client.objects[key] == b"original-4k"

    storage.delete(storage.reference(key))
    assert key not in storage.client.objects
    assert capped not in storage.client.objects


def test_completion_rejects_a_part_larger_than_the_signed_length(storage):
    storage._client = Mock()
    url = f"upload://s3/{owner_key('owner')}/upload/video.mp4"
    storage.client.list_parts.return_value = {
        "Parts": [{"PartNumber": 1, "Size": 50}],
        "IsTruncated": False,
    }
    with pytest.raises(ValueError, match="too large"):
        storage.complete_upload("owner", {
            "video_path": url,
            "uploadId": "multipart",
            "parts": [{"partNumber": 1, "etag": "etag"}],
        }, size=10)
    storage.client.complete_multipart_upload.assert_not_called()
    storage.client.abort_multipart_upload.assert_called_once()


def test_completion_aborts_an_oversized_part_before_the_upload_is_complete(storage):
    storage._client = Mock()
    url = f"upload://s3/{owner_key('owner')}/upload/video.mp4"
    storage.client.list_parts.return_value = {
        "Parts": [{"PartNumber": 1, "Size": PART_BYTES + 1}],
        "IsTruncated": False,
    }
    with pytest.raises(ValueError, match="too large"):
        storage.complete_upload("owner", {
            "video_path": url,
            "uploadId": "multipart",
            "parts": [
                {"partNumber": 1, "etag": "etag-1"},
                {"partNumber": 2, "etag": "etag-2"},
            ],
        }, size=PART_BYTES + 10)
    storage.client.complete_multipart_upload.assert_not_called()
    storage.client.abort_multipart_upload.assert_called_once()


def test_completion_accepts_parts_that_match_the_signed_length(storage):
    storage._client = Mock()
    url = f"upload://s3/{owner_key('owner')}/upload/video.mp4"
    storage.client.list_parts.return_value = {
        "Parts": [{"PartNumber": 1, "Size": 10}],
        "IsTruncated": False,
    }
    storage.client.head_object.return_value = {
        "ContentLength": 10,
        "Metadata": {"expected-size": "10"},
    }
    assert storage.complete_upload("owner", {
        "video_path": url,
        "uploadId": "multipart",
        "parts": [{"partNumber": 1, "etag": "etag"}],
    }, size=10) == {"video_path": url}
    storage.client.complete_multipart_upload.assert_called_once()


def test_begin_upload_rejects_files_over_the_configured_limit(storage):
    storage.config.max_video_upload_bytes = 4_000_000_000
    storage._client = Mock()
    with pytest.raises(ValueError, match="4 GB"):
        storage.begin_upload("owner", "video.mp4", 4_000_000_001, "video/mp4")
    storage.client.create_multipart_upload.assert_not_called()


def test_multipart_supports_files_larger_than_s3_single_put_limit(storage):
    client = Mock()
    client.create_multipart_upload.return_value = {"UploadId": "multipart-id"}
    client.generate_presigned_url.return_value = "https://private-media/part"
    storage._client = client
    size = 12_000_000_000
    storage.config.max_video_upload_bytes = size
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


@pytest.mark.parametrize("part_number", [True, 1.0])
def test_completion_rejects_noninteger_part_numbers_before_storage(storage, part_number):
    storage._client = Mock()
    url = f"upload://s3/{owner_key('owner')}/upload/video.mp4"
    with pytest.raises(ValueError, match="Invalid multipart"):
        storage.complete_upload("owner", {"video_path": url, "uploadId": "multipart",
            "parts": [{"partNumber": part_number, "etag": "etag"}]}, size=1)
    storage.client.complete_multipart_upload.assert_not_called()


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
    (directory / "asset.json").write_text('{"status": "ready"}')
    storage.client.upload_file = Mock(wraps=storage.client.upload_file)
    storage.save_editor(directory)
    assert storage.client.upload_file.call_count == 3
    (directory / "clean.mp4").unlink()
    (directory / "draft.json").unlink()
    storage.restore_editor(directory, media=True)
    storage.save_editor(directory)
    assert storage.client.upload_file.call_count == 3
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


def test_transient_head_failure_keeps_a_completed_upload_for_retry(storage):
    storage._client = Mock()
    storage.client.head_object.side_effect = ClientError({"Error": {"Code": "SlowDown"}}, "HeadObject")
    url = f"upload://s3/{owner_key('owner')}/upload/video.mp4"
    payload = {"video_path": url, "uploadId": "multipart", "parts": [{"partNumber": 1, "etag": "etag"}]}
    with pytest.raises(ClientError):
        storage.complete_upload("owner", payload)
    storage.client.delete_object.assert_not_called()
    storage.client.complete_multipart_upload.side_effect = ClientError({"Error": {"Code": "NoSuchUpload"}}, "CompleteMultipartUpload")
    storage.client.head_object.side_effect = None
    storage.client.head_object.return_value = {"ContentLength": 5, "Metadata": {"expected-size": "5"}}
    assert storage.complete_upload("owner", payload) == {"video_path": url}


def test_part_refresh_preserves_owner_boundary_and_accepts_new_credentials(storage):
    storage._client = Mock()
    storage.client.generate_presigned_url.return_value = "https://storage/new-signature"
    url = f"upload://s3/{owner_key('owner')}/upload/video.mp4"
    payload = {"video_path": url, "uploadId": "multipart", "partNumbers": [2]}
    with pytest.raises(ValueError, match="belong"):
        storage.refresh_upload_parts("someone-else", payload, size=PART_BYTES+1)
    storage.client.generate_presigned_url.assert_not_called()
    assert storage.refresh_upload_parts("owner", payload, size=PART_BYTES+1)["parts"] == [{"partNumber": 2, "url": "https://storage/new-signature"}]
    with pytest.raises(ValueError):
        storage.refresh_upload_parts("owner", {**payload, "partNumbers": [0]}, size=PART_BYTES+1)


@pytest.mark.parametrize("extension", [".3gp", ".mts", ".mpg", ".wmv"])
def test_browser_multipart_accepts_common_camera_video_formats(storage, extension):
    storage._client = Mock()
    storage.client.create_multipart_upload.return_value = {"UploadId": "multipart"}
    result = storage.begin_upload("owner", "camera" + extension, 10, "video/example")
    assert result["video_path"].endswith(extension)


def test_caption_reads_cache_absence_and_do_not_fetch_transcript_sidecars(storage):
    reference = storage.reference(storage.key("clips", "task", "upload", "clip.mp4"))
    storage.client.download_file = Mock(wraps=storage.client.download_file)
    assert storage.caption_settings(reference) is None
    assert storage.caption_settings(reference) is None
    assert storage.client.download_file.call_count == 1
    storage.client.download_file.side_effect = ClientError({"Error": {"Code": "SlowDown"}}, "GetObject")
    other = storage.reference(storage.key("clips", "task", "other", "clip.mp4"))
    assert storage.caption_settings(other) is None


def test_editor_snapshot_skips_unfinished_exports_and_restores_only_preview(storage, tmp_path):
    directory = tmp_path / "editor" / ("c" * 64)
    directory.mkdir(parents=True)
    (directory / "asset.json").write_text('{"status": "ready"}')
    (directory / "clean.mp4").write_bytes(b"preview")
    job = "d" * 32
    output = directory / f"export-{job}.mp4"
    output.write_bytes(b"unfinished")
    (directory / f"job-{job}.json").write_text('{"status": "rendering"}')
    storage.save_editor(directory)
    export_key = storage.parse(storage.editor_reference(directory, output.name))
    assert export_key not in storage.client.objects
    output.write_bytes(b"finished")
    storage.persist_editor_export(directory, job)
    assert storage.client.objects[export_key] == b"finished"
    (directory / "clean.mp4").unlink()
    output.unlink()
    storage.restore_editor(directory, media=True)
    assert (directory / "clean.mp4").read_bytes() == b"preview"
    assert not output.exists()
    storage.enabled = False
    (directory / "clean.mp4").unlink()
    storage.restore_editor(directory, media=True)
    assert (directory / "clean.mp4").read_bytes() == b"preview"


async def test_legacy_cached_path_takes_a_lease_until_last_reader_finishes(storage):
    key = storage.key("sources", "owner", "upload", "video.mp4")
    storage.client.objects[key] = b"video"
    reference = storage.reference(key)
    available, started, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
    paths = []

    @media_work
    async def reader():
        await available.wait()
        path = await asyncio.to_thread(storage.local_path, str(paths[0]))
        started.set()
        await finish.wait()
        assert path.exists()

    @media_work
    async def first():
        paths.append(await asyncio.to_thread(storage.local_path, reference))
        available.set()
        await started.wait()

    one, two = asyncio.create_task(first()), asyncio.create_task(reader())
    await one
    assert paths[0].exists()
    finish.set()
    await two
    assert not paths[0].exists()


async def test_cancellation_waits_for_thread_before_releasing_media(storage):
    import threading
    from src.utils.async_helpers import run_in_thread
    key = storage.key("sources", "owner", "upload", "video.mp4")
    storage.client.objects[key] = b"video"
    started, finish = threading.Event(), threading.Event()
    paths = []

    def render():
        path = storage.local_path(storage.reference(key))
        paths.append(path)
        started.set()
        assert finish.wait(timeout=5)
        assert path.exists()

    @media_work
    async def operation():
        await run_in_thread(render)

    task = asyncio.create_task(operation())
    await asyncio.to_thread(started.wait, 5)
    assert started.is_set()
    task.cancel()
    await asyncio.sleep(0)
    assert paths[0].exists()
    assert not task.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not paths[0].exists()


async def test_failed_analysis_preserves_source_timings_and_retries_upload(storage):
    from boto3.exceptions import S3UploadFailedError
    from src.utils.async_helpers import run_in_thread
    key = storage.key("sources", "owner", "upload", "video.mp4")
    storage.client.objects[key] = b"video"
    upload = storage.client.upload_file
    storage.client.upload_file = Mock(side_effect=S3UploadFailedError("SlowDown"))
    paths = []

    @media_work
    async def process():
        path = await run_in_thread(storage.local_path, storage.reference(key))
        paths.append(path)
        path.with_suffix(".transcript_cache.json").write_text('{"words": [{"text": "Paid transcription"}]}')
        raise RuntimeError("LLM analysis failed")

    with pytest.raises(RuntimeError, match="LLM"):
        await process()
    path = paths[0]
    assert not path.exists()
    assert path.with_suffix(".transcript_cache.json").exists()
    assert (path.parent / ".metadata-pending").exists()
    storage.client.upload_file = upload
    storage.sync_source_metadata()
    assert not (path.parent / ".metadata-pending").exists()
    assert b"Paid transcription" in storage.client.objects[str(Path(key).with_suffix(".transcript_cache.json"))]


def test_optional_reader_blocks_lease_release_until_metadata_read_finishes(storage):
    from src.media_storage import _release_workspace
    import fcntl
    key = storage.key("clips", "task", "upload", "video.mp4")
    storage.client.objects[key] = b"video"
    storage.client.objects[str(Path(key).with_suffix(".source_map.json"))] = b'{}'
    with storage.read_path(storage.reference(key), metadata_only=True) as path:
        handle = (path.parent / ".lease").open("a")
        fcntl.flock(handle, fcntl.LOCK_SH)
        _release_workspace({"files": {path.parent: handle}})
        assert path.with_suffix(".source_map.json").exists()


def test_local_editor_metadata_without_manifest_skips_s3(storage, tmp_path):
    directory = tmp_path / "editor" / ("b" * 64)
    directory.mkdir(parents=True)
    for name, value in [('asset.json', '{"status": "ready"}'), ('original.json', '{}'), ('draft.json', '{}')]:
        (directory / name).write_text(value)
    storage.client.get_paginator = Mock(side_effect=OSError("offline"))
    storage.restore_editor(directory)
    storage.enabled = False
    storage.restore_editor(directory, s3_backed=True)
    storage.client.get_paginator.assert_not_called()


async def test_active_editor_render_is_protected_from_cache_pruning(storage, tmp_path):
    directory = tmp_path / "editor" / ("c" * 64)
    directory.mkdir(parents=True)
    (directory / 'asset.json').write_text('{"status": "ready"}')
    (directory / 'clean.mp4').write_bytes(b'preview')
    storage.save_editor(directory)

    @media_work
    async def render():
        await asyncio.to_thread(storage.restore_editor, directory, media=True)
        old = time.time() - storage.config.media_cache_ttl_seconds - 1
        os.utime(directory / '.access', (old, old))
        storage.sweep_editor_cache()
        assert (directory / 'clean.mp4').exists()

    await render()
    storage.sweep_editor_cache()
    assert not (directory / 'clean.mp4').exists()


def test_snapshot_retry_continues_after_boto3_wrapped_error(storage, tmp_path):
    from boto3.exceptions import S3UploadFailedError
    first, second = tmp_path / 'editor' / 'first', tmp_path / 'editor' / 'second'
    for directory in (first, second):
        directory.mkdir(parents=True)
        (directory / 'draft.json').write_text('{}')
    original = storage.save_editor
    def save(directory, **kwargs):
        if directory == first:
            raise S3UploadFailedError('SlowDown')
        return original(directory, **kwargs)
    storage.save_editor = save
    storage.sync_editor_drafts()
    assert storage.parse(storage.editor_reference(second, 'draft.json')) in storage.client.objects


def test_signed_parts_enforce_exact_bytes_and_refresh_declared_size(storage):
    storage._client = Mock()
    storage.client.create_multipart_upload.return_value = {"UploadId": "multipart"}
    storage.begin_upload('owner', 'video.mp4', PART_BYTES + 7, 'video/mp4')
    calls = storage.client.generate_presigned_url.call_args_list
    assert [c.kwargs['Params']['ContentLength'] for c in calls] == [PART_BYTES, 7]
    payload = {'uploadId': 'multipart', 'video_path': f"upload://s3/{owner_key('owner')}/upload/video.mp4", 'partNumbers': [2]}
    with pytest.raises(ValueError, match='part numbers'):
        storage.refresh_upload_parts('owner', payload, size=1)
    storage.refresh_upload_parts('owner', payload, size=PART_BYTES + 7)
    assert storage.client.generate_presigned_url.call_args.kwargs['Params']['ContentLength'] == 7


@pytest.mark.parametrize('filename', ['recording', 'recording.qt', 'recording.f4v', 'recording.dv'])
def test_legacy_video_names_remain_uploadable(storage, filename):
    storage._client = Mock()
    storage.client.create_multipart_upload.return_value = {"UploadId": "multipart"}
    assert storage.begin_upload('owner', filename, 7, 'video/mp4')['video_path'].endswith('.mp4')


@pytest.mark.parametrize("prefix", ["apify-", "oxy-", "videoscale-", "ytdl-"])
@pytest.mark.parametrize("recent", [False, True])
def test_orphan_scan_reclaims_retired_downloads_after_grace(storage, tmp_path, prefix, recent):
    from src.workers.media_cleanup import orphan_candidates

    directory = tmp_path / f"{prefix}abcdefghijk-test"
    directory.mkdir()
    media = directory / "abcdefghijk.mp4"
    media.write_bytes(b"temporary source")
    old = time.time() - storage.config.media_orphan_grace_seconds - 60
    os.utime(media, (old, old))
    os.utime(directory, (old, old))
    if recent:
        os.utime(media, None)
    candidates = list(orphan_candidates(storage))
    assert ((str(directory), True) in candidates) is (not recent)


def test_editor_restores_generated_layout_when_raw_media_is_already_local(storage, tmp_path):
    directory = tmp_path / "editor" / ("f" * 64)
    directory.mkdir(parents=True)
    (directory / "asset.json").write_text('{"status":"ready","hasLayout":true}')
    for name in ["draft.json", "original.json"]:
        (directory / name).write_text('{}')
    (directory / "clean.mp4").write_bytes(b"wide source")
    (directory / "layout.mp4").write_bytes(b"full content composition")
    storage.save_editor(directory)
    (directory / "layout.mp4").unlink()
    storage.restore_editor(directory, media=True)
    assert (directory / "clean.mp4").read_bytes() == b"wide source"
    assert (directory / "layout.mp4").read_bytes() == b"full content composition"
