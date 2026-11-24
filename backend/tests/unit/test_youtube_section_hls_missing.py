"""A format list with no HLS at all is a failed playlist fetch, not an unsliceable video.

yt-dlp drops every m3u8 format when the HLS manifest request fails through the
proxy. The section download retries on a fresh session instead of falling
straight back to a full download, which a long source can exceed.
"""

import copy
from unittest.mock import Mock

import pytest

from src import youtube_proxy as proxy
from src.config import Config, set_config_override
from src.services import video_service
from tests.unit.test_youtube_audio_fallback import FORMATS, PROXY, URL, VIDEO_ID

# A progressive https video: what remains when the HLS formats are missing.
HTTPS_VIDEO = {
    "format_id": "137",
    "protocol": "https",
    "vcodec": "avc1",
    "acodec": "none",
    "height": 1080,
    "tbr": 4000,
    "ext": "mp4",
    "url": "https://rr1---sn-example.googlevideo.com/video137",
}
NO_HLS = [fmt for fmt in FORMATS if "m3u8" not in fmt["protocol"]] + [HTTPS_VIDEO]
WITH_HLS = FORMATS + [HTTPS_VIDEO]


@pytest.fixture
def downloads(tmp_path, monkeypatch):
    config = Config()
    config.temp_dir = str(tmp_path)
    config.youtube_proxy_url = PROXY
    config.youtube_proxy_country = ""
    config.youtube_pot_provider_url = ""
    config.youtube_proxy_timeout_seconds = 300
    config.youtube_download_max_bytes = 1000
    config.youtube_source_cache = False
    set_config_override(config)
    monkeypatch.delenv("YOUTUBE_PROXY_DEFAULT_PROVIDER", raising=False)
    state = {"metadata": [], "listings": [NO_HLS, WITH_HLS], "media": []}

    def metadata(_url, options):
        state["metadata"].append(dict(options))
        listing = state["listings"][min(len(state["metadata"]), len(state["listings"])) - 1]
        return {"duration": 20, "formats": copy.deepcopy(listing)}

    def download(options, _url, _id, fmt, stem, fragments=None):
        state["media"].append(fmt["format_id"])
        target = stem.with_suffix(".mp4")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x" * 5)
        return target

    monkeypatch.setattr(proxy, "extract_video_info", metadata)
    monkeypatch.setattr(proxy, "native_download_format", download)
    monkeypatch.setattr(proxy, "contain_raw_aac", lambda path: path)
    monkeypatch.setattr(proxy, "_mux_local_window", lambda _v, _a, dest, **kw: dest.write_bytes(b"mux"))

    def section(**_kwargs):
        return proxy.download_section_file(URL, VIDEO_ID, 0, 10, tmp_path / "section.mp4",
                                           task_id="task12345678")[0]

    yield state, section
    set_config_override(None)


def test_section_without_hls_formats_retries_on_a_fresh_session(downloads):
    state, section = downloads
    assert section().exists()
    first, retry = state["metadata"]
    assert first["proxy"] != retry["proxy"]
    assert "137" not in state["media"]


def test_section_still_without_hls_after_retry_keeps_the_full_file_fallback(downloads):
    state, section = downloads
    state["listings"] = [NO_HLS]
    with pytest.raises(proxy.PartialDownloadUnavailable) as raised:
        section()
    assert not isinstance(raised.value, proxy.HlsFormatsMissing)
    assert len(state["metadata"]) == 2


def test_hls_formats_without_timing_are_still_unsliceable_without_retry(downloads, monkeypatch):
    state, section = downloads
    untimed = [{k: v for k, v in fmt.items() if k != "fragments"} for fmt in FORMATS]
    state["listings"] = [untimed]
    # The playlist was fetched but carried no timing.
    monkeypatch.setattr(proxy, "expand_hls_format", lambda fmt, *_args: fmt)
    with pytest.raises(proxy.PartialDownloadUnavailable, match="no timed fragments"):
        section()
    assert len(state["metadata"]) == 1


def test_audio_section_without_hls_formats_retries_on_a_fresh_session(downloads, tmp_path, monkeypatch):
    state, _section = downloads
    timed = next(fmt for fmt in FORMATS if fmt.get("fragments"))["fragments"]
    monkeypatch.setattr(proxy, "expand_hls_format", lambda fmt, *_args: dict(fmt, fragments=timed))
    path, _start, _end = proxy.download_audio_section(
        URL, VIDEO_ID, 0, 10, tmp_path / "audio-section", task_id="task12345678",
    )
    assert path.exists()
    assert len(state["metadata"]) == 2


def test_section_retry_shares_the_job_byte_budget(downloads, tmp_path):
    state, _section = downloads
    state["listings"] = [NO_HLS]
    budget = proxy.ByteBudget(1000)
    with pytest.raises(proxy.PartialDownloadUnavailable):
        proxy.download_section_file(URL, VIDEO_ID, 0, 10, tmp_path / "section.mp4",
                                    task_id="task12345678", budget=budget)
    assert budget.limit == 1000
    assert len(state["metadata"]) == 2


@pytest.mark.asyncio
async def test_generation_does_not_fall_back_to_a_full_download_after_one_missing_playlist(
    downloads, monkeypatch,
):
    _state, section = downloads
    monkeypatch.setattr(video_service, "materialize_clip_source", lambda *a, **kw: section())
    full = Mock(return_value=None)
    monkeypatch.setattr(video_service, "resolve_full_youtube_download", full)
    path = await video_service.VideoService.download_video(URL, ranges=[(0, 10)])
    assert path is not None and path.name == "section.mp4"
    full.assert_not_called()


@pytest.mark.asyncio
async def test_generation_keeps_the_proxy_full_download_when_hls_stays_missing(downloads, monkeypatch):
    state, section = downloads
    state["listings"] = [NO_HLS]
    monkeypatch.setattr(video_service, "materialize_clip_source", lambda *a, **kw: section())
    full = Mock(return_value=None)
    monkeypatch.setattr(video_service, "resolve_full_youtube_download", full)
    await video_service.VideoService.download_video(URL, ranges=[(0, 10)])
    full.assert_called_once()
    assert full.call_args.kwargs["skip_proxy"] is False
