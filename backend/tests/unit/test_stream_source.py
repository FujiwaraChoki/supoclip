"""Twitch/Kick provider chain tests. Every provider is mocked: no network, no paid actors."""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import stream_source as subject
from src.youtube_source import load_time_map

TWITCH_VOD = "https://www.twitch.tv/videos/123456"
TWITCH_CLIP = "https://clips.twitch.tv/PlaceholderClip-abc"
KICK_ID = "12345678-1234-1234-1234-123456789abc"
KICK_VOD = f"https://kick.com/creator/videos/{KICK_ID}"
MEDIA_DURATION = 30.0


# ----------------------------------------------------------------- fixtures

def make_config(tmp_path=None, **overrides):
    values = dict(
        temp_dir=str(tmp_path or "."),
        twitch_download_providers="proxy,direct,apify_twitch",
        kick_download_providers="proxy,direct_retry,apify_kick_hls,apify_kick_mp4",
        apify_stream_enabled=True,
        apify_api_token="placeholder-token",
        apify_twitch_actor="twitch-actor",
        apify_kick_hls_actor="kick-hls-actor",
        apify_kick_mp4_actor="kick-mp4-actor",
        apify_kick_max_video_size_mb=2000,
        apify_stream_timeout_seconds=60,
        youtube_proxy_url="http://user-{session}:pw@proxy.test:8000",
        youtube_proxy_country="",
        youtube_max_height=1080,
        youtube_download_max_bytes=2_000_000_000,
        youtube_section_padding_seconds=0.0,
    )
    return SimpleNamespace(**(values | overrides))


@pytest.fixture(autouse=True)
def fresh_state():
    subject._operations.clear()
    yield
    subject._operations.clear()


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is required for stream media tests")
    root = tmp_path_factory.mktemp("stream-media")
    ts, mkv = root / "source.ts", root / "source.mkv"
    subprocess.run(
        ["ffmpeg", "-y", "-nostdin", "-f", "lavfi", "-i", f"testsrc=size=160x90:rate=25:duration={MEDIA_DURATION}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={MEDIA_DURATION}",
         "-c:v", "libx264", "-g", "25", "-preset", "ultrafast", "-c:a", "aac", "-shortest", "-f", "mpegts", str(ts)],
        check=True, capture_output=True,
    )
    subprocess.run(["ffmpeg", "-y", "-nostdin", "-i", str(ts), "-c", "copy", str(mkv)], check=True, capture_output=True)
    return SimpleNamespace(ts=ts, mkv=mkv)


def _cut(source: Path, dest: Path, start: float, end: float):
    subprocess.run(
        ["ffmpeg", "-y", "-nostdin", "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{end - start:.3f}",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(dest)],
        check=True, capture_output=True,
    )


class FakeYtdlp:
    """Stands in for yt-dlp on platform URLs (proxy/direct) and actor media URLs."""

    def __init__(self, media, *, duration=MEDIA_DURATION, failures=None, wrong_sections=False):
        self.media, self.duration, self.failures = media, duration, dict(failures or {})
        self.wrong_sections = wrong_sections
        self.calls: list[tuple] = []

    def _maybe_fail(self, key):
        failure = self.failures.get(key)
        if isinstance(failure, list):
            failure = failure.pop(0) if failure else None
        if failure:
            raise failure

    def extract(self, url, provider, *, session, deadline=None):
        self.calls.append(("extract", provider, url))
        if provider == "proxy" and not subject.get_config().youtube_proxy_url:
            raise subject.ProviderSkipped("proxy is not configured")
        self._maybe_fail(provider)
        return {"id": "x", "title": "Stream", "duration": self.duration}

    def _write(self, template, ranges):
        template = Path(template)
        if ranges:
            for index, (start, end) in enumerate(ranges, 1):
                dest = Path(str(template).replace("%(section_number)03d", f"{index:03d}").replace("%(ext)s", "mkv"))
                _cut(self.media.mkv, dest, start, end + (3 if self.wrong_sections else 0))
        else:
            shutil.copy(self.media.mkv, str(template).replace("%(ext)s", "mkv"))

    def download_info(self, info, provider, template, *, audio, ranges, session):
        self.calls.append(("download", provider, tuple(ranges or ())))
        self._maybe_fail(f"download:{provider}")
        self._write(template, ranges)

    def download_url(self, url, provider, template, *, audio, ranges, session):
        self.calls.append(("media", provider, url))
        if provider == "proxy" and not subject.get_config().youtube_proxy_url:
            raise subject.ProviderSkipped("proxy is not configured")
        self._maybe_fail(f"media:{provider}")
        self._write(template, ranges)

    def providers(self, kind):
        return [call[1] for call in self.calls if call[0] == kind]


