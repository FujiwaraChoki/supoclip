"""Selected sections of Twitch/Kick HLS VODs download only the fragments they need.

Real yt-dlp, real ffmpeg and a loopback HTTP server; no platform or paid traffic.
"""
from __future__ import annotations

import shutil
import subprocess
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import stream_source as subject
from src.youtube_source import load_time_map

TWITCH_VOD = "https://www.twitch.tv/videos/123456"
DURATION = 30.0
SEGMENT = 2


def make_config(tmp_path, **overrides):
    values = dict(
        temp_dir=str(tmp_path),
        twitch_download_providers="direct",
        kick_download_providers="direct",
        apify_stream_enabled=False,
        apify_api_token="",
        apify_stream_timeout_seconds=60,
        youtube_proxy_url="",
        youtube_proxy_country="",
        youtube_max_height=1080,
        youtube_download_max_bytes=2_000_000_000,
        youtube_section_padding_seconds=0.0,
        youtube_proxy_timeout_seconds=300,
        stream_download_max_seconds=2400,
    )
    return SimpleNamespace(**(values | overrides))


@pytest.fixture(scope="module")
def vod(tmp_path_factory):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is required for HLS section tests")
    root = tmp_path_factory.mktemp("hls-vod")
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
    (root / "encrypted.m3u8").write_text(
        playlist.replace("#EXT-X-TARGETDURATION", '#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n#EXT-X-TARGETDURATION', 1)
    )
    lines = playlist.splitlines()
    seg5 = next(i for i, line in enumerate(lines) if line == "seg005.ts")
    (root / "discontinuity.m3u8").write_text("\n".join(lines[: seg5 - 1] + ["#EXT-X-DISCONTINUITY"] + lines[seg5 - 1:]) + "\n")

    fmp4 = root / "fmp4"
    fmp4.mkdir()
    subprocess.run(
        ["ffmpeg", "-y", "-nostdin",
         "-f", "lavfi", "-i", f"testsrc=size=160x90:rate=25:duration={DURATION}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={DURATION}",
         "-c:v", "libx264", "-g", "25", "-keyint_min", "25", "-sc_threshold", "0",
         "-preset", "ultrafast", "-c:a", "aac", "-shortest",
         "-f", "hls", "-hls_time", str(SEGMENT), "-hls_playlist_type", "vod", "-hls_segment_type", "fmp4",
         "-hls_fmp4_init_filename", "init.mp4", "-hls_segment_filename", str(fmp4 / "frag%03d.m4s"),
         str(fmp4 / "index.m3u8")],
        check=True, capture_output=True,
    )
    real = root / "real"
    real.mkdir()
    for segment in root.glob("seg*.ts"):
        shutil.copy(segment, real / segment.name)
    shutil.copy(root / "index.m3u8", real / "index.m3u8")

    requests: list[str] = []

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append(self.path)
            if self.path == "/moved.m3u8":
                self.send_response(302)
                self.send_header("Location", "/real/index.m3u8")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return None
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
    config = make_config(tmp_path)
    full_downloads: list[tuple] = []
    state = SimpleNamespace(playlist="index.m3u8", duration=DURATION, separate=False)

    def extract(url, provider, *, session, deadline=None):
        separate = [
            {"format_id": "video_only", "url": f"{vod.base}/{state.playlist}", "protocol": "m3u8_native",
             "ext": "mp4", "height": 90, "width": 160, "vcodec": "avc1.64002A", "acodec": "none", "tbr": 9000},
        ] if state.separate else []
        return {
            "id": "123456", "title": "VOD", "duration": state.duration, "extractor": "twitch:vod",
            "formats": separate + [
                {"format_id": "audio_only", "url": f"{vod.base}/{state.playlist}", "protocol": "m3u8_native",
                 "ext": "mp4", "vcodec": "none", "acodec": "mp4a.40.2"},
                {"format_id": "1080p60", "url": f"{vod.base}/{state.playlist}", "protocol": "m3u8_native",
                 "ext": "mp4", "height": 90, "width": 160, "vcodec": "avc1.64002A", "acodec": "mp4a.40.2"},
            ],
        }

    def download_url(url, provider, template, *, audio, ranges, session):
        full_downloads.append((provider, audio))
        raise RuntimeError("full download attempted")

    def download_info(info, provider, template, *, audio, ranges, session):
        full_downloads.append((provider, audio))
        shutil.copy(vod.root / "seg000.ts", str(template).replace("%(ext)s", "ts"))
        raise RuntimeError("full download attempted")

    monkeypatch.setattr(subject, "get_config", lambda: config)
    monkeypatch.setattr(subject, "_extract", extract)
    monkeypatch.setattr(subject, "_download_info", download_info)
    monkeypatch.setattr(subject, "_download_url", download_url)
    vod.requests.clear()
    subject._operations.clear()
    yield SimpleNamespace(config=config, vod=vod, full_downloads=full_downloads, state=state)
    subject._operations.clear()


