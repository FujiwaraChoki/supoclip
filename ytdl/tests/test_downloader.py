from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError

from supoclip_ytdl import downloader
from supoclip_ytdl.config import Route, Settings
from supoclip_ytdl.downloader import Progress, Result, format_selector
from supoclip_ytdl.errors import Cancelled, DownloadFailed
from supoclip_ytdl.routing import Router


def make_settings(tmp_path, routes, **overrides):
    values = dict(data_dir=tmp_path, routes=routes, player_clients=(None, "mweb"), max_attempts=6)
    values.update(overrides)
    return Settings(**values)


def run(settings, router, attempts):
    calls = []

    def fake_attempt(url, video_id, max_height, max_bytes, settings_, proxy, client, base_format,
                     format_sort, pinned, attempt, progress, work):
        calls.append({"proxy": proxy, "client": client, "pinned": pinned, "route": progress.route})
        outcome = attempts[len(calls) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return Result(path=work / "x.mp4", size=1, title="t", duration=1, width=1, height=1,
                      format_id="136+140", route=progress.route, attempts=0, transferred_bytes=0)

    original = downloader._attempt
    downloader._attempt = fake_attempt
    try:
        return downloader.download("dQw4w9WgXcQ", 720, 10**9, settings, router, Progress(),
                                   settings.work_dir / "job"), calls
    finally:
        downloader._attempt = original


def router_for(settings):
    return Router(settings.routes, settings.circuit_threshold, settings.circuit_cooldown_seconds)


def test_blocked_rotates_to_next_route_with_fresh_session(tmp_path):
    settings = make_settings(tmp_path, (Route("direct", attempts=1),
                                        Route("res", "http://u-{session}:p@gate:1", attempts=3)))
    router = router_for(settings)
    result, calls = run(settings, router, [
        DownloadError("ERROR: Sign in to confirm you’re not a bot"),
        DownloadError("ERROR: HTTP Error 403: Forbidden"),
        None,
    ])
    assert [c["route"] for c in calls] == ["direct", "res", "res"]
    assert calls[0]["proxy"] == "" or calls[0]["proxy"] is None
    assert calls[1]["proxy"] != calls[2]["proxy"]
    # The second block on "res" moves to the next player client group.
    assert [c["client"] for c in calls] == [None, None, "mweb"]
    assert result.attempts == 3 and result.route == "res"
    assert router.stats["direct"].blocked == 1 and router.stats["res"].successes == 1


def test_fatal_error_stops_without_burning_more_exits(tmp_path):
    settings = make_settings(tmp_path, (Route("res", "http://x-{session}", attempts=5),))
    with pytest.raises(DownloadFailed) as info:
        run(settings, router_for(settings), [DownloadError("ERROR: [youtube] x: Private video")])
    assert info.value.failure.code == "private"


def test_gives_up_after_max_attempts_with_last_reason(tmp_path):
    settings = make_settings(tmp_path, (Route("res", "http://x-{session}", attempts=10),), max_attempts=3)
    blocked = DownloadError("ERROR: Sign in to confirm you’re not a bot")
    with pytest.raises(DownloadFailed) as info:
        run(settings, router_for(settings), [blocked, blocked, blocked, None])
    assert info.value.failure.code == "bot_check"


def test_interrupted_download_pins_formats_for_resume(tmp_path):
    settings = make_settings(tmp_path, (Route("res", "http://x-{session}", attempts=3),))
    dropped = DownloadError("ERROR: The read operation timed out")
    dropped._ytdl_pinned = "137+140"
    _, calls = run(settings, router_for(settings), [dropped, None])
    assert [c["pinned"] for c in calls] == [None, "137+140"]


def test_partial_files_survive_a_retry(tmp_path):
    settings = make_settings(tmp_path, (Route("res", "http://x-{session}", attempts=3),))
    work = settings.work_dir / "job"
    work.mkdir(parents=True)
    (work / "dQw4w9WgXcQ.f137.mp4.part").write_bytes(b"x" * 10)
    (work / "dQw4w9WgXcQ.f137.mp4").write_bytes(b"junk")
    run(settings, router_for(settings), [DownloadError("ERROR: timed out"), None])
    assert (work / "dQw4w9WgXcQ.f137.mp4.part").exists()
    assert not (work / "dQw4w9WgXcQ.f137.mp4").exists()


def test_cancel_is_not_retried(tmp_path):
    settings = make_settings(tmp_path, (Route("res", "http://x-{session}", attempts=3),))
    wrapped = DownloadError("ERROR: cancelled", exc_info=(Cancelled, Cancelled(), None))
    with pytest.raises(Cancelled):
        run(settings, router_for(settings), [wrapped, None])


def test_progress_hook_counts_resumed_bytes_once(tmp_path):
    settings = make_settings(tmp_path, (Route("direct"),))
    work = tmp_path / "w"
    work.mkdir()
    (work / "v.f137.mp4.part").write_bytes(b"x" * 100)
    progress = Progress()
    attempt = downloader.Attempt(progress, settings, work, deadline=float("inf"))
    name = str(work / "v.f137.mp4")
    attempt.hook({"status": "downloading", "filename": name, "tmpfilename": name + ".part",
                  "downloaded_bytes": 150})
    attempt.hook({"status": "finished", "filename": name, "downloaded_bytes": 200})
    assert attempt.transferred == 100


def test_format_selector_caps_height_and_prefers_h264():
    fmt, sort = format_selector(720)
    assert fmt.startswith("bv*[height<=720]+ba")
    assert "res:720" in sort and "vcodec:h264" in sort and "proto:https" in sort


def test_stall_window_starts_with_first_bytes_and_trips_on_slow_transfer(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, (Route("direct"),), stall_seconds=10, min_speed_bytes=100)
    clock = [1000.0]
    monkeypatch.setattr(downloader.time, "monotonic", lambda: clock[0])
    attempt = downloader.Attempt(Progress(), settings, tmp_path, deadline=float("inf"))
    clock[0] += 60  # slow extraction must not count against download speed
    name = str(tmp_path / "v.mp4")
    attempt.hook({"status": "downloading", "filename": name, "downloaded_bytes": 10})
    clock[0] += 11
    attempt.hook({"status": "downloading", "filename": name, "downloaded_bytes": 5000})
    clock[0] += 11
    with pytest.raises(downloader.Stalled):
        attempt.hook({"status": "downloading", "filename": name, "downloaded_bytes": 5100})