class FakeActors:
    def __init__(self, results=None):
        self.results = dict(results or {})
        self.calls: list[str] = []

    def __call__(self, actor, run_input, charge, timeout_secs=None):
        self.calls.append(actor)
        result = self.results.get(actor, [])
        if isinstance(result, Exception):
            raise result
        return result


class FakeResponse:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, size):
        with open(self.path, "rb") as handle:
            while chunk := handle.read(size):
                yield chunk


@pytest.fixture
def env(monkeypatch, tmp_path, media):
    config = make_config(tmp_path)
    ytdlp = FakeYtdlp(media)
    actors = FakeActors()
    downloads: list[str] = []
    monkeypatch.setattr(subject, "get_config", lambda: config)
    monkeypatch.setattr(subject, "_extract", ytdlp.extract)
    monkeypatch.setattr(subject, "_download_info", ytdlp.download_info)
    monkeypatch.setattr(subject, "_download_url", ytdlp.download_url)
    monkeypatch.setattr(subject, "_actor_items", actors)
    monkeypatch.setattr(subject, "_DIRECT_RETRY_BACKOFF_SECONDS", 0)
    real_probe = subject._probe
    monkeypatch.setattr(subject, "_probe", lambda target, timeout=60: ytdlp.duration if str(target).startswith("https://") else real_probe(target))

    def fake_get(url, **_kwargs):
        downloads.append(url)
        return FakeResponse(media.ts)

    monkeypatch.setattr(subject.requests, "get", fake_get)
    return SimpleNamespace(config=config, ytdlp=ytdlp, actors=actors, downloads=downloads, root=tmp_path / "stream-work")


def kick_hls_item(**overrides):
    return {"type": "vod", "id": KICK_ID, "durationSec": MEDIA_DURATION, "hlsUrl": "https://media.test/kick.m3u8", "status": "ready"} | overrides


def parsebird_item(**overrides):
    return {"url": KICK_VOD, "duration": MEDIA_DURATION * 1000, "download_url": "https://media.test/kick.mp4", "status": "success"} | overrides


def twitch_item(**overrides):
    return {"recordType": "vod_metadata", "id": "123456", "lengthSeconds": MEDIA_DURATION, "vodHlsUrl": "https://media.test/twitch.m3u8"} | overrides


# ---------------------------------------------------------------- URL rules

@pytest.mark.parametrize(("url", "expected"), [
    (TWITCH_VOD, ("twitch", "vod", "123456")),
    (TWITCH_CLIP, ("twitch", "clip", "PlaceholderClip-abc")),
    ("https://www.twitch.tv/creator/clip/PlaceholderClip-abc", ("twitch", "clip", "PlaceholderClip-abc")),
    (KICK_VOD, ("kick", "vod", KICK_ID)),
    (f"https://kick.com/video/{KICK_ID}", ("kick", "vod", KICK_ID)),
    ("https://kick.com/creator/clips/clip_abc", ("kick", "clip", "clip_abc")),
    ("https://kick.com/creator?clip=clip_abc", ("kick", "clip", "clip_abc")),
])
def test_detect_stream_source(url, expected):
    assert subject.detect_stream_source(url) == expected


@pytest.mark.parametrize("url", ["https://twitch.tv/creator", "https://kick.com/creator"])
def test_live_channel_urls_are_rejected_as_live(url):
    with pytest.raises(ValueError, match="Live streams aren't supported"):
        subject.detect_stream_source(url)


