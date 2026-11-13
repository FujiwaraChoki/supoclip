"""The studio's optional ``matte.mp4``: published by the FX analysis job, restored by version on any machine (AI-H7).

Its ``.matte-version`` marker is separate from ``.media-version``: the matte is published after preparation, so a
machine whose media is current must still fetch it.
"""

from pathlib import Path
from unittest.mock import Mock

import pytest

from src.config import Config
from src.media_storage import EDITOR_OPTIONAL_MEDIA_NAMES, MATTE_VERSION_MARKER, MediaStorage
from tests.unit.test_media_storage import MemoryS3


@pytest.fixture
def storage(tmp_path):
    config = Config()
    config.media_storage = "s3"
    config.media_s3_bucket = "private-media"
    config.temp_dir = str(tmp_path / "api-machine")
    return MediaStorage(config, MemoryS3())


def other_machine(storage, tmp_path, name="render-machine"):
    config = Config()
    config.media_storage = "s3"
    config.media_s3_bucket = storage.bucket
    config.temp_dir = str(tmp_path / name)
    return MediaStorage(config, storage.client)


def prepared_session(storage, tmp_path, *, media_version="v1"):
    """A session prepared on one machine: media published, then its matte published by the analysis job."""
    directory = Path(storage.config.temp_dir) / "editor" / ("a" * 64)
    directory.mkdir(parents=True)
    (directory / "clean.mp4").write_bytes(b"clean")
    (directory / "layout.mp4").write_bytes(b"layout")
    storage.publish_editor_media(directory)
    (directory / ".media-version").write_text(media_version)
    return directory


def publish_matte(storage, directory, content: bytes):
    (directory / "matte.mp4").write_bytes(content)
    storage.publish_editor_media(directory, names=("matte.mp4",))


def remote_dir(remote, directory):
    return Path(remote.config.temp_dir) / "editor" / directory.name


def test_the_matte_is_an_optional_editor_media_name():
    assert EDITOR_OPTIONAL_MEDIA_NAMES == ("matte.mp4",)
    assert MATTE_VERSION_MARKER == ".matte-version"


def test_matte_reaches_a_machine_whose_media_is_already_current(storage, tmp_path):
    directory = prepared_session(storage, tmp_path)
    remote = other_machine(storage, tmp_path)
    target = remote_dir(remote, directory)
    remote.restore_editor_media(target, has_layout=True, version="v1")
    assert (target / ".media-version").read_text() == "v1"
    assert not (target / "matte.mp4").exists()

    # The analysis publishes later; the render on the remote machine names the new matte version.
    publish_matte(storage, directory, b"matte one")
    remote.restore_editor_media(target, has_layout=True, version="v1", matte_version="m1")
    assert (target / "matte.mp4").read_bytes() == b"matte one"
    assert (target / MATTE_VERSION_MARKER).read_text() == "m1"
    # Media already current was not fetched again.
    assert (target / "clean.mp4").read_bytes() == b"clean"

    # A second render with the same versions reuses everything.
    storage.client.download_file = Mock(side_effect=AssertionError("cached"))
    remote.restore_editor_media(target, has_layout=True, version="v1", matte_version="m1")


def test_a_newer_matte_replaces_the_cached_copy(storage, tmp_path):
    directory = prepared_session(storage, tmp_path)
    publish_matte(storage, directory, b"matte one")
    remote = other_machine(storage, tmp_path)
    target = remote_dir(remote, directory)
    remote.restore_editor_media(target, version="v1", matte_version="m1")
    publish_matte(storage, directory, b"matte two")
    remote.restore_editor_media(target, version="v1", matte_version="m2")
    assert (target / "matte.mp4").read_bytes() == b"matte two"
    assert (target / MATTE_VERSION_MARKER).read_text() == "m2"


def test_a_missing_matte_is_absent_not_an_error_and_never_claims_the_version(storage, tmp_path):
    directory = prepared_session(storage, tmp_path)
    remote = other_machine(storage, tmp_path)
    target = remote_dir(remote, directory)
    remote.restore_editor_media(target, version="v1", matte_version="m1")
    assert (target / "clean.mp4").exists()
    assert not (target / "matte.mp4").exists()
    assert not (target / MATTE_VERSION_MARKER).exists()


