"""A refused section playlist must not cost the job its working proxy route.

Production: a regional audio download recovered through another country, then
the section's video playlist returned 404 (yt-dlp had already marked that HLS
format broken). The opaque error skipped the full-file proxy, and every retry
ended on the paid and direct providers YouTube was blocking.
"""

import copy
import io
from contextlib import contextmanager
from pathlib import Path

import pytest
from yt_dlp.networking import Response
from yt_dlp.networking.exceptions import HTTPError, ProxyError

from src import youtube_proxy as proxy
from src import youtube_utils
from src.config import Config, set_config_override
from tests.unit.test_youtube_audio_fallback import FORMATS, URL, VIDEO_ID
from tests.unit.test_youtube_geo_retry import geo_error

SIGNED = "https://manifest.googlevideo.com/api/manifest/hls_playlist/sig/private-signature"
SECRET_PROXY = "http://user-session-abc:super-secret-password@gw.example:823"


def http_error(status: int) -> HTTPError:
    return HTTPError(Response(io.BytesIO(b""), url=SIGNED, headers={}, status=status))


def playlist_client(monkeypatch, failure: BaseException):
    """yt-dlp stand-in whose playlist request fails with ``failure``."""
    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def urlopen(self, _request):
            raise failure

    monkeypatch.setattr("yt_dlp.YoutubeDL", lambda _options: Client())


def untimed_hls(fmt_id: str = "96") -> dict:
    return {"format_id": fmt_id, "protocol": "m3u8_native", "url": SIGNED}


# Format selection


def test_section_pickers_skip_formats_yt_dlp_could_not_download():
    broken_video = {
        "format_id": "616", "protocol": "m3u8_native", "vcodec": "vp09", "acodec": "none",
        "height": 1080, "tbr": 9000, "ext": "mp4", "__working": False, "__needs_testing": True,
    }
    untested_video = dict(broken_video, format_id="270", vcodec="avc1", tbr=4000)
    untested_video.pop("__working")
    broken_audio = {
        "format_id": "234", "protocol": "m3u8_native", "vcodec": "none", "acodec": "mp4a",
        "abr": 192, "ext": "mp4", "__working": False,
    }
    working_audio = dict(broken_audio, format_id="233", abr=48)
    working_audio.pop("__working")
    formats = [broken_video, untested_video, broken_audio, working_audio]

    assert proxy.pick_video_format(formats)["format_id"] == "270"
    assert proxy.pick_audio_format(formats, modest=False)["format_id"] == "233"
    assert proxy.pick_hls_audio_format(formats)["format_id"] == "233"
    assert proxy.pick_video_format([broken_video]) is None


# Playlist error classification


@pytest.mark.parametrize("status", [403, 404, 410])
def test_refused_playlist_is_section_only_with_sanitized_detail(monkeypatch, status):
    playlist_client(monkeypatch, http_error(status))

    with pytest.raises(proxy.HlsPlaylistUnavailable) as raised:
        proxy.expand_hls_format(untimed_hls(), {"proxy": SECRET_PROXY}, proxy.ByteBudget(1000), 12)

    error = raised.value
    assert isinstance(error, proxy.PartialDownloadUnavailable)
    assert f"HTTP Error {status}" in str(error)
    assert "private-signature" not in str(error) and "super-secret" not in str(error)
    # The raw yt-dlp error carries the signed URL; it must not ride along.
    assert error.__cause__ is None and error.__suppress_context__
    assert not proxy.is_transient_proxy_error(error)


@pytest.mark.parametrize("failure", [
    ProxyError(f"Unable to connect to proxy {SECRET_PROXY}"),
    TimeoutError("The read operation timed out"),
    ConnectionResetError("Connection reset by peer"),
    http_error(503),
])
def test_playlist_transport_failure_is_retryable_not_section_only(monkeypatch, failure):
    playlist_client(monkeypatch, failure)

    with pytest.raises(proxy.YouTubeDownloadError) as raised:
        proxy.expand_hls_format(untimed_hls(), {}, proxy.ByteBudget(1000), 12)

    error = raised.value
    assert not isinstance(error, proxy.PartialDownloadUnavailable)
    assert proxy.is_transient_proxy_error(error)
    assert "super-secret" not in str(error)
    assert error.__cause__ is None


@pytest.mark.parametrize("failure", [http_error(407), http_error(429), RuntimeError("unexpected")])
def test_proxy_refusal_throttling_and_unknown_errors_are_not_section_only(monkeypatch, failure):
    playlist_client(monkeypatch, failure)

    with pytest.raises(proxy.YouTubeDownloadError) as raised:
        proxy.expand_hls_format(untimed_hls(), {}, proxy.ByteBudget(1000), 12)

    assert not isinstance(raised.value, proxy.PartialDownloadUnavailable)
    assert str(raised.value).startswith("Could not fetch the HLS media playlist through the proxy: ")


# The download chain: section failure, then the full-file proxy