@pytest.mark.parametrize(("url", "message"), [
    ("https://www.twitch.tv/creator/videos", "Unsupported Twitch URL"),
    ("https://www.twitch.tv/videos", "Unsupported Twitch URL"),
    ("https://www.twitch.tv/directory/category/chess", "Unsupported Twitch URL"),
    ("https://clips.twitch.tv/a/b", "Invalid Twitch clip URL"),
    ("https://kick.com/creator/about", "Unsupported Kick URL"),
    ("https://kick.com/video/not-a-uuid", "Unsupported Kick URL"),
    ("https://example.com/video.mp4", "Only YouTube, Twitch"),
])
def test_unsupported_urls_get_accurate_messages(url, message):
    with pytest.raises(ValueError, match=message):
        subject.detect_stream_source(url)


def test_provider_order_is_proxy_first_and_apify_last():
    assert subject._provider_order("twitch", make_config()) == ["proxy", "direct", "apify_twitch"]
    assert subject._provider_order("kick", make_config()) == ["proxy", "direct_retry", "apify_kick_hls", "apify_kick_mp4"]
    assert subject._provider_order("twitch", make_config(twitch_download_providers="apify_twitch,proxy,direct")) == ["proxy", "direct", "apify_twitch"]
    with pytest.raises(subject.StreamSourceError):
        subject._provider_order("kick", make_config(kick_download_providers="proxy,bogus"))


def test_proxy_without_config_is_skipped(monkeypatch):
    monkeypatch.setattr(subject, "get_config", lambda: make_config(youtube_proxy_url=""))
    with pytest.raises(subject.ProviderSkipped):
        subject._ydl_options("proxy", Path("out"))


def test_apify_without_token_is_skipped(monkeypatch):
    monkeypatch.setattr(subject, "get_config", lambda: make_config(apify_api_token=""))
    with pytest.raises(subject.ProviderSkipped):
        subject._actor_items("actor", {}, .1)


def test_private_mapping_is_narrow():
    assert subject._is_private_error("ERROR: [twitch:vod] 123456: Video 123456 does not exist")
    assert subject._is_private_error("access to this subscriber-only content")
    assert not subject._is_private_error("Apify actor failed: Actor kick-hls-actor does not exist")
    assert not subject._is_private_error("Dataset does not exist")


def test_redact_hides_proxy_credentials_and_signed_queries():
    text = subject._redact("Unable to connect http://user-abc:pw@proxy.test:8000 via https://media.test/a.m3u8?sig=secret&token=x")
    assert "pw@" not in text and "sig=secret" not in text and "proxy.test" in text


# ------------------------------------------------------------ actor parsing

def test_twitch_clip_never_runs_the_actor(env):
    with pytest.raises(subject.ProviderSkipped):
        subject._apify_info(TWITCH_CLIP, "apify_twitch")
    assert env.actors.calls == []


def test_twitch_actor_reads_length_seconds(env):
    env.actors.results["twitch-actor"] = [twitch_item(lengthSeconds=1210, durationSeconds=None)]
    info = subject._apify_info(TWITCH_VOD, "apify_twitch")
    assert (info["duration"], info["media_url"]) == (1210, "https://media.test/twitch.m3u8")


def test_kick_hls_failed_and_empty_results_are_failures(env):
    for items in ([], [{"status": "failed", "id": None, "hlsUrl": None}]):
        env.actors.results["kick-hls-actor"] = items
        with pytest.raises(RuntimeError, match="no ready media"):
            subject._apify_info(KICK_VOD, "apify_kick_hls")


def test_echoed_input_url_is_not_an_id_match(env):
    env.actors.results["kick-hls-actor"] = [kick_hls_item(id=None, inputUrl=KICK_VOD, kickUrl=f"https://kick.com/video/{KICK_ID}")]
    with pytest.raises(RuntimeError, match="id does not match"):
        subject._apify_info(KICK_VOD, "apify_kick_hls")
    env.actors.results["twitch-actor"] = [twitch_item(id="999", url=TWITCH_VOD)]
    with pytest.raises(RuntimeError, match="id does not match"):
        subject._apify_info(TWITCH_VOD, "apify_twitch")


