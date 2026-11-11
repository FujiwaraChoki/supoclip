"""Uploaded subtitles are stored per owner and resolved only for their owner."""
import hashlib

import pytest

from src.media.srt_transcript import SrtSubtitleError
from src.media_storage import owner_key
from src import srt_uploads
from src.srt_uploads import (
    SRT_MAX_UPLOAD_BYTES,
    is_srt_reference,
    srt_digest,
    srt_upload_path,
    store_srt_bytes,
    validate_srt_reference,
)

USER = "user-1"
OTHER_USER = "user-2"
SRT = b"1\n00:00:00,000 --> 00:00:02,000\nhello there\n"


@pytest.fixture(autouse=True)
def temp_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TEMP_DIR", str(tmp_path))
    return tmp_path


def test_store_returns_an_owner_scoped_content_addressed_reference(temp_dir):
    reference, cue_count = store_srt_bytes(USER, SRT)

    digest = hashlib.sha256(SRT).hexdigest()
    assert reference == f"srt://{owner_key(USER)}/{digest}.srt"
    assert cue_count == 1
    assert is_srt_reference(reference)
    assert srt_digest(reference) == digest
    stored = temp_dir / "uploads" / "srt" / owner_key(USER) / f"{digest}.srt"
    assert stored.read_bytes() == SRT


def test_storing_the_same_bytes_twice_keeps_one_file(temp_dir):
    first, _ = store_srt_bytes(USER, SRT)
    second, _ = store_srt_bytes(USER, SRT)

    assert first == second
    files = list((temp_dir / "uploads" / "srt" / owner_key(USER)).iterdir())
    assert [path.name for path in files] == [f"{hashlib.sha256(SRT).hexdigest()}.srt"]


def test_store_rejects_a_file_without_cues():
    with pytest.raises(SrtSubtitleError):
        store_srt_bytes(USER, b"nothing to see here")


def test_store_rejects_an_oversized_file(monkeypatch):
    monkeypatch.setattr(srt_uploads, "SRT_MAX_UPLOAD_BYTES", 4)

    with pytest.raises(SrtSubtitleError):
        store_srt_bytes(USER, SRT)


def test_validate_resolves_the_uploaders_file():
    reference, _ = store_srt_bytes(USER, SRT)

    path = validate_srt_reference(reference, USER)

    assert path.read_bytes() == SRT


def test_validate_refuses_another_users_reference():
    reference, _ = store_srt_bytes(USER, SRT)

    with pytest.raises(SrtSubtitleError):
        validate_srt_reference(reference, OTHER_USER)


def test_validate_refuses_a_malformed_reference():
    with pytest.raises(SrtSubtitleError):
        validate_srt_reference("upload://nope.srt", USER)


def test_validate_refuses_a_missing_file():
    reference, _ = store_srt_bytes(USER, SRT)
    srt_upload_path(reference).unlink()

    with pytest.raises(SrtSubtitleError):
        validate_srt_reference(reference, USER)


def test_validate_refuses_a_file_whose_bytes_changed():
    reference, _ = store_srt_bytes(USER, SRT)
    srt_upload_path(reference).write_bytes(b"1\n00:00:00,000 --> 00:00:01,000\nchanged\n")

    with pytest.raises(SrtSubtitleError):
        validate_srt_reference(reference, USER)


def test_max_upload_size_is_five_megabytes():
    assert SRT_MAX_UPLOAD_BYTES == 5 * 1024 * 1024