def _segments_requested(vod) -> list[str]:
    return sorted(path for path in vod.requests if path.endswith(".ts"))


def test_sections_fetch_only_their_fragments_and_keep_source_time(env):
    path = subject.download_stream_sections(
        TWITCH_VOD, [{"start_time": 5, "end_time": 9}, {"start_time": 21, "end_time": 23}],
        DURATION, "task1", operation="job",
    )
    assert env.full_downloads == []
    # 5-9s sits in segments 2-4 (4-10s); 21-23s in segments 10-11 (20-24s).
    assert _segments_requested(env.vod) == [f"/seg{i:03d}.ts" for i in (2, 3, 4, 10, 11)]
    assert subject._probe(path) == pytest.approx(6, abs=0.3)
    time_map = load_time_map(path)
    assert time_map.to_original(1.0) == pytest.approx(6.0, abs=0.05)
    assert time_map.to_original(5.0) == pytest.approx(22.0, abs=0.05)
    total = sum(p.stat().st_size for p in env.vod.root.glob("seg*.ts"))
    assert subject._operations[("task1", "job")].budget.total < total / 2


def test_overlapping_sections_share_fragments(env):
    subject.download_stream_sections(
        TWITCH_VOD, [{"start_time": 4.5, "end_time": 5.5}, {"start_time": 5.6, "end_time": 7}],
        DURATION, "task1", operation="job",
    )
    assert _segments_requested(env.vod) == ["/seg002.ts", "/seg003.ts"]


@pytest.mark.parametrize("playlist", ["encrypted.m3u8", "discontinuity.m3u8"])
def test_unsupported_playlists_fall_back_to_the_whole_file(env, playlist):
    env.state.playlist = playlist
    with pytest.raises(subject.StreamSourceError):
        subject.download_stream_sections(
            TWITCH_VOD, [{"start_time": 8, "end_time": 12}], DURATION, "task1", operation="job",
        )
    assert env.full_downloads == [("direct", False)]
    assert _segments_requested(env.vod) == []


def test_fragment_bytes_count_against_the_job_budget(env):
    env.config.youtube_download_max_bytes = 20_000
    with pytest.raises(subject.StreamSourceError, match="byte job limit"):
        subject.download_stream_sections(
            TWITCH_VOD, [{"start_time": 0, "end_time": 29}], DURATION, "task1", operation="job",
        )
    assert env.full_downloads == []


def test_segment_spans_merge_touching_ranges():
    segments = [subject._HlsSegment(i * 2.0, 2.0, f"s{i}", None, False) for i in range(10)]
    assert subject._segment_spans(segments, [(1, 3), (4.5, 5), (13, 14)]) == [(0, 2), (6, 6)]
    with pytest.raises(RuntimeError, match="does not cover"):
        subject._segment_spans(segments, [(25, 30)])


def test_parse_rejects_master_and_byte_range_playlists():
    with pytest.raises(subject._NoHlsSections):
        subject._parse_media_playlist("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nlow.m3u8\n", "http://x/")
    with pytest.raises(subject._NoHlsSections):
        subject._parse_media_playlist("#EXTM3U\n#EXTINF:2,\n#EXT-X-BYTERANGE:10@0\na.ts\n", "http://x/")
    parsed = subject._parse_media_playlist(
        '#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:2.5,\nchunks/a.m4s\n#EXTINF:2,\nb.m4s\n#EXT-X-ENDLIST\n',
        "http://cdn.test/vod/index.m3u8",
    )
    assert [(s.start, s.uri, s.init) for s in parsed] == [
        (0.0, "http://cdn.test/vod/chunks/a.m4s", "http://cdn.test/vod/init.mp4"),
        (2.5, "http://cdn.test/vod/b.m4s", "http://cdn.test/vod/init.mp4"),
    ]


def _stream_work_files(env) -> list[Path]:
    root = Path(env.config.temp_dir) / "stream-work"
    return [p for p in root.rglob("*") if p.is_file()] if root.exists() else []


