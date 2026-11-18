"""An uploader trims a video and keeps its id: cached media of the old version is not reused.

NxgYtzq_n70 went from ~2160 s to 803 s. The cache, keyed by video id, served
the old audio and transcript, the analysis picked moments past 803 s, and the
clips rendered from sections that no longer exist.
"""

from contextlib import nullcontext
import json
from pathlib import Path

import pytest

from src import youtube_source as source
from src.config import Config
from src.media_storage import MediaStorage
from tests.unit.test_media_storage import MemoryS3

VIDEO_ID = "NxgYtzq_n70"
URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
OLD, NEW = 2160.0, 803.0


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    config = Config()
    config.temp_dir = str(tmp_path / "machine")
    config.youtube_proxy_url = "http://proxy.example"
    config.youtube_section_padding_seconds = 0
    config.media_storage = "local"
    monkeypatch.setattr(source, "get_config", lambda: config)
    monkeypatch.setattr(source, "_storage", lambda: None)
    downloads = []

    def download_audio(url, video_id, base, *, task_id=None, budget=None):
        path = base.with_suffix(".m4a")
        path.write_bytes(b"current audio")
        downloads.append(("audio", path.name))
        return path

    def download_section(url, video_id, start, end, dest, *, task_id=None, budget=None):
        dest.write_bytes(b"current section")
        downloads.append(("section", start, end))
        return dest, start, end

    monkeypatch.setattr(source, "download_audio", download_audio)
    monkeypatch.setattr(source, "download_section_file", download_section)
    monkeypatch.setattr(source, "_trim_local", lambda path, dest, *args: dest.write_bytes(path.read_bytes()))
    monkeypatch.setattr(
        source, "_concat_local",
        lambda files, dest, **kwargs: dest.write_bytes(b"".join(Path(f).read_bytes() for f in files)),
    )
    directory = source.cache_dir_for(VIDEO_ID, config)
    directory.mkdir(parents=True)
    return config, directory, downloads


def old_cache(directory: Path, *, recorded: bool = True, full: bool = False) -> dict:
    """The cache as the earlier, longer version of the video left it."""
    audio = f"audio-{source.AUDIO_POLICY}.m4a"
    (directory / audio).write_bytes(b"old audio")
    (directory / audio).with_suffix(".transcript_cache.json").write_text(json.dumps({"words": []}))
    section = source._range_filename(0, 60)
    (directory / section).write_bytes(b"old section")
    manifest = source._empty_manifest(VIDEO_ID)
    manifest.update(audio=audio, ranges=[{"start": 0, "end": 60, "file": section, "bytes": 11}])
    if full:
        (directory / "full-1080.mp4").write_bytes(b"old full")
        manifest.update(full="full-1080.mp4", full_height=1080)
    if recorded:
        manifest["source_duration"] = OLD
    source._save_manifest(directory, manifest)
    return manifest


def analysis_source(duration: float) -> Path:
    path, audio_first = source.download_analysis_source(
        URL, VIDEO_ID, task_id="task-1", duration=duration,
        download_full=lambda: pytest.fail("audio path should not fall back"),
    )
    assert audio_first
    return path


def test_audio_cached_from_a_longer_version_is_downloaded_again(runtime):
    _config, directory, downloads = runtime
    old_cache(directory)
    path = analysis_source(NEW)
    assert downloads == [("audio", path.name)]
    assert path.read_bytes() == b"current audio"
    # The old transcript and sections go with it; the new length is recorded.
    assert not path.with_suffix(".transcript_cache.json").exists()
    manifest = source._load_manifest(directory, VIDEO_ID)
    assert manifest["ranges"] == [] and manifest["source_duration"] == NEW
    assert not (directory / source._range_filename(0, 60)).exists()
    # The same version is reused on the next job.
    assert analysis_source(NEW + 1) == path and len(downloads) == 1


def test_audio_cached_before_lengths_were_recorded_is_measured(runtime, monkeypatch):
    _config, directory, downloads = runtime
    old_cache(directory, recorded=False)
    measured = {"value": OLD}
    monkeypatch.setattr("src.media.ffmpeg.ffprobe_duration", lambda _path: measured["value"])
    analysis_source(NEW)
    assert len(downloads) == 1

    # An unrecorded cache of the right length is kept and gets its length recorded.
    old_cache(directory, recorded=False)
    measured["value"] = NEW - 0.6
    path = analysis_source(NEW)
    assert len(downloads) == 1 and path.read_bytes() == b"old audio"
    assert source._load_manifest(directory, VIDEO_ID)["source_duration"] == NEW


