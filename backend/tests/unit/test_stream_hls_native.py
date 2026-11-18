"""Whole-VOD stream downloads stay on yt-dlp's native HLS downloader.

yt-dlp hands live-marked HLS, and manifests its native downloader can't read,
to ffmpeg. Network ffmpeg is disabled for stream downloads, so those inputs
used to fail on every provider and then on the paid actors. Now another
rendition or provider is tried, and only when none is readable natively is the
source refused, before the paid actors. Real yt-dlp and a loopback HTTP server;
no platform or paid traffic.
"""
from __future__ import annotations

import shutil
import subprocess
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from Cryptodome.Cipher import AES
from Cryptodome.Util.Padding import pad
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
    # The same key with URI before METHOD: attribute order is free (RFC 8216).
    (root / "sample-aes-uri-first.m3u8").write_text(
        playlist.replace(
            "#EXT-X-TARGETDURATION", '#EXT-X-KEY:URI="key.bin",METHOD=SAMPLE-AES\n#EXT-X-TARGETDURATION', 1,
        )
    )
    # A finished recording: VOD playlists never change (RFC 8216), end tag or not.
    (root / "vod-no-endlist.m3u8").write_text(playlist.replace("#EXT-X-ENDLIST\n", ""))
    # An HTTP 200 that is not a playlist, e.g. a challenge page.
    (root / "challenge.m3u8").write_text("<html><body>Checking your browser</body></html>\n")
    # A master playlist handed over as the selected URL.
    (root / "master-sample-aes.m3u8").write_text(
        '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=200000,RESOLUTION=160x90\nsample-aes.m3u8\n'
    )
    # AES-128 is decrypted by the native downloader.
    key = b"0123456789abcdef"
    (root / "key.bin").write_bytes(key)
    aes = playlist.replace(
        "#EXT-X-TARGETDURATION", '#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n#EXT-X-TARGETDURATION', 1,
    )
    for index, segment in enumerate(sorted(root.glob("seg*.ts"))):
        encrypted = root / f"aes-{segment.name}"
        cipher = AES.new(key, AES.MODE_CBC, iv=index.to_bytes(16, "big"))
        encrypted.write_bytes(cipher.encrypt(pad(segment.read_bytes(), 16)))
        aes = aes.replace(segment.name, encrypted.name)
    (root / "aes128.m3u8").write_text(aes)

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


def _extractions(monkeypatch, *playlists):
    """Each extraction serves the next playlist; the last one repeats."""
    original = subject._extract
    served: list[str] = []

    def extract(*args, **kwargs):
        info = original(*args, **kwargs)
        playlist = playlists[min(len(served), len(playlists) - 1)]
        served.append(playlist)
        info["formats"][0]["url"] = info["formats"][0]["url"].rsplit("/", 1)[0] + "/" + playlist
        return info

    monkeypatch.setattr(subject, "_extract", extract)
    return served


def test_an_unreadable_rendition_on_one_provider_falls_through_to_the_next(env, monkeypatch):
    subject.get_config().twitch_download_providers = "direct,direct_retry,apify_twitch"
    served = _extractions(monkeypatch, "sample-aes.m3u8", "index.m3u8")
    path = subject.download_stream_video(TWITCH_VOD, DURATION, "task1", operation="job")
    assert subject._probe(path) == pytest.approx(DURATION, abs=0.5)
    assert served == ["sample-aes.m3u8", "index.m3u8"]
    assert env.ffmpeg_calls == [] and env.actors == []


def test_an_unreadable_best_rendition_falls_back_to_a_readable_one(env, monkeypatch):
    original = subject._extract

    def extract(*args, **kwargs):
        info = original(*args, **kwargs)
        best = dict(info["formats"][0], format_id="1080p60", height=1080, width=1920,
                    url=f"{env.vod.base}/sample-aes.m3u8")
        readable = dict(info["formats"][0], format_id="720p60", height=720, width=1280)
        info["formats"] = [readable, best]
        return info

    monkeypatch.setattr(subject, "_extract", extract)
    path = subject.download_stream_video(TWITCH_VOD, DURATION, "task1", operation="job")
    assert subject._probe(path) == pytest.approx(DURATION, abs=0.5)
    assert "/sample-aes.m3u8" in env.vod.requests
    assert env.ffmpeg_calls == [] and env.actors == []


def test_a_master_playlist_is_checked_through_its_media_rendition(env):
    env.state.playlist = "master-sample-aes.m3u8"
    with pytest.raises(subject.StreamSourceError) as raised:
        subject.download_stream_video(TWITCH_VOD, DURATION, "task1", operation="job")
    assert str(raised.value) == subject.UNSUPPORTED_HLS_MESSAGE
    assert "/sample-aes.m3u8" in env.vod.requests
    assert env.ffmpeg_calls == [] and env.actors == []


def test_a_vod_playlist_without_an_end_tag_is_finished_even_when_marked_live(env):
    env.state.live, env.state.playlist = True, "vod-no-endlist.m3u8"
    path = subject.download_stream_video(TWITCH_VOD, DURATION, "task1", operation="job")
    assert subject._probe(path) == pytest.approx(DURATION, abs=0.5)
    assert env.ffmpeg_calls == [] and env.actors == []


def test_a_finished_event_playlist_not_marked_live_downloads(env):
    env.state.playlist = "live.m3u8"
    path = subject.download_stream_video(TWITCH_VOD, DURATION, "task1", operation="job")
    assert subject._probe(path) == pytest.approx(DURATION, abs=0.5)
    assert env.ffmpeg_calls == [] and env.actors == []


