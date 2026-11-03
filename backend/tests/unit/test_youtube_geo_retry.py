"""Region recovery follows YouTube's country list without changing other jobs."""

import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from contextlib import contextmanager

import pytest
from yt_dlp.utils import DownloadError, GeoRestrictedError

from src import youtube_proxy as proxy
from src.config import Config, set_config_override
from tests.unit.test_youtube_audio_fallback import FORMATS, URL, VIDEO_ID


def geo_error(countries=("AR", "US", "CA")):
    cause = GeoRestrictedError(
        "The uploader has not made this video available in your country", countries=countries,
    )
    return DownloadError("YouTube extraction failed", (type(cause), cause, None))


@pytest.fixture
def region_downloads(tmp_path, monkeypatch):
    config = Config()
    config.youtube_proxy_url = "http://user-country-{country}-session-{session}:secret@proxy.example:8000"
    config.youtube_proxy_country = "de"
    config.youtube_pot_provider_url = ""
    config.youtube_proxy_timeout_seconds = 300
    set_config_override(config)
    state = {"metadata": [], "media": [], "errors": [geo_error()], "clock": 100.0}
    monkeypatch.setattr(proxy.time, "monotonic", lambda: state["clock"])

    def metadata(_url, options):
        state["metadata"].append(dict(options))
        if state["errors"]:
            raise state["errors"].pop(0)
        proxy._metadata_ready(options)
        return {"duration": 20, "formats": copy.deepcopy(FORMATS)}

    def media(options, _url, _id, fmt, stem, fragments=None):
        state["media"].append(dict(options))
        path = stem.with_suffix(".mp4")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
        options["progress_hooks"][0]({"status": "finished", "filename": str(path), "downloaded_bytes": 5})
        return path

    @contextmanager
    def full_client(options):
        class Client:
            def download(self, urls):
                metadata(urls[0], options)
                state["media"].append(dict(options))
                Path(options["outtmpl"].replace("%(ext)s", "mp4")).write_bytes(b"video")
        yield Client()

    monkeypatch.setattr(proxy, "extract_video_info", metadata)
    monkeypatch.setattr(proxy, "native_download_format", media)
    monkeypatch.setattr(proxy, "_proxy_client", full_client)
    monkeypatch.setattr(proxy, "_mux_local_window", lambda _v, _a, dest, **kw: dest.write_bytes(b"mux"))

    def run(kind, budget=None, task_id="task12345678"):
        kwargs = {"task_id": task_id, "budget": budget}
        if kind == "audio":
            return proxy.download_audio(URL, VIDEO_ID, tmp_path / "audio", **kwargs)
        if kind == "full":
            return proxy.download_full_with_proxy(URL, VIDEO_ID, tmp_path, **kwargs)
        return proxy.download_section_file(URL, VIDEO_ID, 0, 10, tmp_path / "section.mp4", **kwargs)[0]

    yield config, state, run
    set_config_override(None)


@pytest.mark.parametrize("kind", ["audio", "section", "full"])
def test_geo_failure_reextracts_and_downloads_in_permitted_region(region_downloads, kind, caplog):
    config, state, run = region_downloads
    with caplog.at_level("INFO"):
        assert run(kind).exists()
    first, retry = state["metadata"]
    assert "country-de-" in first["proxy"]
    assert "country-us-" in retry["proxy"]
    assert first["proxy"].split("session-")[1] != retry["proxy"].split("session-")[1]
    assert all(item["proxy"] == retry["proxy"] for item in state["media"])
    assert retry["_youtube_deadline"] == 460
    assert config.youtube_proxy_country == "de"
    assert "youtube_proxy_region_retry country=us" in caplog.text
    assert "secret@" not in caplog.text


def test_successful_route_is_reused_for_sections_but_not_other_jobs(region_downloads):
    _, state, run = region_downloads
    budget = proxy.ByteBudget(100)
    run("audio", budget)
    recovered_proxy = state["media"][-1]["proxy"]
    run("section", budget)
    assert state["metadata"][-1]["proxy"] == recovered_proxy
    run("audio", proxy.ByteBudget(100), task_id="other12345678")
    assert "country-de-" in state["metadata"][-1]["proxy"]


