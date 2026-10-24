"""403 on proxied https audio retries HLS, then the section path, then the paid chain."""

import copy
import json
import logging
from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError

from src.config import Config, set_config_override
from src.observability import JsonLogFormatter
from src.youtube_proxy import (
    AudioDownloadForbidden,
    DownloadBudgetExceeded,
    is_forbidden_media_error,
    pick_hls_audio_format,
)
from src.youtube_source import download_analysis_source


VIDEO_ID = "abcdefghijk"
URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
PROXY = "http://user-session-{session}:super-secret-password@gw.example:823"
FORBIDDEN = (
    "ERROR: unable to download video data: HTTP Error 403: Forbidden "
    "url=https://rr1---sn-example.googlevideo.com/audio?sig=secret "
    "proxy=http://user:super-secret-password@gw.example:823"
)

_FRAGMENTS = [
    {"url": "https://rr1---sn-example.googlevideo.com/seg0", "duration": 10},
    {"url": "https://rr1---sn-example.googlevideo.com/seg1", "duration": 10},
]
FORMATS = [
    {
        "format_id": "140",
        "protocol": "https",
        "vcodec": "none",
        "acodec": "aac",
        "abr": 129,
        "ext": "m4a",
        "url": "https://rr1---sn-example.googlevideo.com/audio140",
    },
    {
        "format_id": "233",
        "protocol": "m3u8_native",
        "vcodec": "none",
        "acodec": None,
        "format_note": "Default, low",
        "ext": "mp4",
        "url": "https://rr1---sn-example.googlevideo.com/audio233.m3u8",
    },
    {
        "format_id": "234",
        "protocol": "m3u8_native",
        "vcodec": "none",
        "acodec": None,
        "format_note": "Default, high",
        "ext": "mp4",
        "url": "https://rr1---sn-example.googlevideo.com/audio234.m3u8",
        "fragments": _FRAGMENTS,
    },
    {
        "format_id": "96",
        "protocol": "m3u8_native",
        "vcodec": "avc1",
        "acodec": "mp4a.40.2",
        "height": 360,
        "tbr": 500,
        "ext": "mp4",
        "url": "https://rr1---sn-example.googlevideo.com/video96.m3u8",
        "fragments": _FRAGMENTS,
    },
]


class FakeYDL:
    """Stand-in for yt-dlp. Records format attempts and optionally raises 403."""

    mode = "hls_ok"
    downloaded: list[dict] = []

    def __init__(self, params):
        self.params = params

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def urlopen(self, request):
        raise AssertionError("section formats already carry timings; no playlist fetch expected")

    def extract_info(self, url, download=False):
        return {"id": VIDEO_ID, "duration": 20, "formats": copy.deepcopy(FORMATS)}

    def process_info(self, info):
        record = {
            "format_id": info.get("format_id"),
            "protocol": info.get("protocol"),
            "section": bool(info.get("fragments") or info.get("hls_media_playlist_data")),
        }
        FakeYDL.downloaded.append(record)
        mode = FakeYDL.mode
        if mode == "https_ok":
            self._write(info)
            return
        if mode == "botcheck":
            raise DownloadError("ERROR: Sign in to confirm you're not a bot")
        if info.get("format_id") == "140":
            raise DownloadError(FORBIDDEN)
        if mode == "hls_ok":
            self._write(info)
            return
        if not record["section"] or mode == "all_fail":
            raise DownloadError(FORBIDDEN)
        self._write(info)

    def _write(self, info):
        destination = Path(self.params["outtmpl"].replace("%(ext)s", info.get("ext") or "mp4"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"media")


def _config(tmp_path: Path) -> Config:
    config = Config()
    config.temp_dir = str(tmp_path)
    config.youtube_proxy_url = PROXY
    config.youtube_proxy_country = ""
    config.youtube_pot_provider_url = ""
    config.youtube_max_height = 1080
    config.youtube_source_cache = True
    config.youtube_download_max_bytes = 2_000_000_000
    config.media_storage = "local"
    return config


def _mux(video_path, audio_path, dest, **kwargs):
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"section")


@pytest.fixture
def fallback_run(tmp_path, monkeypatch):
    FakeYDL.downloaded = []
    monkeypatch.setattr("yt_dlp.YoutubeDL", FakeYDL)
    monkeypatch.setattr("src.youtube_proxy._mux_local_window", _mux)
    config = _config(tmp_path)
    set_config_override(config)
    calls = {"full": 0}

    def download_full():
        calls["full"] += 1
        target = tmp_path / "paid-chain.mp4"
        target.write_bytes(b"chain")
        return target

    def run(mode: str, duration: float | None = 20):
        FakeYDL.mode = mode
        FakeYDL.downloaded = []
        calls["full"] = 0
        return download_analysis_source(
            URL,
            VIDEO_ID,
            task_id="task-placeholder",
            duration=duration,
            download_full=download_full,
        ), calls

    try:
        yield run
    finally:
        set_config_override(None)


def _assert_no_secrets(caplog):
    text = caplog.text
    assert "super-secret-password" not in text
    assert "sig=secret" not in text
    assert "googlevideo.com" not in text


def test_forbidden_detector_ignores_budget_and_bot_check():
    assert is_forbidden_media_error(DownloadError(FORBIDDEN))
    assert is_forbidden_media_error(RuntimeError("unable to download video data"))
    assert not is_forbidden_media_error(DownloadError("ERROR: Sign in to confirm you're not a bot"))
    assert not is_forbidden_media_error(DownloadBudgetExceeded("over the cap"))
    assert not is_forbidden_media_error(AudioDownloadForbidden("HLS audio download failed"))