def test_reported_duration_must_match_expected(env):
    env.actors.results["twitch-actor"] = [twitch_item(lengthSeconds=120)]
    with pytest.raises(RuntimeError, match="duration does not match"):
        subject._apify_info(TWITCH_VOD, "apify_twitch", 100)


def test_parsebird_milliseconds_minimum_cap_and_duration_proof(env, monkeypatch):
    captured = {}

    def actor(_actor, payload, _charge, _timeout=None):
        captured.update(payload)
        return [parsebird_item(duration=41000)]

    monkeypatch.setattr(subject, "_actor_items", actor)
    info = subject._apify_info(KICK_VOD, "apify_kick_mp4", 41)
    assert info["duration"] == 41
    assert captured["maxVideoSizeMb"] >= 100
    with pytest.raises(RuntimeError, match="needs a known duration"):
        subject._apify_info(KICK_VOD, "apify_kick_mp4")
    with pytest.raises(RuntimeError, match="duration does not match"):
        subject._apify_info(KICK_VOD, "apify_kick_mp4", 300)


# ------------------------------------------------------------- worker chain

def test_proxy_success_stops_the_chain(env):
    path = subject.download_stream_audio(TWITCH_VOD, task_id="task1", operation="job")
    assert path.exists()
    assert env.ytdlp.providers("extract") == ["proxy"]
    assert env.actors.calls == []


def test_twitch_falls_back_proxy_direct_then_actor_hls(env):
    env.ytdlp.failures = {"proxy": RuntimeError("HTTP 403"), "direct": RuntimeError("HTTP 403")}
    env.actors.results["twitch-actor"] = [twitch_item()]
    path = subject.download_stream_audio(TWITCH_VOD, task_id="task1", operation="job")
    assert path.exists()
    assert env.ytdlp.providers("extract") == ["proxy", "direct"]
    assert env.ytdlp.providers("media") == ["direct"]
    assert env.actors.calls == ["twitch-actor"]


def test_kick_order_retries_direct_and_falls_through_failed_actor(env, caplog):
    env.ytdlp.failures = {"proxy": RuntimeError("proxy down"), "direct": [RuntimeError("HTTP 403"), RuntimeError("HTTP 403")]}
    env.actors.results["kick-hls-actor"] = [{"status": "failed", "id": None, "hlsUrl": None}]
    env.actors.results["kick-mp4-actor"] = [parsebird_item()]
    with caplog.at_level(logging.INFO):
        path = subject.download_stream_audio(KICK_VOD, MEDIA_DURATION, task_id="task1", operation="job")
    assert path.name == "source.mp3" and path.exists()
    assert env.ytdlp.providers("extract") == ["proxy", "direct", "direct"]
    assert env.actors.calls == ["kick-hls-actor", "kick-mp4-actor"]
    assert "provider=apify_kick_mp4" in caplog.text


def test_actors_run_at_most_once_and_parsebird_file_is_reused(env):
    env.ytdlp.failures = {"proxy": RuntimeError("down"), "direct": RuntimeError("down")}
    env.actors.results["kick-hls-actor"] = []
    env.actors.results["kick-mp4-actor"] = [parsebird_item()]
    audio = subject.download_stream_audio(KICK_VOD, MEDIA_DURATION, task_id="task1", operation="job")
    sections = subject.download_stream_sections(
        KICK_VOD, [{"start_time": 2, "end_time": 6}, {"start_time": 10, "end_time": 12}], MEDIA_DURATION,
        "task1", operation="job",
    )
    full = subject.download_stream_video(KICK_VOD, MEDIA_DURATION, "task1", operation="job")
    assert audio.exists() and sections.exists() and full.exists()
    assert env.actors.calls == ["kick-hls-actor", "kick-mp4-actor"]
    assert env.downloads == ["https://media.test/kick.mp4"]  # one parsebird download per task
    # Later phases start at the provider that worked; proxy/direct aren't retried.
    assert env.ytdlp.providers("extract").count("proxy") == 1