@pytest.mark.parametrize("countries", [None, [], ["US@attacker"], ["ZZ"], ["DE"]])
def test_no_region_guess_when_country_evidence_is_missing_or_invalid(region_downloads, countries):
    _, state, run = region_downloads
    state["errors"] = [geo_error(countries)]
    with pytest.raises(proxy.YouTubeGeoRestrictedError, match="Upload the video file"):
        run("audio")
    assert len(state["metadata"]) == 1


def test_fixed_country_proxy_cannot_silently_retry_another_region(region_downloads):
    config, state, run = region_downloads
    config.youtube_proxy_url = config.youtube_proxy_url.replace("{country}", "de")
    with pytest.raises(proxy.YouTubeGeoRestrictedError):
        run("audio")
    assert len(state["metadata"]) == 1


def test_repeated_geo_failure_stops_after_one_region_retry(region_downloads):
    _, state, run = region_downloads
    state["errors"] = [geo_error(), geo_error(["CA"]), geo_error()]
    with pytest.raises(proxy.YouTubeGeoRestrictedError):
        run("section")
    assert len(state["metadata"]) == 2


def test_transport_and_region_retries_share_one_extra_time_allowance(region_downloads):
    _, state, run = region_downloads
    state["errors"] = [TimeoutError("timed out"), geo_error()]
    run("audio")
    first, transport, region = state["metadata"]
    assert transport["_youtube_deadline"] == 160
    assert region["_youtube_deadline"] == 460
    assert "country-de-" in transport["proxy"]
    assert "country-us-" in region["proxy"]


def test_geo_retry_does_not_reset_byte_budget(region_downloads):
    _, state, run = region_downloads
    budget = proxy.ByteBudget(7)
    budget.add(4)
    with pytest.raises(proxy.DownloadBudgetExceeded):
        run("audio", budget)
    assert budget.total == 9
    assert len(state["metadata"]) == 2


def test_forbidden_response_does_not_trigger_country_switch(region_downloads):
    _, state, run = region_downloads
    state["errors"] = [DownloadError("HTTP Error 403: Forbidden")]
    with pytest.raises(DownloadError):
        run("audio")
    assert len(state["metadata"]) == 1


def test_geo_detection_supports_wrapped_errors_and_public_messages():
    wrapped = RuntimeError("wrapper")
    wrapped.__cause__ = geo_error(["US"])
    assert proxy.is_geo_restricted_error(wrapped)
    assert proxy.is_geo_restricted_error(proxy.YouTubeGeoRestrictedError())
    assert proxy.is_geo_restricted_error(DownloadError("ERROR: This video is not available in your country"))
    assert not proxy.is_geo_restricted_error(DownloadError("HTTP Error 403: Forbidden"))
    wrapped.__context__ = wrapped
    assert proxy._allowed_geo_countries(wrapped) == ["us"]


def test_metadata_preflight_can_recover_without_downloading_media(region_downloads):
    _, state, _ = region_downloads
    info = proxy.extract_video_info_with_proxy(URL, VIDEO_ID)
    assert info["duration"] == 20
    assert len(state["metadata"]) == 2
    assert "country-us-" in state["metadata"][-1]["proxy"]
    assert state["media"] == []


def test_full_retry_accounts_partial_transfer_without_progress(region_downloads, tmp_path, monkeypatch):
    _, state, run = region_downloads
    state["errors"] = []
    attempts = []

    @contextmanager
    def client(options):
        class Client:
            def download(self, _urls):
                attempts.append(options)
                target = Path(options["outtmpl"].replace("%(ext)s", "mp4"))
                if len(attempts) == 1:
                    target.with_suffix(".mp4.part").write_bytes(b"12345")
                    raise ConnectionError("connection reset")
                assert not Path(attempts[0]["outtmpl"]).parent.exists()
                assert Path(attempts[0]["outtmpl"]).parent != target.parent
                target.write_bytes(b"12345")
        yield Client()

    monkeypatch.setattr(proxy, "_proxy_client", client)
    budget = proxy.ByteBudget(8)
    with pytest.raises(proxy.DownloadBudgetExceeded):
        run("full", budget)
    assert len(attempts) == 2
    assert budget.total == 10


