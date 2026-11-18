"""Whole-VOD stream downloads stay on yt-dlp's native HLS downloader.

yt-dlp hands live-marked HLS, and manifests its native downloader can't read,
to ffmpeg. Network ffmpeg is disabled for stream downloads, so those inputs
used to fail on every provider and then on the paid actors. Real yt-dlp and a
loopback HTTP server; no platform or paid traffic.
"""
from __future__ import annotations

import shutil
import subprocess
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from yt_dlp.downloader.external import FFmpegFD

from src import stream_source as subject

TWITCH_VOD = "https://www.twitch.tv/videos/123456"
DURATION = 12.0
SEGMENT = 2


@pytest.fixture(scope="module")
def vod(tmp_path_factory):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is required to build the HLS fixture")
    root = tmp_path_factory.mktemp("hls-native")
    subprocess.run(
        ["ffmpeg", "-y", "-nostdin",
         "-f", "lavfi", "-i", f"testsrc=size=160x90:rate=25:duration={DURATION}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={DURATION}",
         "-c:v", "libx264", "-g", "25", "-keyint_min", "25", "-sc_threshold", "0",
         "-preset", "ultrafast", "-c:a", "aac", "-shortest",
         "-f", "hls", "-hls_time", str(SEGMENT), "-hls_playlist_type", "vod",
         "-hls_segment_filename", str(root / "seg%03d.ts"), str(root / "index.m3u8")],
        check=True, capture_output=True,
    )
    playlist = (root / "index.m3u8").read_text()
    assert "#EXT-X-ENDLIST" in playlist
    # A broadcast still being recorded: the playlist grows and has no end tag.
    (root / "live.m3u8").write_text(
        playlist.replace("#EXT-X-PLAYLIST-TYPE:VOD", "#EXT-X-PLAYLIST-TYPE:EVENT").replace("#EXT-X-ENDLIST\n", "")
    )
    # Encryption the native downloader can't decrypt; yt-dlp delegates it to ffmpeg.
    (root / "sample-aes.m3u8").write_text(
        playlist.replace(
            "#EXT-X-TARGETDURATION", '#EXT-X-KEY:METHOD=SAMPLE-AES,URI="key.bin"\n#EXT-X-TARGETDURATION', 1,
        )
    )

    requests: list[str] = []

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append(self.path)
            return super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(root)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield SimpleNamespace(root=root, base=f"http://127.0.0.1:{server.server_port}", requests=requests)
    server.shutdown()
    thread.join()
    server.server_close()


@pytest.fixture
def env(monkeypatch, tmp_path, vod):
    config = SimpleNamespace(
        temp_dir=str(tmp_path),
        # Direct, then the paid Twitch actor: a rejected VOD must never reach it.
        twitch_download_providers="direct,apify_twitch",
        kick_download_providers="direct",
        apify_stream_enabled=True,
        apify_api_token="test-token",
        apify_stream_timeout_seconds=60,
        youtube_proxy_url="",
        youtube_proxy_country="",
        youtube_max_height=1080,
        youtube_download_max_bytes=2_000_000_000,
        youtube_section_padding_seconds=0.0,
        youtube_proxy_timeout_seconds=300,
        stream_download_max_seconds=2400,
    )
    state = SimpleNamespace(playlist="index.m3u8", live=False)
    actors: list[str] = []
    ffmpeg_calls: list[str] = []

    def extract(url, provider, *, session, deadline=None):
        info = {
            "id": "123456", "title": "VOD", "duration": DURATION,
            "extractor": "twitch:vod", "extractor_key": "TwitchVod",
            "webpage_url": TWITCH_VOD,
            "formats": [
                {"format_id": "1080p60", "url": f"{vod.base}/{state.playlist}", "protocol": "m3u8_native",
                 "ext": "mp4", "height": 90, "width": 160, "vcodec": "avc1.64002A", "acodec": "mp4a.40.2"},
            ],
        }
        if state.live:
            info |= {"is_live": True, "live_status": "is_live"}
        return info

    def actor_media(url, provider, *args, **kwargs):
        actors.append(provider)
        raise RuntimeError("paid actor attempted")

    def call_ffmpeg(self, tmpfilename, info_dict):
        ffmpeg_calls.append(info_dict.get("url"))
        raise RuntimeError("network ffmpeg attempted")

    monkeypatch.setattr(subject, "get_config", lambda: config)
    monkeypatch.setattr(subject, "_extract", extract)
    monkeypatch.setattr(subject, "_actor_media", actor_media)
    monkeypatch.setattr(FFmpegFD, "_call_downloader", call_ffmpeg)
    vod.requests.clear()
    subject._operations.clear()
    yield SimpleNamespace(vod=vod, state=state, actors=actors, ffmpeg_calls=ffmpeg_calls)
    subject._operations.clear()


def _segments_requested(vod) -> list[str]:
    return sorted(path for path in vod.requests if path.endswith(".ts"))


def test_an_ordinary_vod_downloads_natively(env):
    path = subject.download_stream_video(TWITCH_VOD, DURATION, "task1", operation="job")
    assert subject._probe(path) == pytest.approx(DURATION, abs=0.5)
    assert len(_segments_requested(env.vod)) == DURATION / SEGMENT
    assert env.ffmpeg_calls == [] and env.actors == []


def test_a_finished_vod_still_marked_live_downloads_natively(env):
    env.state.live = True
    path = subject.download_stream_video(TWITCH_VOD, DURATION, "task1", operation="job")
    assert subject._probe(path) == pytest.approx(DURATION, abs=0.5)
    assert env.ffmpeg_calls == [] and env.actors == []


def test_a_stream_still_being_recorded_is_refused_without_fallbacks(env):
    env.state.live, env.state.playlist = True, "live.m3u8"
    with pytest.raises(subject.StreamSourceError) as raised:
        subject.download_stream_audio(TWITCH_VOD, DURATION, "task1", operation="job")
    assert str(raised.value) == subject.LIVE_VOD_MESSAGE
    assert env.ffmpeg_calls == [] and env.actors == []
    assert _segments_requested(env.vod) == []


def test_a_manifest_the_native_downloader_cannot_read_is_refused_without_fallbacks(env):
    env.state.playlist = "sample-aes.m3u8"
    with pytest.raises(subject.StreamSourceError) as raised:
        subject.download_stream_video(TWITCH_VOD, DURATION, "task1", operation="job")
    assert str(raised.value) == subject.UNSUPPORTED_HLS_MESSAGE
    assert env.ffmpeg_calls == [] and env.actors == []
    assert _segments_requested(env.vod) == []
