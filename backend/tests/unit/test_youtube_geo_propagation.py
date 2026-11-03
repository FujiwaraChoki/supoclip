"""A known country restriction survives exhausted independent download paths."""

from unittest.mock import Mock

import pytest
from yt_dlp.utils import DownloadError, GeoRestrictedError

from src import youtube_source, youtube_utils
from src.config import Config, set_config_override
from src.services import video_service
from src.youtube_proxy import DownloadBudgetExceeded, YouTubeGeoRestrictedError

URL = "https://www.youtube.com/watch?v=abcdefghijk"
VIDEO_ID = "abcdefghijk"


@pytest.fixture
def config(tmp_path):
    config = Config()
    config.temp_dir = str(tmp_path)
    config.youtube_proxy_url = "http://user-country-{country}:password@proxy.invalid:80"
    config.youtube_proxy_country = "de"
    config.youtube_source_cache = False
    config.rapidapi_youtube_api_key = "test"
    config.apify_api_token = "test"
    set_config_override(config)
    yield config
    set_config_override(None)


def test_full_chain_preserves_geo_after_other_providers_fail(config, monkeypatch):
    proxy = Mock(side_effect=YouTubeGeoRestrictedError())
    rapid = Mock(side_effect=RuntimeError("no supported quality"))
    apify = Mock(side_effect=RuntimeError("HTTP 403"))
    direct = Mock(return_value=None)
    monkeypatch.setattr(youtube_utils, "download_full_with_proxy", proxy)
    monkeypatch.setattr(youtube_utils, "download_video_via_rapidapi", rapid)
    monkeypatch.setattr(youtube_utils, "download_youtube_video_with_apify", apify)
    monkeypatch.setattr(youtube_utils, "_download_youtube_video_with_ytdlp", direct)
    errors = []

    with pytest.raises(YouTubeGeoRestrictedError, match="Upload the video file"):
        youtube_utils.download_youtube_video(URL, quality="720", errors=errors)

    assert proxy.call_count == rapid.call_count == apify.call_count == direct.call_count == 1
    assert str(YouTubeGeoRestrictedError()) in errors


def test_geo_error_does_not_prevent_successful_independent_provider(config, monkeypatch, tmp_path):
    source = tmp_path / "provider" / "source.mp4"
    source.parent.mkdir()
    source.write_bytes(b"video")
    monkeypatch.setattr(youtube_utils, "download_full_with_proxy", Mock(side_effect=YouTubeGeoRestrictedError()))
    monkeypatch.setattr(youtube_utils, "download_video_via_rapidapi", Mock(return_value=source))
    monkeypatch.setattr(youtube_utils, "_get_local_video_dimensions", lambda _path: (1280, 720))

    assert youtube_utils.download_youtube_video(URL, quality="720") == source


def test_direct_download_does_not_retry_same_country(config, monkeypatch):
    error = GeoRestrictedError("The uploader has not made this video available in your country", countries=["US"])
    client = Mock()
    client.download.side_effect = DownloadError(str(error), exc_info=(type(error), error, None))
    factory = Mock()
    factory.return_value.__enter__ = Mock(return_value=client)
    factory.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(youtube_utils.yt_dlp, "YoutubeDL", factory)
    sleep = Mock()
    monkeypatch.setattr(youtube_utils.time, "sleep", sleep)

    with pytest.raises(YouTubeGeoRestrictedError):
        youtube_utils._download_youtube_video_with_ytdlp(URL)

    client.download.assert_called_once()
    sleep.assert_not_called()


def test_metadata_geo_reason_survives_later_metadata_provider_error(config, monkeypatch):
    config.youtube_metadata_provider = "yt_dlp"
    monkeypatch.setattr(youtube_utils, "_fetch_video_info_with_ytdlp", Mock(side_effect=GeoRestrictedError("unavailable", countries=["US"])))
    monkeypatch.setattr(youtube_utils, "_fetch_video_info_with_youtube_data_api", Mock(side_effect=RuntimeError("metadata API unavailable")))
    with pytest.raises(YouTubeGeoRestrictedError):
        youtube_utils.get_youtube_video_info(URL)


