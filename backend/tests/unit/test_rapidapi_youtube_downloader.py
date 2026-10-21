"""API Router contracts and failure cases captured from the large-video trial."""
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from src import rapidapi_youtube_downloader as router
from src.budget_apify_downloader import BudgetApifyError

# The real hosts live in private configuration; tests use stand-ins.
API = "api.example.test"
RELAY = "relay.example.test"


@pytest.fixture(autouse=True)
def router_hosts(monkeypatch):
    monkeypatch.setenv("RAPIDAPI_YOUTUBE_HOST", API)
    monkeypatch.setenv("RAPIDAPI_YOUTUBE_RELAY_HOST", RELAY)


def row(kind, *, size=100, duration=120, tags="", height=1080, bitrate=100):
    params = {"clen": str(size), "dur": str(duration)}
    if tags:
        params["xtags"] = tags
    source = "https://rr1.googlevideo.com/videoplayback?" + urlencode(params)
    return {"type": kind, "height": height, "width": 1920, "fps": 60, "bitrate": bitrate,
            "mimeType": 'video/mp4; codecs="avc1.64002A"' if kind == "video" else "audio/webm",
            "download_url": f"https://{RELAY}/?" + urlencode({"url": source})}


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    monkeypatch.setattr("src.budget_apify_downloader.socket.getaddrinfo",
                        lambda *a, **k: [(None, None, None, None, ("8.8.8.8", 443))])


def test_selects_original_audio_instead_of_first_automatic_dub():
    dubbed = row("audio", tags="acont=dubbed-auto:lang=en-US", bitrate=9999)
    original = row("audio", tags="acont=original:lang=es-US")
    video, audio = router._select({"medias": [row("video"), dubbed, original]}, "1080")
    assert audio["row"] is original
    assert video["duration"] == 120  # Uses signed URL dur, not rounded provider minutes.


def test_rejects_audio_when_only_automatic_dubs_exist():
    with pytest.raises(router.RapidAPIDownloadError, match="original audio"):
        router._select({"medias": [row("video"), row("audio", tags="acont=dubbed-auto:lang=en")]}, "1080")


def test_exact_10gb_combined_allowed_but_one_extra_byte_rejected():
    payload = {"medias": [row("video", size=router.MAX_BYTES - 100), row("audio")]}
    video, audio = router._select(payload, "1080")
    assert video["size"] + audio["size"] == router.MAX_BYTES
    payload["medias"][1] = row("audio", size=101)
    with pytest.raises(router.RapidAPIDownloadError, match="10 GB"):
        router._select(payload, "1080")


@pytest.mark.parametrize("url", [
    "https://attacker.example/?url=https://rr1.googlevideo.com/videoplayback",
    f"https://{RELAY}/?" + urlencode({"url": "https://127.0.0.1/videoplayback?clen=1&dur=1"}),
    f"https://{RELAY}/?" + urlencode({"url": "https://googlevideo.com.evil.example/videoplayback?clen=1&dur=1"}),
    f"https://{RELAY}/?" + urlencode({"url": "https://rr1.googlevideo.com/videoplayback?clen=1&clen=2&dur=1"}),
])
def test_rejects_unapproved_or_ambiguous_addresses(url):
    with pytest.raises(router.RapidAPIDownloadError):
        router._track({"download_url": url})


def test_ranges_are_encoded_in_inner_source_query():
    track = router._track(row("video"))
    relay = urlsplit(router._range_url(track, 10, 19))
    inner = parse_qs(relay.query)["url"][0]
    assert parse_qs(urlsplit(inner).query)["range"] == ["10-19"]