def test_parsebird_sections_are_cut_accurately(env):
    env.ytdlp.failures = {"proxy": RuntimeError("down"), "direct": RuntimeError("down")}
    env.actors.results["kick-mp4-actor"] = [parsebird_item()]
    path = subject.download_stream_sections(
        KICK_VOD, [{"start_time": 3.3, "end_time": 7.7}, {"start_time": 15.1, "end_time": 18.4}], MEDIA_DURATION, "task1", operation="job",
    )
    assert subject._probe(path) == pytest.approx(4.4 + 3.3, abs=0.25)
    time_map = load_time_map(path)
    assert time_map.to_original(4.4 + 1.0) == pytest.approx(16.1, abs=0.01)


def test_yt_dlp_sections_are_verified_and_time_mapped(env):
    path = subject.download_stream_sections(TWITCH_VOD, [{"start_time": 5, "end_time": 9}], MEDIA_DURATION, "task1", operation="job")
    assert subject._probe(path) == pytest.approx(4, abs=0.25)
    assert load_time_map(path).to_original(1.0) == pytest.approx(6.0)


def test_inaccurate_sections_fail_the_provider(env):
    env.ytdlp.wrong_sections = True
    env.config.twitch_download_providers = "proxy"
    with pytest.raises(subject.StreamSourceError, match="couldn't fetch"):
        subject.download_stream_sections(TWITCH_VOD, [{"start_time": 5, "end_time": 9}], MEDIA_DURATION, "task1", operation="job")


def test_section_ranges_are_clamped_to_the_known_length(env):
    subject.download_stream_sections(TWITCH_VOD, [{"start_time": 25, "end_time": 45}], None, "task1", operation="job")
    assert env.ytdlp.calls[-1] == ("download", "proxy", ((25.0, MEDIA_DURATION),))


def test_no_rerun_after_every_provider_failed(env):
    env.ytdlp.failures = {"proxy": RuntimeError("down"), "direct": RuntimeError("down")}
    env.actors.results["twitch-actor"] = RuntimeError("Apify actor failed: run FAILED")
    with pytest.raises(subject.StreamSourceError, match="couldn't fetch"):
        subject.download_stream_audio(TWITCH_VOD, task_id="task1", operation="job")
    calls = len(env.ytdlp.calls)
    with pytest.raises(subject.StreamSourceError, match="couldn't fetch"):
        subject.download_stream_video(TWITCH_VOD, task_id="task1", operation="job")
    assert len(env.ytdlp.calls) == calls
    assert env.actors.calls == ["twitch-actor"]
    assert not list((env.root / "task1" / "job").glob("*/*"))


def test_failed_job_does_not_lock_out_editor_and_failure_expires(env, monkeypatch):
    env.ytdlp.failures = {"proxy": RuntimeError("down"), "direct": RuntimeError("down")}
    env.config.twitch_download_providers = "proxy,direct"
    with pytest.raises(subject.StreamSourceError):
        subject.download_stream_audio(TWITCH_VOD, task_id="task1", operation="job")
    env.ytdlp.failures = {}
    # An editor render is its own operation and is not blocked by the job's failure.
    assert subject.download_stream_sections(TWITCH_VOD, [{"start_time": 1, "end_time": 3}], None, "task1").exists()
    # The job's own marker expires instead of lasting until a worker restart.
    clock = subject.time.monotonic() + subject._FAILED_TTL_SECONDS + 1
    monkeypatch.setattr(subject.time, "monotonic", lambda: clock)
    assert subject.download_stream_audio(TWITCH_VOD, task_id="task1", operation="job").exists()


def test_length_cap_is_checked_before_any_download(env):
    env.ytdlp.duration = 7200
    with pytest.raises(subject.StreamTooLongError, match="Maximum allowed duration is 60 minutes"):
        subject.download_stream_audio(TWITCH_VOD, task_id="task1", max_duration=3600, operation="job")
    assert env.ytdlp.providers("download") == []