def test_direct_download_succeeds_when_proxy_metadata_is_geo_blocked(config, monkeypatch, tmp_path):
    config.rapidapi_youtube_api_key = None
    config.apify_api_token = None
    blocked_metadata = Mock(side_effect=YouTubeGeoRestrictedError())
    monkeypatch.setattr(youtube_utils, "get_youtube_video_info", blocked_metadata)
    monkeypatch.setattr(youtube_utils, "download_full_with_proxy", Mock(side_effect=YouTubeGeoRestrictedError()))
    monkeypatch.setattr(youtube_utils, "_get_local_video_dimensions", lambda _: (1280, 720))
    downloaded = []
    source = tmp_path / f"{VIDEO_ID}.mp4"

    class DirectClient:
        def __init__(self, options):
            assert not options.get("proxy")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def download(self, urls):
            downloaded.extend(urls)
            source.write_bytes(b"complete video")

    monkeypatch.setattr(youtube_utils.yt_dlp, "YoutubeDL", DirectClient)

    assert youtube_utils.download_youtube_video(URL, quality="720") == source
    assert downloaded == [URL]
    blocked_metadata.assert_not_called()


@pytest.mark.asyncio
async def test_metadata_geo_restriction_is_actionable_before_task_creation(config, monkeypatch):
    from fastapi import HTTPException
    from src.api.routes.tasks import _preflight_youtube_source

    config.youtube_metadata_provider = "yt_dlp"
    monkeypatch.setattr(youtube_utils, "_fetch_video_info_with_ytdlp", Mock(side_effect=YouTubeGeoRestrictedError()))
    monkeypatch.setattr(youtube_utils, "_fetch_video_info_with_youtube_data_api", Mock(side_effect=RuntimeError("metadata API unavailable")))
    with pytest.raises(HTTPException) as result:
        await _preflight_youtube_source(URL, 3600)
    assert result.value.status_code == 422
    assert result.value.detail["code"] == "YOUTUBE_GEO_RESTRICTED"
    assert "Upload the video file" in result.value.detail["message"]
    assert "No minutes were used" in result.value.detail["message"]


def test_metadata_preflight_recovers_allowed_region_without_data_api(config, monkeypatch):
    config.youtube_metadata_provider = "yt_dlp"
    config.youtube_data_api_key = None
    attempts = []

    class Client:
        def __init__(self, options):
            self.options = options
            attempts.append(options["proxy"])

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def urlopen(self, request):
            raise AssertionError("No network expected")

        def extract_info(self, url, download=False):
            assert download is False
            assert self.options["skip_download"] is True
            if "country-de:" in self.options["proxy"]:
                geo = GeoRestrictedError("Not available", countries=["US"])
                raise DownloadError(str(geo), exc_info=(type(geo), geo, None))
            assert "country-us:" in self.options["proxy"]
            return {"id": VIDEO_ID, "title": "Title", "duration": 600}

    monkeypatch.setattr(youtube_utils.yt_dlp, "YoutubeDL", Client)
    info = youtube_utils.get_youtube_video_info(URL)
    assert info["duration"] == 600
    assert len(attempts) == 2
    assert config.youtube_proxy_country == "de"


def test_audio_geo_survives_full_fallback_failure(monkeypatch):
    monkeypatch.setattr(youtube_source, "download_audio_for_job", Mock(side_effect=YouTubeGeoRestrictedError()))
    full = Mock(return_value=None)
    with pytest.raises(YouTubeGeoRestrictedError):
        youtube_source.download_analysis_source(URL, VIDEO_ID, download_full=full)
    full.assert_called_once()


