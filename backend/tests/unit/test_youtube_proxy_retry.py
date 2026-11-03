"""Transient download failures recover with fresh URLs and bounded resources."""

import copy
import logging

import pytest

from src import youtube_proxy as proxy
from src.config import Config, set_config_override
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
             "bytes": 5}
    monkeypatch.setattr(proxy.time, "monotonic", lambda: state["clock"])

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
    assert retry["_youtube_deadline"] == 460  # shared deadline, not another 300 seconds


@pytest.mark.parametrize("kind", ["audio", "section"])
def test_retry_exhaustion_returns_control_to_existing_fallback(downloads, kind, tmp_path):
    state, run = downloads
    state["failures"] = 10
    with pytest.raises(RuntimeError, match="502 NO_HOST_CONNECTION"):
        run(kind)
    assert len(state["metadata"]) == 2
    assert not list(tmp_path.rglob("*.mp4"))


@pytest.mark.parametrize("kind", ["audio", "section"])
def test_failed_bytes_cannot_get_a_new_allowance_on_retry(downloads, kind):
    state, run = downloads
    with pytest.raises(proxy.DownloadBudgetExceeded):
        run(kind, proxy.ByteBudget(8))
    assert len(state["metadata"]) == 2


@pytest.mark.parametrize("error", [
    proxy.DownloadBudgetExceeded("over cap"),
    proxy.PartialDownloadUnavailable("Selected formats have no timed fragments"),
    RuntimeError("Sign in to confirm you're not a bot"),
    RuntimeError("Video is private"),
])
def test_nontransient_failures_do_not_retry(downloads, error):
    state, run = downloads
    state["error"] = error
    with pytest.raises(type(error)):
        run("audio")
    assert len(state["metadata"]) == 1


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