@pytest.fixture
def chain(tmp_path, monkeypatch):
    """Real retry, section and provider chain over a fake YouTube and proxy."""
    config = Config()
    config.temp_dir = str(tmp_path)
    config.youtube_proxy_url = "http://user-country-{country}-session-{session}:secret@proxy.example:8000"
    config.youtube_proxy_country = "de"
    config.youtube_pot_provider_url = ""
    config.youtube_proxy_timeout_seconds = 300
    config.youtube_download_provider = "yt_dlp"
    config.apify_api_token = ""
    config.rapidapi_youtube_api_key = ""
    set_config_override(config)
    state = {
        "metadata": [], "playlists": [], "full": [], "independent": [], "clock": 100.0,
        "errors": [geo_error()], "playlist_errors": [http_error(404)], "full_bytes": b"video",
    }
    monkeypatch.setattr(proxy.time, "monotonic", lambda: state["clock"])
    formats = copy.deepcopy(FORMATS)
    # The video rendition needs its playlist fetched, as on YouTube.
    formats[-1].pop("fragments")

    def metadata(_url, options):
        state["metadata"].append(dict(options))
        if state["errors"]:
            raise state["errors"].pop(0)
        proxy._metadata_ready(options)
        return {"duration": 20, "formats": copy.deepcopy(formats)}

    @contextmanager
    def client(options):
        class Client:
            def urlopen(self, _request):
                state["playlists"].append(options["proxy"])
                if state["playlist_errors"]:
                    raise state["playlist_errors"].pop(0)
                raise AssertionError("no playlist should succeed in these tests")

            def download(self, urls):
                metadata(urls[0], options)
                state["full"].append(dict(options))
                output = Path(options["outtmpl"].replace("%(ext)s", "mp4"))
                output.write_bytes(state["full_bytes"])
                options["progress_hooks"][0]({
                    "status": "finished", "filename": str(output),
                    "downloaded_bytes": len(state["full_bytes"]),
                })
        yield Client()

    def no_media(*_args, **_kwargs):
        raise AssertionError("the section never reaches media transfer")

    def direct(*_args, **_kwargs):
        state["independent"].append("yt_dlp")
        return None

    monkeypatch.setattr(proxy, "extract_video_info", metadata)
    monkeypatch.setattr(proxy, "_proxy_client", client)
    monkeypatch.setattr(proxy, "native_download_format", no_media)
    monkeypatch.setattr(youtube_utils, "_download_youtube_video_with_ytdlp", direct)

    def section(budget):
        return proxy.download_section_file(
            URL, VIDEO_ID, 0, 10, tmp_path / "section.mp4", task_id="task12345678", budget=budget,
        )

    def full(budget, *, skip_proxy):
        return youtube_utils.download_youtube_video(
            URL, 3, "task12345678", None, budget=budget, skip_proxy=skip_proxy,
        )

    yield config, state, section, full
    set_config_override(None)


def test_refused_playlist_then_full_proxy_succeeds_on_recovered_region(chain):
    _, state, section, full = chain
    budget = proxy.ByteBudget(10_000)

    with pytest.raises(proxy.HlsPlaylistUnavailable, match="HTTP Error 404"):
        section(budget)
    # Region recovery, then one playlist request: a 404 is not retried.
    assert len(state["metadata"]) == 2 and len(state["playlists"]) == 1
    recovered = state["metadata"][-1]["proxy"]
    assert "country-us-" in recovered

    path = full(budget, skip_proxy=False)

    assert path.read_bytes() == b"video"
    assert [item["proxy"] for item in state["full"]] == [recovered]
    # The full download reuses the route: no new region detour.
    assert state["metadata"][-1]["proxy"] == recovered and len(state["metadata"]) == 3
    assert budget.snapshot() == len(b"video")
    assert state["independent"] == []


def test_full_proxy_after_section_failure_stays_within_job_bounds(chain):
    _, state, section, full = chain
    budget = proxy.ByteBudget(4)
    with pytest.raises(proxy.HlsPlaylistUnavailable):
        section(budget)

    with pytest.raises(proxy.DownloadBudgetExceeded):
        full(budget, skip_proxy=False)

    # Same job budget, and one attempt with the normal 300s + 60s ceiling.
    assert len(state["full"]) == 1 and state["independent"] == []
    assert state["full"][0]["_youtube_deadline"] <= 100 + 300 + 60


def test_proxy_wide_playlist_failure_retries_once_then_skips_full_proxy(chain):
    _, state, section, full = chain
    state["errors"] = []
    state["playlist_errors"] = [ProxyError("Tunnel connection failed: 502"), ProxyError("Tunnel connection failed: 502")]

    with pytest.raises(proxy.HlsPlaylistTransportError) as raised:
        section(proxy.ByteBudget(10_000))

    # One new-session retry, bounded by the extra 60 seconds.
    first, retry = state["metadata"]
    assert first["proxy"].split("session-")[1] != retry["proxy"].split("session-")[1]
    assert retry["_youtube_deadline"] == 160
    assert "Tunnel connection failed" in str(raised.value)
    assert "secret@" not in str(raised.value)

    # The caller then bypasses the proxy, so only independent providers run.
    assert full(proxy.ByteBudget(10_000), skip_proxy=True) is None
    assert state["full"] == [] and state["independent"] == ["yt_dlp"]