class Response:
    def __init__(self, chunks=(), status=200, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self.chunks = chunks
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def iter_content(self, *args): return iter(self.chunks)
    def close(self): pass


class Session:
    def __init__(self, response, calls): self.response, self.calls = response, calls
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response
    post = get


@pytest.mark.parametrize("chunks,headers", [([b"ab"], {}), ([b"abcdef"], {}),
                                         ([b"abcd"], {"Content-Length": "999"})])
def test_rejects_incomplete_or_oversized_ranges(tmp_path, monkeypatch, chunks, headers):
    calls = []
    monkeypatch.setattr(router.requests, "Session", lambda: Session(Response(chunks, headers=headers), calls))
    with pytest.raises(router.RapidAPIDownloadError):
        router._download_track({"source": router._track(row("video"))["source"], "size": 4},
                               tmp_path / "part", time.monotonic() + 10, chunk_size=4, workers=1)
    assert calls[0][1]["allow_redirects"] is False
    assert "headers" not in calls[0][1]  # No extraction credentials on media requests.


def test_extraction_does_not_retry_or_forward_credentials_on_redirect(monkeypatch):
    calls = []
    monkeypatch.setattr(router.requests, "Session", lambda: Session(Response(status=302), calls))
    with pytest.raises(router.RapidAPIDownloadError, match="HTTP 302"):
        router._extract("abcdefghijk", "test-key", time.monotonic() + 10)
    assert len(calls) == 1
    assert calls[0][1]["allow_redirects"] is False


def test_rejects_wrong_video_identity(monkeypatch):
    calls = []
    response = Response([b'{"thumbnail":"https://i.ytimg.com/vi/other_video/maxresdefault.jpg"}'])
    monkeypatch.setattr(router.requests, "Session", lambda: Session(response, calls))
    with pytest.raises(router.RapidAPIDownloadError, match="identity"):
        router._extract("abcdefghijk", "test-key", time.monotonic() + 10)


@pytest.mark.parametrize("failure", ["transfer", "verify", "mux", "size"])
def test_failed_delivery_cleans_all_staging_files(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(router, "_extract", lambda *a: {"medias": [row("video"), row("audio")]})
    def download(track, target, *args, **kwargs):
        target.write_bytes(b"media")
        if failure == "transfer": raise OSError("secret-bearing URL")
    monkeypatch.setattr(router, "_download_track", download)
    def mux(command, **kwargs):
        if failure == "mux": raise router.subprocess.CalledProcessError(1, command)
        Path(command[-1]).write_bytes(b"muxed")
    from pathlib import Path
    monkeypatch.setattr(router.subprocess, "run", mux)
    def verify(*a):
        if failure == "verify": raise BudgetApifyError("corrupt")
    monkeypatch.setattr(router, "_verify_media", verify)
    if failure == "size":
        monkeypatch.setattr(router, "MAX_BYTES", 201)
        # Selection is below the cap; mux output is above it.
        original_mux = mux
        def oversized(command, **kwargs):
            original_mux(command, **kwargs)
            Path(command[-1]).write_bytes(b"x" * 202)
        monkeypatch.setattr(router.subprocess, "run", oversized)
    with pytest.raises(router.RapidAPIDownloadError) as error:
        router.download_video_via_rapidapi("abcdefghijk", tmp_path, "test-key")
    assert "secret-bearing" not in str(error.value)
    assert list(tmp_path.iterdir()) == []


def test_parallel_ranges_reconstruct_the_complete_file(tmp_path, monkeypatch):
    content = b"0123456789abcdef"
    calls = []
    class RangedSession(Session):
        def get(self, url, **kwargs):
            inner = parse_qs(urlsplit(url).query)["url"][0]
            start, end = map(int, parse_qs(urlsplit(inner).query)["range"][0].split("-"))
            calls.append((start, end))
            return Response([content[start:end + 1]], headers={"Content-Length": str(end - start + 1)})
    monkeypatch.setattr(router.requests, "Session", lambda: RangedSession(None, calls))
    track = router._track(row("video", size=len(content)))
    target = tmp_path / "complete"
    router._download_track(track, target, time.monotonic() + 10, chunk_size=5, workers=3)
    assert target.read_bytes() == content
    assert sorted(calls) == [(0, 4), (5, 9), (10, 14), (15, 15)]



def test_google_redirect_stays_inside_relay_and_preserves_requested_range():
    track = router._track(row("video", size=100))
    source = "https://rr2.googlevideo.com/videoplayback?clen=100&dur=120&signature=signed"
    replies = iter([Response(status=302, headers={"Location": source}), Response([b"bytes"])])
    calls = []
    class RedirectSession:
        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return next(replies)
    result = router._open_range(RedirectSession(), track, 10, 14, time.monotonic() + 10)
    assert result.status_code == 200
    assert len(calls) == 2
    assert all(urlsplit(url).hostname == RELAY for url, _ in calls)
    inner = parse_qs(urlsplit(calls[-1][0]).query)["url"][0]
    assert urlsplit(inner).hostname == "rr2.googlevideo.com"
    assert parse_qs(urlsplit(inner).query)["range"] == ["10-14"]
    assert all(kwargs["allow_redirects"] is False and "headers" not in kwargs for _, kwargs in calls)


@pytest.mark.parametrize("location", ["https://evil.example/video", "http://rr2.googlevideo.com/videoplayback",
                                    "https://rr2.googlevideo.com/videoplayback?clen=999&dur=120"])
def test_unsafe_or_different_media_redirect_is_rejected(location):
    session = Session(Response(status=302, headers={"Location": location}), [])
    with pytest.raises((router.RapidAPIDownloadError, BudgetApifyError)):
        router._open_range(session, router._track(row("video")), 0, 9, time.monotonic() + 10)


def test_media_redirect_loop_is_bounded():
    source = "https://rr2.googlevideo.com/videoplayback?clen=100&dur=120"
    calls = []
    session = Session(Response(status=302, headers={"Location": source}), calls)
    with pytest.raises(router.RapidAPIDownloadError, match="same source"):
        router._open_range(session, router._track(row("video")), 0, 9, time.monotonic() + 10)
    assert len(calls) == 2


def test_relay_returning_original_source_fails_without_repeating_request():
    track = router._track(row('video'))
    calls = []
    session = Session(Response(status=302, headers={'Location': track['source']}), calls)
    with pytest.raises(router.RapidAPIDownloadError, match='same source'):
        router._open_range(session, track, 0, 9, time.monotonic() + 10)
    assert len(calls) == 1


def test_missing_redirect_destination_is_reported_separately():
    session = Session(Response(status=302), [])
    with pytest.raises(router.RapidAPIDownloadError, match='without a destination'):
        router._open_range(session, router._track(row('video')), 0, 9, time.monotonic() + 10)


def test_missing_host_configuration_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("RAPIDAPI_YOUTUBE_RELAY_HOST")
    with pytest.raises(router.RapidAPIDownloadError, match="not configured"):
        router.download_video_via_rapidapi("abcdefghijk", tmp_path, "key")