def test_full_failure_does_not_charge_merged_output_twice(region_downloads, tmp_path, monkeypatch):
    _, state, run = region_downloads
    state["errors"] = []

    @contextmanager
    def client(options):
        class Client:
            def download(self, _urls):
                stem = Path(options["outtmpl"]).parent / VIDEO_ID
                for suffix in (".f137.mp4", ".f140.m4a"):
                    options["progress_hooks"][0]({
                        "status": "finished", "filename": str(stem) + suffix,
                        "downloaded_bytes": 5,
                    })
                stem.with_suffix(".mp4").write_bytes(b"merged" * 2)
                raise RuntimeError("local merge verification failed")
        yield Client()

    monkeypatch.setattr(proxy, "_proxy_client", client)
    budget = proxy.ByteBudget(15)
    with pytest.raises(RuntimeError, match="local merge"):
        run("full", budget)
    assert budget.total == 10


@pytest.mark.parametrize("kind,error_type", [
    ("section", proxy.PartialDownloadUnavailable),
    ("audio", proxy.AudioDownloadForbidden),
])
def test_post_geo_format_failure_keeps_original_error_and_working_route(
    region_downloads, monkeypatch, kind, error_type,
):
    _, state, run = region_downloads
    budget = proxy.ByteBudget(100)
    if kind == "section":
        monkeypatch.setattr(proxy, "pick_video_format", lambda *_args: None)
    else:
        def forbidden(*_args, **_kwargs):
            raise DownloadError("HTTP Error 403: Forbidden")
        monkeypatch.setattr(proxy, "native_download_format", forbidden)
    with pytest.raises(error_type):
        run(kind, budget)
    recovered = state["metadata"][-1]["proxy"]
    assert "country-us-" in recovered
    assert run("full", budget).exists()
    assert state["metadata"][-1]["proxy"] == recovered


def test_post_geo_bot_check_does_not_become_regional_error(region_downloads):
    _, state, run = region_downloads
    failure = DownloadError("Sign in to confirm you're not a bot")
    state["errors"] = [geo_error(), failure]
    with pytest.raises(DownloadError) as raised:
        run("audio")
    assert raised.value is failure
    assert not proxy.is_geo_restricted_error(raised.value)


def test_unrelated_failure_during_geo_handler_is_not_geo():
    try:
        raise geo_error()
    except DownloadError:
        try:
            raise ConnectionError("connection reset")
        except ConnectionError as failure:
            assert failure.__context__ is not None
            assert not proxy.is_geo_restricted_error(failure)
            assert proxy._allowed_geo_countries(failure) == []
            assert proxy.is_transient_proxy_error(failure)


def test_region_download_can_use_more_than_sixty_seconds(region_downloads, monkeypatch):
    _, state, run = region_downloads
    download = proxy.native_download_format

    def slow_download(options, *args, **kwargs):
        state["clock"] += 90
        proxy._check_deadline(options)
        return download(options, *args, **kwargs)

    monkeypatch.setattr(proxy, "native_download_format", slow_download)
    assert run("audio").exists()
    assert state["clock"] == 190
    assert state["media"][-1]["_youtube_deadline"] == 460


def test_metadata_retries_obey_callers_absolute_deadline(region_downloads):
    _, state, _ = region_downloads
    proxy.extract_video_info_with_proxy(URL, VIDEO_ID, deadline=125)
    assert [item["_youtube_deadline"] for item in state["metadata"]] == [125, 125]
    state["metadata"].clear()
    with pytest.raises(proxy.DownloadDeadlineExceeded):
        proxy.extract_video_info_with_proxy(URL, VIDEO_ID, deadline=99)
    assert state["metadata"] == []


