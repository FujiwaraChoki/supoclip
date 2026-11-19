"""Publishing an upload hashes the bytes and does not transcode them."""

import hashlib
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.datastructures import UploadFile

from src.config import Config, set_config_override
from src.media.upload_ingest import (
    UploadRejected,
    foreign_upload,
    persist_local_upload,
    publish_spooled_upload,
)
from src.media_storage import MediaStorage
from src.services.video_service import VideoService


@pytest.fixture(autouse=True)
def no_fragment_reads(monkeypatch):
    """Signed URLs in these tests are fake; the fragmented MP4 reader must not fetch them."""
    monkeypatch.setattr("src.services.video_service.fragmented_mp4_duration", lambda _source: None)


@pytest.fixture(autouse=True)
def runtime_config(tmp_path):
    config = Config()
    config.temp_dir = str(tmp_path)
    config.media_storage = "local"
    set_config_override(config)
    yield config
    set_config_override(None)


@pytest.mark.asyncio
async def test_publish_spooled_upload_hashes_and_keeps_the_bytes(tmp_path):
    payload = b"video-bytes"
    source = tmp_path / "spool.mp4"
    source.write_bytes(payload)
    upload = UploadFile(filename="demo.mp4", file=source.open("rb"))
    dest = tmp_path / "uploads" / "out.mp4"

    digest, size = await publish_spooled_upload(upload, dest, max_bytes=100)
    await upload.close()

    assert size == len(payload)
    assert digest == hashlib.sha256(payload).hexdigest()
    assert dest.read_bytes() == payload
    assert dest.exists()


@pytest.mark.asyncio
async def test_publish_falls_back_to_a_copy_when_the_spool_cannot_be_linked(tmp_path):
    payload = b"copied"
    upload = UploadFile(filename="demo.mp4", file=io.BytesIO(payload))
    dest = tmp_path / "uploads" / "out.mp4"

    digest, size = await publish_spooled_upload(upload, dest, max_bytes=100)

    assert (digest, size) == (hashlib.sha256(payload).hexdigest(), len(payload))
    assert dest.read_bytes() == payload


@pytest.mark.asyncio
async def test_publish_rejects_an_empty_or_oversized_file(tmp_path):
    empty = UploadFile(filename="demo.mp4", file=io.BytesIO(b""))
    with pytest.raises(UploadRejected) as empty_error:
        await publish_spooled_upload(empty, tmp_path / "empty.mp4", max_bytes=100)
    assert empty_error.value.status_code == 400

    oversized = UploadFile(filename="demo.mp4", file=io.BytesIO(b"abcdef"))
    with pytest.raises(UploadRejected) as large_error:
        await publish_spooled_upload(oversized, tmp_path / "large.mp4", max_bytes=3)
    assert large_error.value.status_code == 413
    assert not (tmp_path / "large.mp4").exists()


@pytest.mark.asyncio
async def test_persist_local_upload_is_a_no_op_without_object_storage():
    await persist_local_upload("upload://missing.mp4")


@pytest.mark.asyncio
async def test_prepare_source_copies_before_normalizing(tmp_path, monkeypatch):
    path = tmp_path / "uploads" / "source.mp4"
    path.parent.mkdir()
    path.write_bytes(b"source")
    order = []

    async def persist(url):
        order.append(("persist", url))

    def normalize(source):
        order.append(("normalize", Path(source).name))
        return source

    monkeypatch.setattr("src.media.upload_ingest.persist_local_upload", persist)
    monkeypatch.setattr("src.services.video_service.normalize_source_media", normalize)

    prepared = await VideoService.prepare_source(path)

    assert prepared == path
    assert order == [("persist", "upload://source.mp4"), ("normalize", "source.mp4")]


@pytest.mark.asyncio
async def test_unowned_local_file_is_not_a_usable_upload(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "clip.mp4").write_bytes(b"private")

    async def no_row(_db, _path):
        return None

    monkeypatch.setattr("src.media.upload_ingest.get_stored_upload", no_row)
    assert await foreign_upload(object(), "upload://clip.mp4", "user-1") is True
    (uploads / "clip.mp4").unlink()
    assert await foreign_upload(object(), "upload://missing.mp4", "user-1") is False
    assert await foreign_upload(None, "upload://clip.mp4", "user-1") is False
    assert await foreign_upload(object(), "upload://s3/owner/clip.mp4", "user-1") is False
    assert await foreign_upload(object(), "upload://../secret.mp4", "user-1") is True

    async def other_owner(_db, _path):
        return SimpleNamespace(user_id="user-2")

    monkeypatch.setattr("src.media.upload_ingest.get_stored_upload", other_owner)
    assert await foreign_upload(object(), "upload://clip.mp4", "user-1") is True

    async def same_owner(_db, _path):
        return SimpleNamespace(user_id="user-1")

    monkeypatch.setattr("src.media.upload_ingest.get_stored_upload", same_owner)
    assert await foreign_upload(object(), "upload://clip.mp4", "user-1") is False