def test_length_cap_uses_actor_duration_before_media_download(env):
    env.ytdlp.failures = {"proxy": RuntimeError("down"), "direct": RuntimeError("down")}
    env.actors.results["twitch-actor"] = [twitch_item(lengthSeconds=7200)]
    with pytest.raises(subject.StreamTooLongError):
        subject.download_stream_audio(TWITCH_VOD, task_id="task1", max_duration=3600, operation="job")
    assert env.ytdlp.providers("media") == []


def test_private_vod_stops_paid_fallbacks(env):
    env.ytdlp.failures = {"proxy": RuntimeError("ERROR: Video 123456 does not exist")}
    with pytest.raises(subject.StreamSourceError, match="subscriber-only or private"):
        subject.download_stream_audio(TWITCH_VOD, task_id="task1", operation="job")
    assert env.actors.calls == []


def test_apify_does_not_exist_error_does_not_stop_the_chain(env):
    env.ytdlp.failures = {"proxy": RuntimeError("down"), "direct": [RuntimeError("down"), RuntimeError("down")]}
    env.actors.results["kick-hls-actor"] = RuntimeError("Apify actor failed: Actor kick-hls-actor does not exist")
    env.actors.results["kick-mp4-actor"] = [parsebird_item()]
    assert subject.download_stream_video(KICK_VOD, MEDIA_DURATION, "task1", operation="job").exists()
    assert env.actors.calls == ["kick-hls-actor", "kick-mp4-actor"]


def test_real_direct_error_is_reported_when_proxy_is_not_configured(env, caplog):
    env.config.youtube_proxy_url = ""
    env.ytdlp.failures = {"direct": RuntimeError("down"), "media:direct": RuntimeError("HTTP Error 403: Forbidden")}
    env.actors.results["twitch-actor"] = [twitch_item()]
    with caplog.at_level(logging.WARNING), pytest.raises(subject.StreamSourceError):
        subject.download_stream_audio(TWITCH_VOD, task_id="task1", operation="job")
    assert "provider=apify_twitch" in caplog.text and "HTTP Error 403" in caplog.text


def test_actor_media_with_wrong_length_is_rejected(env, monkeypatch):
    env.ytdlp.failures = {"proxy": RuntimeError("down"), "direct": RuntimeError("down")}
    env.actors.results["twitch-actor"] = [twitch_item(lengthSeconds=MEDIA_DURATION)]
    env.ytdlp.duration = 500.0  # what ffprobe reports for the actor's media URL
    with pytest.raises(subject.StreamSourceError):
        subject.download_stream_audio(TWITCH_VOD, MEDIA_DURATION, task_id="task1", operation="job")
    assert env.ytdlp.providers("media") == []


def test_seeded_preflight_result_is_reused_not_rerun(env):
    env.ytdlp.failures = {"proxy": RuntimeError("down"), "direct": [RuntimeError("down"), RuntimeError("down")]}
    subject.seed_stream_resolution("task1", {"provider": "apify_kick_hls", "duration": MEDIA_DURATION, "media_url": "https://media.test/kick.m3u8", "resolved_at": subject.time.time()}, 30)
    assert subject.download_stream_audio(KICK_VOD, task_id="task1", operation="job").exists()
    assert env.actors.calls == []


def test_stale_seeded_media_url_is_ignored(env):
    subject.seed_stream_resolution("task1", {"provider": "apify_kick_hls", "duration": 30, "media_url": "https://media.test/old.m3u8", "resolved_at": 1}, 30)
    state = subject._operation("task1", "job")
    assert state.actor_results == {} and state.expected_duration == 30


# ----------------------------------------------------------------- cleanup

def test_cleanup_removes_only_its_own_operation(env):
    job = subject.download_stream_audio(TWITCH_VOD, task_id="task1", operation="job")
    edit_a = subject.download_stream_sections(TWITCH_VOD, [{"start_time": 1, "end_time": 3}], None, "task1")
    edit_b = subject.download_stream_sections(TWITCH_VOD, [{"start_time": 4, "end_time": 6}], None, "task1")
    assert subject.cleanup_stream_path(edit_a)
    assert not edit_a.exists() and edit_b.exists() and job.exists()
    subject.cleanup_stream_task("task1")
    assert not job.exists() and edit_b.exists()
    assert subject.cleanup_stream_path(edit_b)
    assert not (env.root / "task1").exists()
    assert subject._operations == {}