def test_concurrent_full_download_failure_leaves_other_job_and_sidecars_intact(
    region_downloads, tmp_path, monkeypatch,
):
    _, _, _ = region_downloads
    original = tmp_path / f"{VIDEO_ID}.mp4"
    original.write_bytes(b"shared source")
    sidecar = original.with_suffix(".transcript_cache.json")
    payload = {"version": 1, "words": [{"text": "hello", "start": 0, "end": 100}], "text": "hello"}
    sidecar.write_text(json.dumps(payload))
    good_ready = threading.Event()
    bad_finished = threading.Event()
    paths = []

    @contextmanager
    def client(options):
        class Client:
            def download(self, _urls):
                path = Path(options["outtmpl"].replace("%(ext)s", "mp4"))
                paths.append(path)
                if "session-good0001" in options["proxy"]:
                    path.write_bytes(b"success")
                    good_ready.set()
                    assert bad_finished.wait(5)
                    assert path.read_bytes() == b"success"
                else:
                    assert good_ready.wait(5)
                    path.with_suffix(".mp4.part").write_bytes(b"bad")
                    raise RuntimeError("fatal test failure")
        yield Client()

    monkeypatch.setattr(proxy, "_proxy_client", client)
    good_budget, bad_budget = proxy.ByteBudget(100), proxy.ByteBudget(100)

    def bad_job():
        try:
            with pytest.raises(RuntimeError, match="fatal test"):
                proxy.download_full_with_proxy(URL, VIDEO_ID, tmp_path, task_id="bad00001", budget=bad_budget)
        finally:
            bad_finished.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        good = pool.submit(proxy.download_full_with_proxy, URL, VIDEO_ID, tmp_path,
                           task_id="good0001", budget=good_budget)
        bad = pool.submit(bad_job)
        result = good.result(timeout=10)
        bad.result(timeout=10)
    assert paths[0].parent != paths[1].parent
    assert original.read_bytes() == b"shared source"
    assert json.loads(sidecar.read_text()) == payload
    assert json.loads(result.with_suffix(".transcript_cache.json").read_text()) == payload
    assert result.read_bytes() == b"success"
    assert good_budget.total == 7
    assert bad_budget.total == 3
    assert list(tmp_path.glob(f"proxy-{VIDEO_ID}-*")) == [result.parent]
    assert not list(result.parent.glob("attempt-*"))


def test_failed_full_download_preserves_root_sidecar(region_downloads, tmp_path):
    _, state, run = region_downloads
    sidecar = tmp_path / f"{VIDEO_ID}.transcript_cache.json"
    sidecar.write_text('{"words": []}')
    state["errors"] = [geo_error(), geo_error()]
    with pytest.raises(proxy.YouTubeGeoRestrictedError):
        run("full")
    assert sidecar.read_text() == '{"words": []}'
    assert not list(tmp_path.glob(f"proxy-{VIDEO_ID}-*"))


def test_incomplete_audio_response_does_not_reuse_completed_video_counter(region_downloads, tmp_path):
    budget = proxy.ByteBudget(100)
    options = proxy.download_options("task12345678", VIDEO_ID, budget)
    video = tmp_path / "video.mp4"
    options["_youtube_count_bytes"]({
        "status": "finished", "filename": str(video), "downloaded_bytes": 5,
    })
    audio = tmp_path / "audio"
    proxy._account_failed_media(
        options, audio, DownloadError("3 bytes read, 7 more expected"),
        extensions={".m4a"}, incomplete_filename=str(audio) + ".m4a",
    )
    assert budget.total == 8


@pytest.mark.parametrize("audio_partial_exists", [False, True])
def test_full_incomplete_audio_is_not_charged_to_completed_video(
    region_downloads, tmp_path, monkeypatch, audio_partial_exists,
):
    _, _, run = region_downloads
    attempts = []

    @contextmanager
    def client(options):
        class Client:
            def download(self, _urls):
                attempts.append(options)
                stem = Path(options["outtmpl"]).parent / VIDEO_ID
                video = Path(str(stem) + ".f137.mp4")
                video.write_bytes(b"12345")
                options["progress_hooks"][0]({
                    "status": "finished", "filename": str(video), "downloaded_bytes": 5,
                })
                if audio_partial_exists:
                    audio = Path(str(stem) + ".f140.m4a")
                    Path(str(audio) + ".part").write_bytes(b"12")
                    options["progress_hooks"][0]({
                        "status": "downloading", "filename": str(audio), "downloaded_bytes": 2,
                    })
                raise DownloadError("3 bytes read, 7 more expected")
        yield Client()

    monkeypatch.setattr(proxy, "_proxy_client", client)
    budget = proxy.ByteBudget(7)
    with pytest.raises(proxy.DownloadBudgetExceeded):
        run("full", budget)
    assert budget.total == 8
    assert len(attempts) == 1  # Failed bytes exhaust the budget before a retry.
    assert not list(tmp_path.glob(f"proxy-{VIDEO_ID}-*"))
