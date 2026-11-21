"""A full proxy download that clearly cannot fit the job's byte budget is refused before media."""

from contextlib import contextmanager
from pathlib import Path

import pytest
import yt_dlp

from src import youtube_proxy as proxy
from src.config import Config, set_config_override

URL = "https://www.youtube.com/watch?v=abcdefghijk"
VIDEO_ID = "abcdefghijk"
LIMIT = 2_000_000_000
HOURS_9 = 9.4 * 3600


def _info(duration, video, audio):
    base = {"url": "http://127.0.0.1:9/never", "protocol": "m3u8_native"}
    return {
        "id": VIDEO_ID, "title": "long source", "extractor": "youtube", "extractor_key": "Youtube",
        "webpage_url": URL, "duration": duration,
        "formats": [
            dict(base, format_id="v", ext="mp4", vcodec="avc1", acodec="none", height=720, **video),
            dict(base, format_id="a", ext="m4a", vcodec="none", acodec="mp4a", **audio),
        ],
    }


@pytest.fixture
def full_download(tmp_path, monkeypatch):
    config = Config()
    config.youtube_proxy_url = "http://user-session-{session}:secret@proxy.example:8000"
    config.youtube_pot_provider_url = ""
    config.youtube_proxy_timeout_seconds = 300
    set_config_override(config)
    state = {"streamed": 0}

    def run(info, budget, media_bytes):
        @contextmanager
        def client(options):
            class Client:
                def download(self, urls):
                    passed = []
                    match = options["match_filter"]

                    def recording_match(entry, *, incomplete=False):
                        result = match(entry, incomplete=incomplete)
                        if not incomplete:
                            passed.append(result is None)
                        return result

                    # Real yt-dlp format selection decides when the filter runs.
                    with yt_dlp.YoutubeDL({
                        "quiet": True, "simulate": True, "format": options["format"],
                        "match_filter": recording_match,
                    }) as ydl:
                        ydl.process_ie_result(dict(info, formats=[dict(f) for f in info["formats"]]),
                                              download=True)
                    if not any(passed):
                        return 1
                    # Stream the media in 100 MB steps through the job's byte counter.
                    target = Path(options["outtmpl"].replace("%(ext)s", "mp4"))
                    received = 0
                    while received < media_bytes:
                        received = min(media_bytes, received + 100_000_000)
                        state["streamed"] = received
                        options["progress_hooks"][0]({
                            "status": "downloading", "filename": str(target), "downloaded_bytes": received,
                        })
                    target.write_bytes(b"video")
                    return 0
            yield Client()

        monkeypatch.setattr(proxy, "_proxy_client", client)
        return proxy.download_full_with_proxy(URL, VIDEO_ID, tmp_path, task_id="task12345678", budget=budget)

    yield state, run
    set_config_override(None)


def test_clearly_oversized_full_download_is_refused_before_any_media(full_download):
    state, run = full_download
    # ~2.6 Mbit/s for 9.4 h is ~11 GB against a 2 GB job limit.
    info = _info(HOURS_9, {"tbr": 2500}, {"tbr": 130})
    budget = proxy.ByteBudget(LIMIT)
    with pytest.raises(proxy.DownloadBudgetExceeded, match="too large to download in full"):
        run(info, budget, media_bytes=11_000_000_000)
    assert state["streamed"] == 0
    assert budget.snapshot() == 0


def test_exact_size_counts_bytes_the_job_already_spent(full_download):
    state, run = full_download
    info = _info(3600, {"filesize": 500_000_000}, {"filesize": 60_000_000})
    budget = proxy.ByteBudget(LIMIT)
    budget.add(1_600_000_000)  # audio and playlists of this job
    with pytest.raises(proxy.DownloadBudgetExceeded, match="too large to download in full"):
        run(info, budget, media_bytes=560_000_000)
    assert state["streamed"] == 0


@pytest.mark.parametrize("video,audio", [
    ({}, {}),                                    # no size information at all
    ({"tbr": 2500}, {}),                         # one stream has no estimate
    ({"filesize_approx": 2_400_000_000}, {"filesize_approx": 100_000_000}),  # 1.25x: not clearly over
])
def test_uncertain_or_marginal_estimates_keep_the_current_download(full_download, video, audio):
    state, run = full_download
    info = _info(HOURS_9, video, audio)
    budget = proxy.ByteBudget(LIMIT)
    with pytest.raises(proxy.DownloadBudgetExceeded, match="byte job limit"):
        run(info, budget, media_bytes=2_500_000_000)
    assert state["streamed"] > LIMIT


def test_estimate_within_budget_downloads(full_download):
    state, run = full_download
    info = _info(3600, {"tbr": 2500}, {"tbr": 130})
    path = run(info, proxy.ByteBudget(LIMIT), media_bytes=1_200_000_000)
    assert path.read_bytes() == b"video"
    assert state["streamed"] == 1_200_000_000


@pytest.mark.parametrize("info,expected", [
    ({"requested_formats": [{"filesize": 10}, {"filesize": 5}]}, (15, True)),
    ({"requested_formats": [{"filesize": 10}, {"filesize_approx": 5}]}, (15, False)),
    ({"duration": 100, "requested_formats": [{"tbr": 8}]}, (100_000, False)),
    ({"filesize_approx": 7}, (7, False)),
    ({"duration": 100, "requested_formats": [{"tbr": 8}, {}]}, (0, False)),
    ({"requested_formats": [{"filesize": "bad", "tbr": float("nan")}]}, (0, False)),
])
def test_estimate_download_bytes(info, expected):
    assert proxy.estimate_download_bytes(info) == expected


def test_no_job_limit_never_refuses():
    info = {"requested_formats": [{"filesize": 10**12}]}
    assert proxy.full_download_refusal(info, proxy.ByteBudget(0)) is None
