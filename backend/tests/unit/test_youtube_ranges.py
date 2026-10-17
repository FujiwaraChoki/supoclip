import pytest

from src.youtube_utils import get_youtube_video_id


@pytest.mark.parametrize("host", ["youtube.com", "www.youtube.com", "m.youtube.com"])
def test_live_url_extracts_video_id(host):
    url = f"https://{host}/live/abcdefghijk?t=60"
    assert get_youtube_video_id(url) == "abcdefghijk"


@pytest.mark.parametrize("suffix,expected", [
    ("", None), ("&t=600", (600, None)), ("&t=1h2m3s", (3723, None)),
    ("&start=60&end=120", (60, 120)), ("&end=120", (0, 120)),
    ("#t=10m", (600, None)), ("&t=60&start=120", (120, None)),
    ("&t=0", None), ("&start=0&end=60", (0, 60)),
])
def test_parse_requested_range(suffix, expected):
    from src.youtube_ranges import parse_youtube_range
    assert parse_youtube_range(f"https://youtube.com/watch?v=abcdefghijk{suffix}") == expected


@pytest.mark.parametrize("suffix", [
    "&t=-1", "&t=abc", "&t=", "&t=NaN", "&t=1.5", "&t=1mgarbage",
    "&start=60&end=60", "&start=120&end=60", "&end=0", "&t=1&t=2",
    "&start=" + "9" * 400,
])
def test_invalid_range_is_rejected(suffix):
    from src.youtube_ranges import parse_youtube_range
    with pytest.raises(ValueError):
        parse_youtube_range(f"https://youtube.com/watch?v=abcdefghijk{suffix}")


def test_range_duration_is_bounded_by_source():
    from src.youtube_ranges import youtube_range_duration
    assert youtube_range_duration("https://youtu.be/abcdefghijk?start=60&end=999", 180) == 120
    assert youtube_range_duration("https://youtu.be/abcdefghijk?t=60", 180) == 120
    assert youtube_range_duration("https://youtu.be/abcdefghijk", 180) == 180
    with pytest.raises(ValueError):
        youtube_range_duration("https://youtu.be/abcdefghijk?t=180", 180)


def test_cache_keys_separate_ranges_and_keep_full_video_compatibility():
    from src.youtube_ranges import youtube_cache_key
    url = "https://youtu.be/abcdefghijk"
    assert youtube_cache_key(url, "abcdefghijk") == "abcdefghijk"
    assert youtube_cache_key(url + "?t=60", "abcdefghijk") != "abcdefghijk"
    assert youtube_cache_key(url + "?t=60", "abcdefghijk") != youtube_cache_key(url + "?t=120", "abcdefghijk")
    assert youtube_cache_key(url + "?t=60", "abcdefghijk") == youtube_cache_key(url + "?start=1m", "abcdefghijk")


def test_ranged_download_passes_bounds_and_separate_output_to_ytdlp(tmp_path, monkeypatch):
    from src import youtube_utils as youtube
    from src.config import Config, set_config_override
    config = Config()
    config.temp_dir = str(tmp_path)
    captured = {}

    class Downloader:
        def __init__(self, options):
            captured.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def download(self, urls):
            from pathlib import Path
            Path(captured["outtmpl"].replace("%(ext)s", "mp4")).write_bytes(b"range")

    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", Downloader)
    monkeypatch.setattr(youtube, "get_youtube_video_info", lambda *a, **k: {"duration": 300, "title": "fixture"})
    monkeypatch.setattr(youtube, "_get_local_video_dimensions", lambda p: (640, 360))
    set_config_override(config)
    try:
        result = youtube._download_youtube_video_with_ytdlp("https://youtu.be/abcdefghijk?start=60&end=120")
        assert result is not None
        assert result.stem != "abcdefghijk"
        assert list(captured["download_ranges"]({"duration": 300}, None)) == [
            {"start_time": 60, "end_time": 120},
        ]
        assert captured["force_keyframes_at_cuts"] is True
    finally:
        set_config_override(None)


def test_range_download_never_falls_back_to_full_video_provider(monkeypatch):
    from src import youtube_utils as youtube
    from unittest.mock import Mock
    ranged = Mock(return_value=None)
    full = Mock(side_effect=AssertionError("Full video fallback must not run"))
    monkeypatch.setattr(youtube, "_download_youtube_video_with_ytdlp", ranged)
    monkeypatch.setattr(youtube, "download_youtube_video_with_apify", full)
    assert youtube.download_youtube_video("https://youtu.be/abcdefghijk?start=60&end=120") is None
    ranged.assert_called_once()
    full.assert_not_called()


@pytest.mark.asyncio
async def test_word_timings_are_restored_with_range_specific_key(tmp_path, monkeypatch):
    from src.services import video_service as service
    from unittest.mock import AsyncMock, Mock
    path = tmp_path / "abcdefghijk__range_60_120.mp4"
    path.write_bytes(b"range")
    storage = Mock()
    monkeypatch.setattr(service, "async_download_youtube_video", AsyncMock(return_value=path))
    monkeypatch.setattr(service, "MediaStorage", lambda: storage)
    await service.VideoService.download_video("https://youtu.be/abcdefghijk?start=60&end=120")
    storage.restore_youtube_metadata.assert_called_once_with(path.stem, path)