def test_a_stale_matte_whose_download_fails_is_not_left_behind(storage, tmp_path):
    directory = prepared_session(storage, tmp_path)
    publish_matte(storage, directory, b"matte one")
    remote = other_machine(storage, tmp_path)
    target = remote_dir(remote, directory)
    remote.restore_editor_media(target, version="v1", matte_version="m1")
    del storage.client.objects[storage.parse(storage.editor_reference(directory, "matte.mp4"))]
    remote.restore_editor_media(target, version="v1", matte_version="m2")
    assert not (target / "matte.mp4").exists()
    assert not (target / MATTE_VERSION_MARKER).exists()


def test_a_rebuilt_session_drops_its_old_matte(storage, tmp_path):
    directory = prepared_session(storage, tmp_path)
    publish_matte(storage, directory, b"matte one")
    remote = other_machine(storage, tmp_path)
    target = remote_dir(remote, directory)
    remote.restore_editor_media(target, version="v1", matte_version="m1")
    (directory / "clean.mp4").write_bytes(b"clean rebuilt")
    storage.publish_editor_media(directory)
    # The rebuilt session has no matte yet: restoring its media removes the old one.
    remote.restore_editor_media(target, version="v2")
    assert (target / "clean.mp4").read_bytes() == b"clean rebuilt"
    assert not (target / "matte.mp4").exists()
    assert not (target / MATTE_VERSION_MARKER).exists()


def test_without_a_matte_version_restore_is_unchanged(storage, tmp_path):
    directory = prepared_session(storage, tmp_path)
    publish_matte(storage, directory, b"matte one")
    remote = other_machine(storage, tmp_path)
    target = remote_dir(remote, directory)
    remote.restore_editor_media(target, has_layout=True, version="v1")
    assert sorted(p.name for p in target.iterdir() if not p.name.startswith(".")) == ["clean.mp4", "layout.mp4"]


def test_matte_publish_uploads_only_the_matte(storage, tmp_path):
    directory = prepared_session(storage, tmp_path)
    storage.client.upload_file = Mock(wraps=storage.client.upload_file)
    publish_matte(storage, directory, b"matte one")
    assert storage.client.upload_file.call_count == 1
    assert storage.client.upload_file.call_args.args[2].endswith("/matte.mp4")


def test_local_storage_never_fetches(tmp_path):
    config = Config()
    config.media_storage = "local"
    config.media_s3_bucket = ""
    config.temp_dir = str(tmp_path)
    local = MediaStorage(config, Mock(side_effect=AssertionError("no bucket")))
    directory = tmp_path / "editor" / ("b" * 64)
    local.restore_editor_media(directory, version="v1", matte_version="m1")
    assert directory.is_dir()


def test_publish_editor_matte_installs_uploads_then_marks_under_the_session_lock(storage, tmp_path):
    import fcntl
    import threading

    directory = prepared_session(storage, tmp_path)
    (directory / "matte.mp4").write_bytes(b"old matte")
    (directory / MATTE_VERSION_MARKER).write_text("m0")
    fresh = directory / ".matte-new.mp4"
    fresh.write_bytes(b"new matte")

    # A restore on this machine holds the session lock: the install must wait for it, not race it.
    lock = (directory / ".sync").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX)
    worker = threading.Thread(target=storage.publish_editor_matte, args=(directory, fresh, "m1"))
    worker.start()
    worker.join(timeout=0.3)
    assert worker.is_alive()
    assert (directory / "matte.mp4").read_bytes() == b"old matte"
    fcntl.flock(lock, fcntl.LOCK_UN)
    lock.close()
    worker.join(timeout=5)
    assert not worker.is_alive()

    assert (directory / "matte.mp4").read_bytes() == b"new matte" and not fresh.exists()
    assert (directory / MATTE_VERSION_MARKER).read_text() == "m1"
    remote = other_machine(storage, tmp_path)
    target = remote_dir(remote, directory)
    remote.restore_editor_media(target, has_layout=True, version="v1", matte_version="m1")
    assert (target / "matte.mp4").read_bytes() == b"new matte"


def test_publish_editor_matte_without_a_bucket_installs_and_marks(tmp_path):
    config = Config()
    config.media_storage = "local"
    config.temp_dir = str(tmp_path)
    local = MediaStorage(config, Mock(side_effect=AssertionError("no bucket")))
    directory = tmp_path / "editor" / ("b" * 64)
    directory.mkdir(parents=True)
    fresh = directory / ".matte-new.mp4"
    fresh.write_bytes(b"matte")
    local.publish_editor_matte(directory, fresh, "local.1")
    assert (directory / "matte.mp4").read_bytes() == b"matte"
    assert (directory / MATTE_VERSION_MARKER).read_text() == "local.1"