def test_aes128_is_decrypted_natively(env):
    env.state.playlist = "aes128.m3u8"
    path = subject.download_stream_video(TWITCH_VOD, DURATION, "task1", operation="job")
    assert subject._probe(path) == pytest.approx(DURATION, abs=0.5)
    assert env.ffmpeg_calls == [] and env.actors == []


def test_a_page_that_is_not_a_playlist_is_not_taken_for_a_live_stream(env, monkeypatch):
    subject.get_config().twitch_download_providers = "direct,direct_retry,apify_twitch"
    env.state.live = True
    served = _extractions(monkeypatch, "challenge.m3u8", "vod-no-endlist.m3u8")
    path = subject.download_stream_video(TWITCH_VOD, DURATION, "task1", operation="job")
    assert subject._probe(path) == pytest.approx(DURATION, abs=0.5)
    assert served[0] == "challenge.m3u8"
    assert env.ffmpeg_calls == [] and env.actors == []


def test_a_page_that_is_not_a_playlist_keeps_the_ordinary_fallbacks(env):
    env.state.live, env.state.playlist = True, "challenge.m3u8"
    with pytest.raises(subject.StreamSourceError) as raised:
        subject.download_stream_video(TWITCH_VOD, DURATION, "task1", operation="job")
    assert str(raised.value) not in {subject.LIVE_VOD_MESSAGE, subject.UNSUPPORTED_HLS_MESSAGE}
    assert env.actors == ["apify_twitch"]
    assert env.ffmpeg_calls == []


def test_refusals_talk_about_the_video_and_keep_the_upload_route():
    for message in (subject.LIVE_VOD_MESSAGE, subject.UNSUPPORTED_HLS_MESSAGE):
        assert "VOD" not in message and "video" in message
        assert "upload the video file" in message.lower()
        assert "encrypt" not in message.lower()


def test_kick_spends_no_actor_once_every_native_route_refused(env):
    subject.get_config().kick_download_providers = "proxy,direct_retry,apify_kick_hls,apify_kick_mp4"
    env.state.playlist = "sample-aes.m3u8"
    with pytest.raises(subject.StreamSourceError) as raised:
        subject.download_stream_video(
            "https://kick.com/demo/videos/12345678-1234-1234-1234-123456789abc", DURATION, "task1", operation="job",
        )
    assert str(raised.value) == subject.UNSUPPORTED_HLS_MESSAGE
    assert env.ffmpeg_calls == [] and env.actors == []
    assert _segments_requested(env.vod) == []


def test_sample_aes_is_refused_whatever_the_key_attribute_order(env):
    env.state.playlist = "sample-aes-uri-first.m3u8"
    with pytest.raises(subject.StreamSourceError) as raised:
        subject.download_stream_video(TWITCH_VOD, DURATION, "task1", operation="job")
    assert str(raised.value) == subject.UNSUPPORTED_HLS_MESSAGE
    assert env.ffmpeg_calls == [] and env.actors == []
    assert _segments_requested(env.vod) == []


def _precheck_fails_once(monkeypatch, failure):
    """The pre-check's playlist read fails; the real download's reads don't."""
    original = subject._read_url
    calls: list[str] = []

    def read(ydl, url, headers, destination=None):
        calls.append(url)
        if len(calls) == 1:
            return failure(url)
        return original(ydl, url, headers, destination)

    monkeypatch.setattr(subject, "_read_url", read)
    return calls


def _count_extractions(monkeypatch):
    original = subject._extract
    providers: list[str] = []

    def extract(url, provider, **kwargs):
        providers.append(provider)
        return original(url, provider, **kwargs)

    monkeypatch.setattr(subject, "_extract", extract)
    return providers


@pytest.mark.parametrize("status", [403, 503])
def test_a_failed_precheck_fetch_is_unknown_and_downloads_natively(env, monkeypatch, status):
    def fail(url):
        raise HTTPError(url, status, "playlist fetch failed", {}, None)

    calls = _precheck_fails_once(monkeypatch, fail)
    providers = _count_extractions(monkeypatch)
    path = subject.download_stream_video(TWITCH_VOD, DURATION, "task1", operation="job")
    assert subject._probe(path) == pytest.approx(DURATION, abs=0.5)
    assert len(calls) == 1 and providers == ["direct"]
    assert len(_segments_requested(env.vod)) == DURATION / SEGMENT
    assert env.ffmpeg_calls == [] and env.actors == []


def test_a_blocked_precheck_fetch_is_unknown_and_downloads_natively(env, monkeypatch):
    calls = _precheck_fails_once(monkeypatch, lambda url: (b"<html>Access denied</html>", url))
    providers = _count_extractions(monkeypatch)
    path = subject.download_stream_video(TWITCH_VOD, DURATION, "task1", operation="job")
    assert subject._probe(path) == pytest.approx(DURATION, abs=0.5)
    assert len(calls) == 1 and providers == ["direct"]
    assert env.ffmpeg_calls == [] and env.actors == []


def test_a_blocked_precheck_never_hands_a_live_marked_rendition_to_ffmpeg(env, monkeypatch):
    env.state.live = True
    calls = _precheck_fails_once(monkeypatch, lambda url: (b"<html>Access denied</html>", url))
    path = subject.download_stream_video(TWITCH_VOD, DURATION, "task1", operation="job")
    assert subject._probe(path) == pytest.approx(DURATION, abs=0.5)
    assert len(calls) == 1
    assert env.ffmpeg_calls == [] and env.actors == []
