"""Publishing an upload hashes the bytes and does not transcode them."""

import hashlib
import io
from pathlib import Path

import pytest
from starlette.datastructures import UploadFile

from src.config import Config, set_config_override
from src.media.upload_ingest import (
    UploadRejected,
    persist_local_upload,
    publish_spooled_upload,
)
from src.services.video_service import VideoService


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