def test_cleanup_ignores_paths_outside_stream_work(env, tmp_path):
    outside = tmp_path / "other.mp4"
    outside.write_bytes(b"x")
    assert not subject.cleanup_stream_path(outside)
    assert outside.exists()


def test_failed_downloads_leave_no_media_behind(env):
    env.ytdlp.failures = {"download:proxy": RuntimeError("cut off"), "direct": RuntimeError("down")}
    env.actors.results["twitch-actor"] = []
    with pytest.raises(subject.StreamSourceError):
        subject.download_stream_video(TWITCH_VOD, task_id="task1", operation="job")
    assert [p for p in (env.root / "task1").rglob("*") if p.is_file()] == []


# --------------------------------------------------------------- preflight

def test_preflight_uses_cheap_actor_when_proxy_and_direct_fail(env):
    env.ytdlp.failures = {"proxy": RuntimeError("down"), "direct_retry": RuntimeError("down")}
    env.actors.results["kick-hls-actor"] = [kick_hls_item(title="VOD")]
    info = subject.fetch_stream_info(KICK_VOD)
    assert info["provider"] == "apify_kick_hls" and info["duration"] == MEDIA_DURATION
    assert info["media_url"] == "https://media.test/kick.m3u8"
    assert env.actors.calls == ["kick-hls-actor"]


def test_preflight_never_runs_parsebird(env):
    env.ytdlp.failures = {"proxy": RuntimeError("down"), "direct_retry": RuntimeError("down")}
    env.actors.results["kick-hls-actor"] = []
    with pytest.raises(subject.StreamSourceError, match="couldn't fetch"):
        subject.fetch_stream_info(KICK_VOD)
    assert env.actors.calls == ["kick-hls-actor"]


def test_preflight_success_has_no_actor_cost(env):
    info = subject.fetch_stream_info(TWITCH_VOD)
    assert info["provider"] == "proxy" and "media_url" not in info
    assert env.actors.calls == []


def test_preflight_has_one_overall_deadline(env, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(subject.time, "monotonic", lambda: clock[0])

    def slow(url, provider, *, session, deadline=None):
        env.ytdlp.calls.append(("extract", provider, url))
        clock[0] += 29.5
        raise RuntimeError("timed out")

    monkeypatch.setattr(subject, "_extract", slow)
    with pytest.raises(subject.StreamSourceError):
        subject.fetch_stream_info(KICK_VOD, deadline_seconds=30)
    assert env.ytdlp.providers("extract") == ["proxy"]
    assert env.actors.calls == []


def test_preflight_maps_private_vods(env):
    env.ytdlp.failures = {"proxy": RuntimeError("You must be logged into an account that has access to this subscriber-only content")}
    with pytest.raises(subject.StreamSourceError, match="subscriber-only or private"):
        subject.fetch_stream_info(TWITCH_VOD)


def test_extract_bounds_socket_timeout_by_deadline(monkeypatch):
    seen = {}

    class FakeYDL:
        def __init__(self, options):
            seen.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download, process):
            seen["download"], seen["process"] = download, process
            return {"duration": 10}

    import yt_dlp
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(subject, "get_config", lambda: make_config(youtube_proxy_url=""))
    subject._extract(TWITCH_VOD, "direct", session="s", deadline=subject.time.monotonic() + 3)
    assert seen["socket_timeout"] <= 3 and seen["download"] is False and seen["process"] is False


def test_section_ranges_are_numbered_so_files_do_not_overwrite(monkeypatch):
    monkeypatch.setattr(subject, "get_config", lambda: make_config())
    options = subject._ydl_options("direct", Path("x/section-%(section_number)03d.%(ext)s"), ranges=[(1.0, 2.0), (3.0, 4.0)])
    assert options["download_ranges"](None, None) == [
        {"start_time": 1.0, "end_time": 2.0, "index": 1},
        {"start_time": 3.0, "end_time": 4.0, "index": 2},
    ]
    assert options["force_keyframes_at_cuts"] is True
