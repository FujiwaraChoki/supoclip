"""A proxy at capacity (HTTP 466 on CONNECT) is waited out, never paid around."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src import youtube_proxy as proxy
from src import youtube_source
from src import youtube_utils as utils
from src.services import video_service
from src.services.video_service import VideoService
from tests.unit.test_youtube_audio_fallback import URL, VIDEO_ID
from tests.unit.test_processing_checkpoints import pipeline  # noqa: F401  (fixture)
from tests.unit.test_youtube_proxy_retry import downloads  # noqa: F401  (fixture)

CAPACITY = RuntimeError(
    "ERROR: Unable to download API page: ('Unable to connect to proxy', "
    "OSError('Tunnel connection failed: 466 Limit Reached'))"
)


@pytest.fixture
def sleeps(downloads, monkeypatch):  # noqa: F811
    state, _run = downloads
    waits = []

    def sleep(seconds):
        waits.append(seconds)
        state["clock"] += seconds

    monkeypatch.setattr(proxy, "_capacity_sleep", sleep)
    return waits


@pytest.fixture
def chain(tmp_path, monkeypatch, downloads):  # noqa: F811
    """Every provider after the proxy is a mock that must not be reached on a 466."""
    config = proxy.get_config()
    config.temp_dir = str(tmp_path)
    config.youtube_source_cache = False
    config.rapidapi_youtube_api_key = "test-key"
    config.apify_api_token = "test-token"
    calls = SimpleNamespace(
        rapidapi=Mock(return_value=None),
        apify=Mock(return_value=None),
        direct=Mock(return_value=None),
        full_proxy=Mock(side_effect=AssertionError("the full proxy download was not expected")),
    )
    monkeypatch.setattr(utils, "download_video_via_rapidapi", calls.rapidapi)
    monkeypatch.setattr(utils, "download_youtube_video_with_apify", calls.apify)
    monkeypatch.setattr(utils, "_download_youtube_video_with_ytdlp", calls.direct)
    monkeypatch.setattr(utils, "download_full_with_proxy", calls.full_proxy)
    monkeypatch.setattr(utils, "_resolve_apify_quality", lambda *a: "1080")

    def sections(url, video_id, segments, *, task_id=None, budget=None, **_kwargs):
        # The real section downloader, through the real proxy retry loop.
        return proxy.download_section_file(
            url, video_id, 0, 10, tmp_path / "section.mp4", task_id=task_id, budget=budget,
        )[0]

    monkeypatch.setattr(video_service, "materialize_clip_source", sections)
    return calls


def test_capacity_error_is_its_own_class():
    assert proxy.is_proxy_capacity_error(CAPACITY)
    assert proxy.is_proxy_capacity_error(RuntimeError("HTTP 466: Limit Reached"))
    assert not proxy.is_transient_proxy_error(CAPACITY)
    assert proxy.is_transient_proxy_error(RuntimeError("Tunnel connection failed: 502 NO_HOST_CONNECTION"))
    assert not proxy.is_proxy_capacity_error(RuntimeError("Tunnel connection failed: 502 NO_HOST_CONNECTION"))
    assert not proxy.is_proxy_capacity_error(RuntimeError("downloaded 466 fragments"))


def test_section_waits_and_retries_through_the_proxy(downloads, sleeps):  # noqa: F811
    state, run = downloads
    state["error"] = CAPACITY
    budget = proxy.ByteBudget(1000)

    assert run("section", budget).exists()

    assert len(sleeps) == 1
    assert proxy.PROXY_CAPACITY_WAIT_RANGE[0] <= sleeps[0] <= proxy.PROXY_CAPACITY_WAIT_RANGE[1]
    # The account limit is not tied to a session, so the retry keeps it.
    first, retry = state["metadata"]
    assert first["proxy"] == retry["proxy"]
    # The wait does not shorten the retry's own download allowance.
    assert retry["_youtube_deadline"] == pytest.approx(100 + 300 + 60 + sleeps[0])


def test_persistent_capacity_error_gives_up_after_bounded_waits(downloads, sleeps):  # noqa: F811
    state, run = downloads
    state["error"], state["failures"] = CAPACITY, 99
    budget = proxy.ByteBudget(1000)

    with pytest.raises(proxy.ProxyCapacityExhausted):
        run("section", budget)

    assert len(sleeps) == proxy.PROXY_CAPACITY_MAX_WAITS
    assert len(state["metadata"]) == proxy.PROXY_CAPACITY_MAX_WAITS + 1

    # Later operations of the same job share one wait allowance.
    state["metadata"].clear()
    for _ in range(5):
        with pytest.raises(proxy.ProxyCapacityExhausted):
            run("audio", budget)
    assert sum(sleeps) <= proxy.PROXY_CAPACITY_JOB_WAIT_SECONDS


@pytest.mark.asyncio
async def test_section_466_never_reaches_rapidapi_or_apify(downloads, sleeps, chain):  # noqa: F811
    state, _run = downloads
    state["error"], state["failures"] = CAPACITY, 99

    with pytest.raises(proxy.ProxyCapacityExhausted):
        await VideoService.download_video(URL, task_id="task12345678", ranges=[(0, 10)])

    chain.rapidapi.assert_not_called()
    chain.apify.assert_not_called()
    chain.direct.assert_not_called()
    chain.full_proxy.assert_not_called()
    assert 0 < len(sleeps) <= proxy.PROXY_CAPACITY_MAX_WAITS


@pytest.mark.asyncio
async def test_other_section_failures_still_use_the_fallback_chain(downloads, sleeps, chain):  # noqa: F811
    state, _run = downloads
    state["error"], state["failures"] = RuntimeError("ERROR: HTTP Error 404: Not Found"), 99

    assert await VideoService.download_video(URL, task_id="task12345678", ranges=[(0, 10)]) is None

    chain.rapidapi.assert_called_once()
    chain.apify.assert_called_once()
    chain.direct.assert_called_once()
    assert sleeps == []


def test_full_chain_stops_when_the_proxy_is_at_capacity(chain):
    chain.full_proxy.side_effect = proxy.ProxyCapacityExhausted()
    errors = []

    with pytest.raises(proxy.ProxyCapacityExhausted):
        utils.download_youtube_video(URL, task_id="task12345678", errors=errors)

    chain.full_proxy.assert_called_once()
    chain.rapidapi.assert_not_called()
    chain.apify.assert_not_called()
    chain.direct.assert_not_called()
    assert errors == [str(proxy.ProxyCapacityExhausted())]


def test_audio_at_capacity_does_not_start_the_paid_full_download(monkeypatch):
    monkeypatch.setattr(youtube_source, "download_audio_for_job",
                        Mock(side_effect=proxy.ProxyCapacityExhausted()))
    download_full = Mock(return_value=None)

    with pytest.raises(proxy.ProxyCapacityExhausted):
        youtube_source.download_analysis_source(
            URL, VIDEO_ID, task_id="task12345678", duration=120, download_full=download_full,
        )

    download_full.assert_not_called()


@pytest.mark.asyncio
async def test_generation_fails_on_capacity_without_a_full_download(pipeline, monkeypatch):  # noqa: F811
    def sections(*_args, **_kwargs):
        raise proxy.ProxyCapacityExhausted()

    monkeypatch.setattr(video_service, "materialize_clip_source", sections)

    with pytest.raises(proxy.ProxyCapacityExhausted):
        await pipeline.run()

    VideoService.download_video.assert_not_awaited()
