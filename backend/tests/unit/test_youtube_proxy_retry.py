"""Transient download failures recover with fresh URLs and bounded resources."""

import copy
import logging
import re
import ssl

import pytest

from src import youtube_proxy as proxy
from src.config import Config, set_config_override
from src.observability import JsonLogFormatter
from tests.unit.test_youtube_audio_fallback import FORMATS, PROXY, URL, VIDEO_ID


@pytest.fixture
def downloads(tmp_path, monkeypatch):
    config = Config()
    config.youtube_proxy_url = PROXY
    config.youtube_proxy_country = ""
    config.youtube_pot_provider_url = ""
    config.youtube_proxy_timeout_seconds = 300
    config.youtube_download_max_bytes = 1000
    set_config_override(config)
    state = {"metadata": [], "media": [], "failures": 1, "clock": 100.0,
             "error": RuntimeError("Tunnel connection failed: 502 NO_HOST_CONNECTION"),
             "bytes": 5, "sleeps": []}
    monkeypatch.setattr(proxy.time, "monotonic", lambda: state["clock"])
    monkeypatch.setattr(proxy.time, "sleep", lambda seconds: state["sleeps"].append(seconds))

    def metadata(_url, options):
        state["metadata"].append(dict(options))
        return {"duration": 20, "formats": copy.deepcopy(FORMATS)}

    def download(options, _url, _id, fmt, stem, fragments=None):
        state["media"].append(dict(options))
        target = stem.with_suffix(".mp4")
        target.parent.mkdir(parents=True, exist_ok=True)
        # New attempts must not reuse fragments from a different proxy session.
        assert not target.exists()
        target.write_bytes(b"x" * state["bytes"])
        options["progress_hooks"][0]({
            "status": "downloading", "filename": str(target), "downloaded_bytes": state["bytes"],
        })
        if state["failures"]:
            state["failures"] -= 1
            if isinstance(state["error"], proxy.DownloadDeadlineExceeded):
                state["clock"] = options["_youtube_deadline"] + 0.1
            raise state["error"]
        return target

    monkeypatch.setattr(proxy, "extract_video_info", metadata)
    monkeypatch.setattr(proxy, "native_download_format", download)
    monkeypatch.setattr(proxy, "_mux_local_window", lambda _v, _a, dest, **kw: dest.write_bytes(b"mux"))

    def run(kind, budget=None):
        if kind == "audio":
            return proxy.download_audio(URL, VIDEO_ID, tmp_path / "audio", task_id="task12345678", budget=budget)
        return proxy.download_section_file(URL, VIDEO_ID, 0, 10, tmp_path / "section.mp4",
                                           task_id="task12345678", budget=budget)[0]

    yield state, run
    set_config_override(None)


@pytest.mark.parametrize("kind", ["audio", "section"])
def test_transport_failure_reextracts_on_new_session_and_keeps_byte_budget(downloads, kind, caplog):
    state, run = downloads
    caplog.set_level(logging.INFO)
    budget = proxy.ByteBudget(1000)
    assert run(kind, budget).exists()
    first, retry = state["metadata"]
    assert first["proxy"] != retry["proxy"]
    assert all(item["proxy"] == retry["proxy"] for item in state["media"][1:])
    assert retry["_youtube_deadline"] == 160
    assert budget.total == (15 if kind == "section" else 10)
    assert "youtube_proxy_retry attempt=2" in caplog.text
    assert "super-secret-password" not in caplog.text


@pytest.mark.parametrize("kind", ["audio", "section"])
def test_timeout_gets_only_sixty_extra_seconds(downloads, kind):
    state, run = downloads
    state["error"] = proxy.DownloadDeadlineExceeded("Residential proxy download attempt timed out")
    run(kind)
    first, retry = state["metadata"]
    assert first["_youtube_deadline"] == 400
    # A retry gets 60 seconds from the failure, not another full attempt.
    # Audio shares the original overall cap. A section may use its longer
    # transport cap, so that 60-second window is not clamped early.
    if kind == "section":
        assert retry["_youtube_deadline"] == pytest.approx(460.1)
    else:
        assert retry["_youtube_deadline"] == 460


@pytest.mark.parametrize("kind", ["audio", "section"])
def test_retry_exhaustion_returns_control_to_existing_fallback(downloads, kind, tmp_path):
    state, run = downloads
    state["failures"] = 10
    with pytest.raises(RuntimeError, match="502 NO_HOST_CONNECTION"):
        run(kind)
    expected = 1 + (proxy.SECTION_TRANSPORT_RETRIES if kind == "section" else 1)
    assert len(state["metadata"]) == expected
    assert state["sleeps"] == ([2.0, 4.0, 8.0] if kind == "section" else [2.0])
    assert not list(tmp_path.rglob("*.mp4"))