@pytest.mark.asyncio
async def test_worker_uses_range_duration_and_passes_cropped_media_to_transcriber(tmp_path, monkeypatch):
    from src.services import video_service as service
    from src.config import Config
    from unittest.mock import AsyncMock
    path = tmp_path / "range.mp4"
    path.write_bytes(b"range")
    config = Config()
    config.temp_dir = str(tmp_path)
    monkeypatch.setattr(service, "get_config", lambda: config)
    monkeypatch.setattr(service, "async_get_youtube_video_info", AsyncMock(return_value={"duration": 14400}))
    downloader = AsyncMock(return_value=path)
    transcriber = AsyncMock(side_effect=RuntimeError("transcription reached"))
    monkeypatch.setattr(service.VideoService, "download_video", downloader)
    monkeypatch.setattr(service.VideoService, "generate_transcript", transcriber)
    monkeypatch.setattr(service.VideoService, "_get_file_duration", lambda p: 60)
    url = "https://youtu.be/abcdefghijk?start=7200&end=7260"
    with pytest.raises(RuntimeError, match="transcription reached"):
        await service.VideoService.process_video_complete(url, "youtube", max_video_duration=90)
    transcriber.assert_awaited_once_with(path, processing_mode="fast", source_url=url)


def test_cleanup_preserves_other_range_media_and_cache(tmp_path, monkeypatch):
    from src.config import Config, set_config_override
    from src.services.task_service import TaskService
    config = Config()
    config.temp_dir = str(tmp_path)
    full = tmp_path / "abcdefghijk.mp4"
    full.write_bytes(b"full")
    selected = tmp_path / "abcdefghijk__range_60_120.mp4"
    selected.write_bytes(b"range")
    cache = selected.with_suffix(".transcript_cache.json")
    cache.write_text("{}")
    set_config_override(config)
    try:
        TaskService._cleanup_source_video(selected, "youtube", "https://youtu.be/abcdefghijk?start=60&end=120")
        assert not selected.exists()
        assert full.exists()
        assert cache.exists()
    finally:
        set_config_override(None)


def test_caption_provider_keeps_only_complete_cues_inside_requested_range(tmp_path, monkeypatch):
    from src.media import transcription
    from src.config import Config, set_config_override
    config = Config()
    config.temp_dir = str(tmp_path)
    cache = tmp_path / "abcdefghijk.transcript_cache.json"
    cache.write_text("{}")
    vtt = """WEBVTT

00:00:00.000 --> 00:00:02.000
Before the range

00:00:02.000 --> 00:00:04.000
Selected words

00:00:04.000 --> 00:00:06.000
After the range
"""

    class Downloader:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def download(self, urls):
            from pathlib import Path
            Path(self.options["outtmpl"] + ".en.vtt").write_text(vtt)

    import yt_dlp
    monkeypatch.setattr(yt_dlp, "YoutubeDL", Downloader)
    set_config_override(config)
    try:
        result = transcription.transcribe_with_youtube_captions("https://youtube.com/live/abcdefghijk?start=2&end=4")
        assert result == "Selected words"
        assert cache.exists()
    finally:
        set_config_override(None)


@pytest.mark.asyncio
async def test_media_reference_index_retains_range_metadata(monkeypatch):
    from src.workers.media_cleanup import reference_index
    from src.config import Config
    from src.media_storage import MediaStorage
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    row = SimpleNamespace(url="https://youtu.be/abcdefghijk?start=60&end=120", type="youtube")
    db = SimpleNamespace(execute=AsyncMock(side_effect=[[], [], [row]]))
    index = await reference_index(db, MediaStorage(Config()))
    assert "abcdefghijk__range_60_120" in index["videos"]


@pytest.mark.parametrize("suffix", [
    "mp4", "webm", "mkv", "m4a", "part", "transcription.mp3",
    "mp4.part", "f137.mp4.part", "mp4.part-Frag1",
])
def test_orphan_collector_finds_interrupted_range_downloads(tmp_path, suffix):
    from src.workers.media_cleanup import orphan_candidates
    from src.config import Config
    from src.media_storage import MediaStorage
    import os
    config = Config()
    config.temp_dir = str(tmp_path)
    config.media_storage_provider = "local"
    config.s3_bucket = ""
    path = tmp_path / f"abcdefghijk__range_60_120.{suffix}"
    path.write_bytes(b"interrupted")
    os.utime(path, (0, 0))
    assert (str(path), False) in list(orphan_candidates(MediaStorage(config)))


@pytest.mark.asyncio
async def test_worker_rejects_unavailable_range_metadata_before_download(monkeypatch):
    from src.services import video_service as service
    from src.config import Config
    from unittest.mock import AsyncMock
    monkeypatch.setattr(service, "get_config", lambda: Config())
    monkeypatch.setattr(service, "async_get_youtube_video_info", AsyncMock(return_value=None))
    download = AsyncMock()
    monkeypatch.setattr(service.VideoService, "download_video", download)
    with pytest.raises(ValueError, match="duration could not be verified"):
        await service.VideoService.process_video_complete("https://youtu.be/abcdefghijk?t=60", "youtube")
    download.assert_not_awaited()


def test_sql_cache_does_not_reuse_legacy_analysis_that_ignored_url_range():
    from src.services.task_service import TaskService
    from src.ai import TRANSCRIPT_ANALYSIS_CACHE_VERSION
    import hashlib
    for suffix in ("", "?t=60"):
        url = "https://youtu.be/abcdefghijk" + suffix
        legacy = hashlib.sha256(
            f"youtube|fast|{TRANSCRIPT_ANALYSIS_CACHE_VERSION}|{url}".encode()
        ).hexdigest()
        current = TaskService._build_cache_key(url, "youtube", "fast")
        assert (current != legacy) is bool(suffix)