def test_sections_without_a_recorded_length_are_downloaded_again(runtime):
    """Sections alone cannot be measured, so an unrecorded cache of them is not trusted."""
    _config, directory, downloads = runtime
    manifest = old_cache(directory, recorded=False)
    (directory / manifest["audio"]).unlink()
    manifest["audio"] = None
    source._save_manifest(directory, manifest)
    source.materialize_clip_source(
        URL, VIDEO_ID, [{"start_time": 10, "end_time": 40}], task_id="task-1", duration=NEW,
    )
    assert downloads == [("section", 10, 40)]


def test_sections_cached_from_a_longer_version_are_not_rendered(runtime):
    _config, directory, downloads = runtime
    old_cache(directory)
    render = source.materialize_clip_source(
        URL, VIDEO_ID, [{"start_time": 10, "end_time": 40}], task_id="task-1", duration=NEW,
    )
    assert downloads == [("section", 10, 40)]
    assert render.read_bytes() == b"current section"


def test_full_file_cached_from_a_longer_version_is_not_returned(runtime):
    _config, directory, _downloads = runtime
    old_cache(directory, full=True)
    assert source.cached_full_media(VIDEO_ID, None, NEW) is None
    assert not (directory / "full-1080.mp4").exists()
    old_cache(directory, full=True)
    assert source.cached_full_media(VIDEO_ID, None, OLD) == directory / "full-1080.mp4"


def test_without_a_known_length_the_cache_is_used_as_stored(runtime):
    _config, directory, downloads = runtime
    old_cache(directory)
    path, _ = source.download_analysis_source(
        URL, VIDEO_ID, duration=None, download_full=lambda: pytest.fail("unexpected"),
    )
    assert path.read_bytes() == b"old audio" and downloads == []


def test_merge_ignores_a_stored_manifest_of_another_version():
    local = {"audio_policy": source.AUDIO_POLICY, "audio": None, "full": None,
             "ranges": [], "source_duration": NEW}
    stale = {"audio_policy": source.AUDIO_POLICY, "audio": "audio.m4a", "full": "full-1080.mp4",
             "full_height": 1080, "ranges": [{"start": 900, "end": 960, "file": "r.mp4"}],
             "source_duration": OLD}
    assert source._merge_manifests(local, stale) == local
    # A stored manifest from before lengths were recorded is not trusted either.
    unrecorded = {key: value for key, value in stale.items() if key != "source_duration"}
    assert source._merge_manifests(local, unrecorded) == local
    # The same version (within the tolerance) still merges.
    same = {**stale, "source_duration": NEW + 1}
    assert source._merge_manifests(local, same)["audio"] == "audio.m4a"
    # A machine with nothing cached adopts the stored manifest and its length.
    empty = {"audio_policy": source.AUDIO_POLICY, "ranges": []}
    merged = source._merge_manifests(empty, same)
    assert merged["source_duration"] == NEW + 1 and merged["ranges"] == same["ranges"]


def test_bucket_copy_of_the_old_version_is_not_restored(runtime, monkeypatch, tmp_path):
    config, directory, downloads = runtime
    client = MemoryS3()
    config.media_storage = "s3"
    config.media_s3_bucket = "private-media"
    storage = MediaStorage(config, client)
    monkeypatch.setattr(source, "_storage", lambda: storage)
    monkeypatch.setattr("src.sync_db.advisory_lock", lambda *args, **kwargs: nullcontext())
    # Another machine stored the old version.
    other = tmp_path / "other" / "youtube-cache" / VIDEO_ID
    other.mkdir(parents=True)
    old = old_cache(other)
    for name in source._manifest_files(old) + [source.MANIFEST_NAME]:
        storage.upload_file(other / name, storage.key(source.CACHE_DIRNAME, VIDEO_ID, name))

    path = analysis_source(NEW)
    assert downloads == [("audio", path.name)] and path.read_bytes() == b"current audio"
    # The stored manifest now describes the current version only.
    stored = json.loads(client.objects[f"media/youtube-cache/{VIDEO_ID}/manifest.json"])
    assert stored["source_duration"] == NEW and stored["ranges"] == []
    assert stored["audio"] == path.name