def test_audio_geo_allows_successful_full_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(youtube_source, "download_audio_for_job", Mock(side_effect=YouTubeGeoRestrictedError()))
    full = tmp_path / "source.mp4"
    assert youtube_source.download_analysis_source(URL, VIDEO_ID, download_full=lambda: full) == (full, False)


def test_audio_geo_does_not_hide_byte_budget_failure(monkeypatch):
    monkeypatch.setattr(youtube_source, "download_audio_for_job", Mock(side_effect=YouTubeGeoRestrictedError()))
    with pytest.raises(DownloadBudgetExceeded):
        youtube_source.download_analysis_source(
            URL, VIDEO_ID, download_full=Mock(side_effect=DownloadBudgetExceeded("limit")),
        )


@pytest.mark.asyncio
async def test_section_geo_survives_full_fallback_failure(config, monkeypatch):
    monkeypatch.setattr(video_service, "materialize_clip_source", Mock(side_effect=YouTubeGeoRestrictedError()))
    full = Mock(return_value=None)
    monkeypatch.setattr(video_service, "resolve_full_youtube_download", full)
    with pytest.raises(YouTubeGeoRestrictedError):
        await video_service.VideoService.download_video(URL, ranges=[(0, 10)])
    assert full.call_args.kwargs["skip_proxy"] is True


@pytest.mark.parametrize("failure", [OSError("disk full"), TypeError("programming error"), RuntimeError("storage unavailable")])
def test_audio_fallback_exceptions_are_not_relabelled_geo(monkeypatch, failure):
    monkeypatch.setattr(youtube_source, "download_audio_for_job", Mock(side_effect=YouTubeGeoRestrictedError()))
    full = Mock(side_effect=failure)
    with pytest.raises(type(failure)) as result:
        youtube_source.download_analysis_source(URL, VIDEO_ID, download_full=full)
    assert result.value is failure
    assert failure.__context__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [OSError("disk full"), TypeError("programming error"), RuntimeError("storage unavailable")])
async def test_section_fallback_exceptions_are_not_relabelled_geo(config, monkeypatch, failure):
    monkeypatch.setattr(video_service, "materialize_clip_source", Mock(side_effect=YouTubeGeoRestrictedError()))
    monkeypatch.setattr(video_service, "resolve_full_youtube_download", Mock(side_effect=failure))
    with pytest.raises(type(failure)) as result:
        await video_service.VideoService.download_video(URL, ranges=[(0, 10)])
    assert result.value is failure
    assert failure.__context__ is None


def test_metadata_deadline_reserves_time_for_independent_api(config, monkeypatch):
    config.youtube_metadata_provider = "yt_dlp"
    config.youtube_data_api_key = "test"
    now = [100.0]
    monkeypatch.setattr(youtube_utils.time, "monotonic", lambda: now[0])
    deadlines = []

    def primary(*args, deadline=None, **kwargs):
        deadlines.append(deadline)
        now[0] = deadline
        raise TimeoutError("proxy timeout")

    def fallback(*args, deadline=None, **kwargs):
        deadlines.append(deadline)
        return {"duration": 600}

    monkeypatch.setattr(youtube_utils, "_fetch_video_info_with_ytdlp", primary)
    monkeypatch.setattr(youtube_utils, "_fetch_video_info_with_youtube_data_api", fallback)
    assert youtube_utils.get_youtube_video_info(URL, deadline=130)["duration"] == 600
    assert deadlines == [115, 130]


def test_metadata_deadline_does_not_start_provider_after_expiry(config, monkeypatch):
    monkeypatch.setattr(youtube_utils.time, "monotonic", lambda: 130.0)
    proxy = Mock()
    api = Mock()
    monkeypatch.setattr(youtube_utils, "_fetch_video_info_with_ytdlp", proxy)
    monkeypatch.setattr(youtube_utils, "_fetch_video_info_with_youtube_data_api", api)
    with pytest.raises(TimeoutError):
        youtube_utils.get_youtube_video_info(URL, deadline=130)
    proxy.assert_not_called()
    api.assert_not_called()