def test_probe_uses_storage_reference_when_the_local_file_is_gone(runtime_config, monkeypatch):
    runtime_config.media_storage = "s3"
    runtime_config.media_s3_bucket = "bucket"
    seen = {}

    def signed_url(self, reference, filename, **_kwargs):
        seen["reference"] = reference
        seen["filename"] = filename
        return "https://signed.example/video"

    monkeypatch.setattr(MediaStorage, "signed_url", signed_url)
    monkeypatch.setattr(
        "src.services.video_service.ffprobe_duration",
        lambda target, timeout=60: 21.0 if target == "https://signed.example/video" and timeout < 60 else (_ for _ in ()).throw(AssertionError(target)),
    )

    duration = VideoService.probe_upload_duration(
        "upload://gone.mp4", "user-1", "s3://bucket/sources/gone.mp4",
    )

    assert duration == 21.0
    assert seen == {"reference": "s3://bucket/sources/gone.mp4", "filename": "source.mp4"}


def test_probe_of_a_storage_upload_gives_up_before_the_proxy_timeout(runtime_config, monkeypatch):
    runtime_config.media_storage = "s3"
    runtime_config.media_s3_bucket = "bucket"
    seen = {}
    monkeypatch.setattr(MediaStorage, "validate_source", lambda self, url, user_id: "s3://bucket/sources/big.mp4")
    monkeypatch.setattr(MediaStorage, "signed_url", lambda self, reference, filename, **_kwargs: "https://signed.example/big")

    def probe(target, timeout=60):
        seen["call"] = (target, timeout)
        return 30.0

    monkeypatch.setattr("src.services.video_service.ffprobe_duration", probe)

    assert VideoService.probe_upload_duration("upload://s3/user-1/big.mp4", "user-1") == 30.0
    assert seen["call"][0] == "https://signed.example/big"
    # The create-task request sits behind a proxy that gives up after 60 s.
    assert seen["call"][1] < 60


def test_fragmented_upload_in_storage_is_measured_without_ffprobe(runtime_config, monkeypatch):
    runtime_config.media_storage = "s3"
    runtime_config.media_s3_bucket = "bucket"
    monkeypatch.setattr(MediaStorage, "validate_source", lambda self, url, user_id: "s3://bucket/sources/rec.mp4")
    monkeypatch.setattr(MediaStorage, "signed_url", lambda self, reference, filename, **_kwargs: "https://signed.example/rec")
    monkeypatch.setattr(
        "src.services.video_service.fragmented_mp4_duration",
        lambda source: 3600.5 if source == "https://signed.example/rec" else None,
    )
    monkeypatch.setattr(
        "src.services.video_service.ffprobe_duration",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("ffprobe walks every fragment")),
    )

    assert VideoService.probe_upload_duration("upload://s3/user-1/rec.mp4", "user-1") == 3600.5


def test_ffprobe_gets_what_is_left_of_the_probe_budget(runtime_config, monkeypatch):
    runtime_config.media_storage = "s3"
    runtime_config.media_s3_bucket = "bucket"
    monkeypatch.setattr(MediaStorage, "validate_source", lambda self, url, user_id: "s3://bucket/sources/big.mp4")
    monkeypatch.setattr(MediaStorage, "signed_url", lambda self, reference, filename, **_kwargs: "https://signed.example/big")
    clock = iter([100.0, 112.0])
    monkeypatch.setattr("src.services.video_service.time", SimpleNamespace(monotonic=lambda: next(clock)))
    seen = {}

    def probe(target, timeout=60):
        seen["timeout"] = timeout
        return 30.0

    monkeypatch.setattr("src.services.video_service.ffprobe_duration", probe)

    assert VideoService.probe_upload_duration("upload://s3/user-1/big.mp4", "user-1") == 30.0
    # The fragment reader used 12 s of the 40 s budget.
    assert seen["timeout"] == pytest.approx(28.0)