@pytest.mark.parametrize("kind", ["audio", "section"])
def test_failed_bytes_cannot_get_a_new_allowance_on_retry(downloads, kind):
    state, run = downloads
    with pytest.raises(proxy.DownloadBudgetExceeded):
        run(kind, proxy.ByteBudget(8))
    assert len(state["metadata"]) == 2


@pytest.mark.parametrize("kind", ["audio", "section"])
@pytest.mark.parametrize("error", [
    proxy.DownloadBudgetExceeded("over cap"),
    proxy.PartialDownloadUnavailable("Selected formats have no timed fragments"),
    RuntimeError("Sign in to confirm you're not a bot"),
    RuntimeError("Video is private"),
    RuntimeError("Giving up after 1 retries"),
])
def test_nontransient_failures_do_not_retry(downloads, kind, error):
    state, run = downloads
    state["error"] = error
    with pytest.raises(type(error)):
        run(kind)
    assert len(state["metadata"]) == 1
    assert state["sleeps"] == []


SSL_RECORD_LAYER = (
    "ERROR: \r[download] Got error: [SSL] record layer failure (_ssl.c:2590). "
    "Giving up after 1 retries"
)


def test_generic_yt_dlp_give_up_is_not_transient():
    assert not proxy.is_transient_proxy_error(RuntimeError("Giving up after 1 retries"))
    assert not proxy.is_transient_proxy_error(RuntimeError("Sign in to confirm you're not a bot"))
    assert not proxy.is_transient_proxy_error(RuntimeError("HTTP Error 403: Forbidden"))
    assert not proxy.is_transient_proxy_error(RuntimeError("Video unavailable"))


@pytest.mark.parametrize("message", [
    SSL_RECORD_LAYER,
    "ssl.SSLError: [SSL: DECRYPTION_FAILED_OR_BAD_RECORD_MAC] decryption failed",
    "TLSV1_ALERT_INTERNAL_ERROR",
    "Connection reset by peer",
    "Broken pipe",
    "Unexpected EOF while reading",
    "[errno 104] econnreset",
])
def test_ssl_tls_and_reset_errors_are_transient(message):
    assert proxy.is_transient_proxy_error(RuntimeError(message))


def test_ssl_error_type_is_transient():
    assert proxy.is_transient_proxy_error(ssl.SSLError("record layer failure"))


def test_section_ssl_rotates_session_with_backoff_before_the_caller_falls_back(downloads, caplog):
    state, run = downloads
    state["error"] = RuntimeError(
        SSL_RECORD_LAYER + " proxy=http://user:super-secret-password@gw.example:823"
        " url=https://rr.googlevideo.com/videoplayback?sig=secret"
    )
    state["failures"] = 10
    caplog.set_level(logging.INFO)
    with pytest.raises(RuntimeError, match="record layer failure"):
        run("section")
    expected = 1 + proxy.SECTION_TRANSPORT_RETRIES
    assert len(state["metadata"]) == expected
    assert state["sleeps"] == [2.0, 4.0, 8.0]
    sessions = [
        re.search(r"session-([A-Za-z0-9]+)", item["proxy"]).group(1)
        for item in state["metadata"]
    ]
    assert len(set(sessions)) == expected
    retries = [record for record in caplog.records if "youtube_proxy_retry" in record.getMessage()]
    assert [record.youtube_retry_attempt for record in retries] == [2, 3, 4]
    assert all(record.youtube_proxy_session_rotated is True for record in retries)
    assert all(record.youtube_download_kind == "section" for record in retries)
    formatted = "\n".join(JsonLogFormatter().format(record) for record in retries)
    assert "super-secret-password" not in formatted
    assert "sig=secret" not in formatted
    # Rotated session ids are new random values. The first id is derived from
    # the task id and may appear as task_id; the rotated ones must not.
    for session_id in sessions[1:]:
        assert session_id not in formatted


def test_audio_ssl_still_retries_only_once(downloads):
    state, run = downloads
    state["error"] = RuntimeError(SSL_RECORD_LAYER)
    state["failures"] = 10
    with pytest.raises(RuntimeError, match="record layer failure"):
        run("audio")
    assert len(state["metadata"]) == 2
    assert state["sleeps"] == [2.0]


def test_retry_has_no_socket_budget_after_overall_deadline(monkeypatch):
    now = [0]
    monkeypatch.setattr(proxy.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(proxy, "download_options", lambda *a, **kw: {})
    attempts = []

    def fail(options):
        attempts.append(options)
        now[0] = 1000
        raise TimeoutError("timed out")

    with pytest.raises(TimeoutError):
        proxy._with_proxy_retry(fail, task_id="task", video_id=VIDEO_ID,
                               budget=proxy.ByteBudget(100), cleanup=lambda: None)
    assert len(attempts) == 1