def test_fragmented_mp4_spans_are_measured_on_their_own_timeline(env):
    env.state.playlist = "fmp4/index.m3u8"
    path = subject.download_stream_sections(
        TWITCH_VOD, [{"start_time": 10.5, "end_time": 13.5}], DURATION, "task1", operation="job",
    )
    assert env.full_downloads == []
    assert sorted(p for p in env.vod.requests if "fmp4/" in p and not p.endswith(".m3u8")) == [
        "/fmp4/frag005.m4s", "/fmp4/frag006.m4s", "/fmp4/init.mp4",
    ]
    assert subject._probe(path) == pytest.approx(3, abs=0.3)
    assert load_time_map(path).to_original(1.0) == pytest.approx(11.5, abs=0.05)


def test_relative_fragments_resolve_against_the_redirected_playlist(env):
    env.state.playlist = "moved.m3u8"
    path = subject.download_stream_sections(
        TWITCH_VOD, [{"start_time": 5, "end_time": 7}], DURATION, "task1", operation="job",
    )
    assert env.full_downloads == []
    assert _segments_requested(env.vod) == ["/real/seg002.ts", "/real/seg003.ts"]
    assert subject._probe(path) == pytest.approx(2, abs=0.3)


def test_a_combined_rendition_is_preferred_over_separate_tracks(env):
    env.state.separate = True
    subject.download_stream_sections(
        TWITCH_VOD, [{"start_time": 5, "end_time": 7}], DURATION, "task1", operation="job",
    )
    assert env.full_downloads == []
    assert _segments_requested(env.vod) == ["/seg002.ts", "/seg003.ts"]


def test_rounding_at_the_end_of_the_vod_is_clamped(env):
    env.state.duration = DURATION + 0.02
    path = subject.download_stream_sections(
        TWITCH_VOD, [{"start_time": 27, "end_time": DURATION + 0.02}], DURATION + 0.02, "task1", operation="job",
    )
    assert env.full_downloads == []
    assert subject._probe(path) == pytest.approx(3, abs=0.3)
    assert load_time_map(path).to_original(2.5) == pytest.approx(29.5, abs=0.05)


def test_a_playlist_much_shorter_than_the_video_is_a_coverage_error(env):
    env.state.duration = DURATION + 20
    with pytest.raises(subject.StreamSourceError):
        subject.download_stream_sections(
            TWITCH_VOD, [{"start_time": 40, "end_time": 45}], DURATION + 20, "task1", operation="job",
        )
    assert env.full_downloads == []
    assert _segments_requested(env.vod) == []


def test_failed_edit_downloads_leave_no_partial_files(env):
    env.config.youtube_download_max_bytes = 20_000
    with pytest.raises(subject.StreamSourceError, match="byte job limit"):
        subject.download_stream_sections(
            TWITCH_VOD, [{"start_time": 0, "end_time": 29}], DURATION, "task1", operation="edit-1",
        )
    assert _stream_work_files(env) == []


def test_local_assembly_does_not_count_as_a_stalled_download(env, monkeypatch):
    env.config.youtube_proxy_timeout_seconds = 1
    real = subject.assemble_timeline

    def slow_assembly(*args, **kwargs):
        import time
        time.sleep(1.5)
        return real(*args, **kwargs)

    monkeypatch.setattr(subject, "assemble_timeline", slow_assembly)
    path = subject.download_stream_sections(
        TWITCH_VOD, [{"start_time": 5, "end_time": 7}], DURATION, "task1", operation="job",
    )
    assert subject._probe(path) == pytest.approx(2, abs=0.3)


def test_apify_hls_media_fetches_only_selected_fragments(env, monkeypatch):
    env.config.twitch_download_providers = "apify_twitch"
    env.config.apify_stream_enabled = True
    env.config.apify_api_token = "placeholder-token"
    env.config.apify_twitch_actor = "twitch-actor"
    monkeypatch.setattr(subject, "_actor_items", lambda *a, **k: [{
        "recordType": "vod_metadata", "id": "123456", "lengthSeconds": DURATION,
        "vodHlsUrl": f"{env.vod.base}/index.m3u8",
    }])
    path = subject.download_stream_sections(
        TWITCH_VOD, [{"start_time": 5, "end_time": 7}], DURATION, "task1", operation="job",
    )
    assert env.full_downloads == []
    assert _segments_requested(env.vod) == ["/seg002.ts", "/seg003.ts"]
    assert load_time_map(path).to_original(1.0) == pytest.approx(6.0, abs=0.05)