def test_fragmented_local_upload_is_measured_without_ffprobe(tmp_path, runtime_config, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "rec.mp4").write_bytes(b"video")
    monkeypatch.setattr(
        "src.services.video_service.fragmented_mp4_duration",
        lambda source: 7.5 if Path(source).name == "rec.mp4" else None,
    )
    monkeypatch.setattr(
        "src.services.video_service.ffprobe_duration",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("ffprobe")),
    )

    assert VideoService.probe_upload_duration("upload://rec.mp4", "user-1") == 7.5


def test_probe_reads_a_local_file_before_the_storage_reference(tmp_path, runtime_config, monkeypatch):
    runtime_config.media_storage = "s3"
    runtime_config.media_s3_bucket = "bucket"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "here.mp4").write_bytes(b"video")
    monkeypatch.setattr(MediaStorage, "signed_url", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("signed")))
    monkeypatch.setattr(
        "src.services.video_service.ffprobe_duration",
        lambda target: 4.0 if Path(target).name == "here.mp4" else (_ for _ in ()).throw(AssertionError(str(target))),
    )

    assert VideoService.probe_upload_duration("upload://here.mp4", "user-1", "s3://bucket/sources/here.mp4") == 4.0


def test_probe_ignores_storage_reference_when_object_storage_is_off(runtime_config, monkeypatch):
    monkeypatch.setattr(MediaStorage, "signed_url", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("signed")))
    monkeypatch.setattr(
        "src.services.video_service.ffprobe_duration",
        lambda _target: (_ for _ in ()).throw(RuntimeError("missing file")),
    )

    with pytest.raises(RuntimeError, match="missing file"):
        VideoService.probe_upload_duration("upload://gone.mp4", "user-1", "s3://bucket/sources/gone.mp4")


@pytest.mark.asyncio
async def test_preflight_passes_the_stored_reference(monkeypatch):
    from src.api.routes.tasks import _preflight_upload_source

    async def row(_db, path):
        assert path == "upload://gone.mp4"
        return SimpleNamespace(storage_reference="s3://bucket/sources/gone.mp4")

    seen = {}

    def probe(url, user_id, reference=None):
        seen["args"] = (url, user_id, reference)
        return 8.0

    monkeypatch.setattr("src.api.routes.tasks.get_stored_upload", row)
    monkeypatch.setattr("src.api.routes.tasks.VideoService.probe_upload_duration", staticmethod(probe))

    duration = await _preflight_upload_source("upload://gone.mp4", "user-1", 3600, object())

    assert duration == 8.0
    assert seen["args"] == ("upload://gone.mp4", "user-1", "s3://bucket/sources/gone.mp4")


@pytest.mark.asyncio
async def test_preflight_without_a_session_does_not_look_up_storage(monkeypatch):
    from src.api.routes.tasks import _preflight_upload_source

    async def unexpected(_db, _path):
        raise AssertionError("lookup")

    def probe(url, user_id, *rest):
        assert rest == ()
        assert url == "upload://x.mp4"
        assert user_id == "user-1"
        return 5.0

    monkeypatch.setattr("src.api.routes.tasks.get_stored_upload", unexpected)
    monkeypatch.setattr("src.api.routes.tasks.VideoService.probe_upload_duration", staticmethod(probe))

    assert await _preflight_upload_source("upload://x.mp4", "user-1", 3600) == 5.0


def test_slow_storage_checks_count_against_the_probe_budget(runtime_config, monkeypatch):
    runtime_config.media_storage = "s3"
    runtime_config.media_s3_bucket = "bucket"
    now = {"t": 100.0}

    def slow(seconds, result):
        def call(*_args, **_kwargs):
            now["t"] += seconds
            return result
        return call

    # A rate-limited store is retried, so checking and signing can take a while.
    monkeypatch.setattr(MediaStorage, "validate_source", slow(20.0, "s3://bucket/sources/big.mp4"))
    monkeypatch.setattr(MediaStorage, "signed_url", slow(10.0, "https://signed.example/big"))
    monkeypatch.setattr("src.services.video_service.time", SimpleNamespace(monotonic=lambda: now["t"]))
    seen = {}

    def probe(target, timeout=60):
        seen["timeout"] = timeout
        return 30.0

    monkeypatch.setattr("src.services.video_service.ffprobe_duration", probe)

    assert VideoService.probe_upload_duration("upload://s3/user-1/big.mp4", "user-1") == 30.0
    assert seen["timeout"] == pytest.approx(10.0)