def test_hls_picker_uses_lowest_muxed_when_audio_only_hls_is_missing():
    formats = [
        {"format_id": "140", "protocol": "https", "vcodec": "none", "acodec": "aac",
         "abr": 128, "ext": "m4a"},
        {"format_id": "299", "protocol": "m3u8_native", "vcodec": "avc1", "acodec": "mp4a.40.2",
         "height": 1080, "tbr": 4000, "ext": "mp4"},
        {"format_id": "91", "protocol": "m3u8_native", "vcodec": "avc1", "acodec": "mp4a.40.2",
         "height": 144, "tbr": 100, "ext": "mp4"},
        {"format_id": "92", "protocol": "m3u8_native", "vcodec": "avc1", "acodec": "none",
         "height": 144, "tbr": 50, "ext": "mp4"},
    ]
    assert pick_hls_audio_format(formats)["format_id"] == "91"


def test_https_audio_success_does_not_retry(fallback_run, caplog):
    caplog.set_level(logging.INFO)
    (path, audio_first), calls = fallback_run("https_ok")
    assert audio_first is True
    assert path is not None and path.is_file()
    assert calls["full"] == 0
    assert [item["format_id"] for item in FakeYDL.downloaded] == ["140"]
    assert not any(getattr(record, "youtube_fallback", None) for record in caplog.records)
    assert any(
        getattr(record, "youtube_download_kind", None) == "audio"
        and record.youtube_outcome == "success"
        for record in caplog.records
    )
    _assert_no_secrets(caplog)


def test_https_audio_403_then_hls_succeeds(fallback_run, caplog):
    caplog.set_level(logging.INFO)
    (path, audio_first), calls = fallback_run("hls_ok")
    assert audio_first is True
    assert path is not None and path.is_file() and path.stat().st_size > 0
    assert calls["full"] == 0
    assert [(item["format_id"], item["section"]) for item in FakeYDL.downloaded] == [
        ("140", False),
        ("233", False),
    ]
    matched = [record for record in caplog.records if getattr(record, "youtube_fallback", None) == "hls_audio"]
    assert len(matched) == 1
    assert matched[0].youtube_outcome == "success"
    assert matched[0].youtube_download_kind == "audio_hls"
    payload = json.loads(JsonLogFormatter().format(matched[0]))
    assert payload["youtube_fallback"] == "hls_audio"
    assert payload["youtube_download_kind"] == "audio_hls"
    assert "super-secret-password" not in payload["message"]
    _assert_no_secrets(caplog)


def test_hls_failure_uses_section_path(fallback_run, caplog):
    caplog.set_level(logging.INFO)
    (path, audio_first), calls = fallback_run("section_ok")
    assert audio_first is False
    assert path is not None and path.is_file() and path.name.startswith("full")
    assert calls["full"] == 0
    attempts = [(item["format_id"], item["section"]) for item in FakeYDL.downloaded]
    assert attempts[:2] == [("140", False), ("233", False)]
    assert attempts[2:] == [("96", True), ("234", True)]
    matched = [record for record in caplog.records if getattr(record, "youtube_fallback", None) == "section"]
    assert len(matched) == 1
    assert matched[0].youtube_outcome == "success"
    assert matched[0].youtube_download_kind == "section"
    payload = json.loads(JsonLogFormatter().format(matched[0]))
    assert payload["youtube_fallback"] == "section"
    assert "googlevideo.com" not in payload["message"]
    _assert_no_secrets(caplog)


def test_hls_and_section_failure_calls_existing_chain(fallback_run, caplog):
    caplog.set_level(logging.INFO)
    (path, audio_first), calls = fallback_run("all_fail")
    assert audio_first is False
    assert path is not None and path.name == "paid-chain.mp4"
    assert calls["full"] == 1
    attempts = [(item["format_id"], item["section"]) for item in FakeYDL.downloaded]
    assert ("140", False) in attempts
    assert ("233", False) in attempts
    assert any(section for _format_id, section in attempts)
    assert not any(
        getattr(record, "youtube_fallback", None) == "hls_audio" and record.youtube_outcome == "success"
        for record in caplog.records
    )
    assert "falling back to a full file" in caplog.text
    _assert_no_secrets(caplog)


def test_section_fallback_is_skipped_without_a_duration(fallback_run, caplog):
    caplog.set_level(logging.INFO)
    (path, audio_first), calls = fallback_run("all_fail", duration=None)
    assert audio_first is False
    assert path is not None and path.name == "paid-chain.mp4"
    assert calls["full"] == 1
    assert [item["section"] for item in FakeYDL.downloaded] == [False, False]
    assert not any(getattr(record, "youtube_fallback", None) == "section" for record in caplog.records)
    _assert_no_secrets(caplog)


def test_non_forbidden_audio_error_skips_hls_and_section(fallback_run, caplog):
    caplog.set_level(logging.INFO)
    (path, audio_first), calls = fallback_run("botcheck")
    assert audio_first is False
    assert path is not None and path.name == "paid-chain.mp4"
    assert calls["full"] == 1
    assert [item["format_id"] for item in FakeYDL.downloaded] == ["140"]
    _assert_no_secrets(caplog)
