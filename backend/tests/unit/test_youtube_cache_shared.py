"""The YouTube source cache as seen by two machines that share only object storage."""

from contextlib import nullcontext
import json
import os
import time
from pathlib import Path

import pytest

from src import youtube_source as source
from src.config import Config
from src.media_storage import MediaStorage
from tests.unit.test_media_storage import MemoryS3

VIDEO_ID = "abcdefghijk"


@pytest.fixture
def machines(tmp_path, monkeypatch):
    """Two cache directories on separate disks, one bucket."""
    client = MemoryS3()
    current = {}

    def machine(name):
        config = Config()
        config.media_storage = "s3"
        config.media_s3_bucket = "private-media"
        config.temp_dir = str(tmp_path / name)
        directory = tmp_path / name / "youtube-cache" / VIDEO_ID
        directory.mkdir(parents=True)
        return MediaStorage(config, client), directory

    first, second = machine("first"), machine("second")

    def use(which):
        current["storage"] = which[0]
        return which[1]

    monkeypatch.setattr(source, "_storage", lambda: current["storage"])
    # The lock is PostgreSQL's; these tests run without a database.
    monkeypatch.setattr("src.sync_db.advisory_lock", lambda *args, **kwargs: nullcontext())
    return client, first, second, use


def cache_range(directory: Path, start: float, end: float, body: bytes) -> str:
    name = source._range_filename(start, end)
    (directory / name).write_bytes(body)
    manifest = source._load_manifest(directory, VIDEO_ID)
    manifest["ranges"].append({"start": start, "end": end, "file": name, "bytes": len(body)})
    source._save_manifest(directory, manifest)
    source._persist_named(VIDEO_ID, directory / name)
    source._persist_manifest(VIDEO_ID, directory)
    return name


def stored_manifest(client) -> dict:
    return json.loads(client.objects[f"media/youtube-cache/{VIDEO_ID}/manifest.json"])


def test_each_machine_adds_to_the_stored_manifest_instead_of_replacing_it(machines):
    client, first, second, use = machines
    one = cache_range(use(first), 0, 30, b"first section")
    two = cache_range(use(second), 60, 90, b"second section")
    assert [item["file"] for item in stored_manifest(client)["ranges"]] == [two, one]
    # Storing again from the first machine keeps the second machine's entry.
    source._persist_manifest(VIDEO_ID, use(first))
    assert {item["file"] for item in stored_manifest(client)["ranges"]} == {one, two}
    # A machine's own manifest only lists files it has.
    assert [item["file"] for item in source._load_manifest(first[1], VIDEO_ID)["ranges"]] == [one]


def test_a_machine_with_its_own_cache_fetches_what_another_machine_added(machines):
    client, first, second, use = machines
    directory = use(second)
    two = cache_range(directory, 60, 90, b"second section")
    source._restore_remote_cache(VIDEO_ID, directory)
    one = cache_range(use(first), 0, 30, b"first section")

    directory = use(second)
    # Checked a moment ago: a job that asks several times does not hit storage each time.
    source._restore_remote_cache(VIDEO_ID, directory)
    assert not (directory / one).exists()
    stale = time.time() - source.REMOTE_REFRESH_SECONDS - 1
    os.utime(directory / source.REMOTE_CHECKED_NAME, (stale, stale))
    source._restore_remote_cache(VIDEO_ID, directory)
    assert (directory / one).read_bytes() == b"first section"
    pieces = source._pieces(directory, source._load_manifest(directory, VIDEO_ID))
    assert [(piece.start, piece.end) for piece in pieces] == [(0, 30), (60, 90)]
    assert (directory / two).read_bytes() == b"second section"


def test_a_new_machine_restores_the_whole_cache_and_a_failed_fetch_is_retried(machines):
    client, first, second, use = machines
    one = cache_range(use(first), 0, 30, b"first section")
    two = cache_range(first[1], 60, 90, b"second section")
    lost = client.objects.pop(f"media/youtube-cache/{VIDEO_ID}/{two}")

    directory = use(second)
    source._restore_remote_cache(VIDEO_ID, directory)
    # Only what arrived is recorded, so the missing section is not mistaken for cached.
    assert [item["file"] for item in source._load_manifest(directory, VIDEO_ID)["ranges"]] == [one]
    client.objects[f"media/youtube-cache/{VIDEO_ID}/{two}"] = lost
    stale = time.time() - source.REMOTE_REFRESH_SECONDS - 1
    os.utime(directory / source.REMOTE_CHECKED_NAME, (stale, stale))
    source._restore_remote_cache(VIDEO_ID, directory)
    assert {item["file"] for item in source._load_manifest(directory, VIDEO_ID)["ranges"]} == {one, two}


def test_a_better_full_file_from_another_machine_replaces_a_lower_one():
    local = {"audio_policy": source.AUDIO_POLICY, "full": "full-720.mp4", "full_height": 720, "ranges": []}
    remote = {"audio_policy": source.AUDIO_POLICY, "full": "full-1080.mp4", "full_height": 1080,
              "audio": "audio.m4a", "ranges": []}
    merged = source._merge_manifests(local, remote)
    assert (merged["full"], merged["full_height"], merged["audio"]) == ("full-1080.mp4", 1080, "audio.m4a")
    assert source._merge_manifests(remote, local)["full"] == "full-1080.mp4"
    # Audio and sections stored under an earlier audio policy are not adopted.
    outdated = {**remote, "audio_policy": "earlier", "ranges": [{"start": 0, "end": 1, "file": "r.mp4"}]}
    merged = source._merge_manifests({"audio_policy": source.AUDIO_POLICY, "ranges": []}, outdated)
    assert merged.get("audio") is None and merged["ranges"] == [] and merged["full"] == "full-1080.mp4"


def test_long_section_resumes_from_last_completed_fragment_on_another_worker(machines, monkeypatch):
    client, first, second, use = machines
    runtime = Config()
    runtime.youtube_proxy_url = "http://proxy.example"
    runtime.youtube_section_padding_seconds = 0
    monkeypatch.setattr(source, "get_config", lambda: runtime)
    calls = []

    def download(url, video_id, start, end, dest, *, task_id, budget):
        calls.append((start, end))
        if dest.parent == first[1] and len(calls) == 2:
            raise TimeoutError("connection lost")
        # Simulate whole fragments covering a little extra at both ends.
        dest.write_bytes(b"complete media")
        return dest, max(0, start - 1), end + 2

    monkeypatch.setattr(source, "download_section_file", download)
    monkeypatch.setattr(source, "_trim_local", lambda path, dest, *args: dest.write_bytes(path.read_bytes()))
    monkeypatch.setattr(source, "_concat_local", lambda files, dest, **kwargs: dest.write_bytes(b"render"))

    def render(machine):
        directory = use(machine)
        runtime.temp_dir = str(directory.parent.parent)
        return source.materialize_clip_source(
            "https://www.youtube.com/watch?v=abcdefghijk", VIDEO_ID,
            [{"start_time": 15, "end_time": 605}], duration=900,
        )

    with pytest.raises(TimeoutError):
        render(first)
    checkpoint = stored_manifest(client)["ranges"]
    assert len(checkpoint) == 1 and checkpoint[0]["end"] == 77
    assert calls == [(15, 75), (77, 137)]
    calls.clear()
    result = render(second)
    assert calls[0] == (77, 137)
    assert calls[-1][1] == 605
    assert all(end - start <= 60 for start, end in calls)
    timeline = source.load_time_map(result)
    assert timeline.to_local_ranges([(15, 605)], strict=True) == [(0, 590)]
    assert timeline.to_original_ranges([(0, 590)]) == [(15, 605)]
