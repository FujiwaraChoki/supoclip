"""Regression coverage using real yt-dlp and loopback HTTP, never paid traffic."""
import asyncio
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import threading

import pytest

from src.stream_transfer import (
    LimitedResponse, TransferBudget, TransferStopped, bounded_client,
    run_stream_download, transfer_scope,
)


@pytest.fixture
def hls(tmp_path):
    for i in range(4):
        (tmp_path / f"{i}.ts").write_bytes(bytes([i]) * 1024)
    (tmp_path / "test.m3u8").write_text(
        "#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXT-X-MEDIA-SEQUENCE:0\n"
        + "".join(f"#EXTINF:1,\n{i}.ts\n" for i in range(4))
        + "#EXT-X-ENDLIST\n"
    )
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(tmp_path)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/test.m3u8", tmp_path
    server.shutdown()
    thread.join()
    server.server_close()


def test_real_hls_total_budget_stops_even_when_each_fragment_fits(hls):
    url, root = hls
    budget = TransferBudget(1500)
    with pytest.raises(TransferStopped, match="byte job limit"):
        with transfer_scope(budget):
            with bounded_client({"quiet": True, "outtmpl": str(root / "result.ts"),
                                 "max_filesize": 1500, "fixup": "never",
                                 "external_downloader": {"default": "native"}}) as ydl:
                ydl.process_ie_result({"id": "test", "title": "test", "url": url,
                                       "ext": "ts", "protocol": "m3u8_native"}, download=True)
    assert budget.total == 1501
    with pytest.raises(TransferStopped):
        with transfer_scope(budget):
            pytest.fail("An exhausted budget must not start another provider")


def test_failed_and_retried_responses_share_actual_wire_bytes():
    budget = TransferBudget(10)
    with pytest.raises(TransferStopped):
        with transfer_scope(budget) as transfer:
            assert LimitedResponse(BytesIO(b"123456"), transfer).read() == b"123456"
            LimitedResponse(BytesIO(b"123456"), transfer).read()
    assert budget.total == 11


def test_deadline_is_terminal(monkeypatch):
    from src import stream_transfer
    now = [0.0]
    monkeypatch.setattr(stream_transfer.time, "monotonic", lambda: now[0])
    with pytest.raises(TransferStopped, match="timed out"):
        with transfer_scope(TransferBudget(100), timeout=1) as transfer:
            now[0] = 2.0
            LimitedResponse(BytesIO(b"x"), transfer).read()


@pytest.mark.asyncio
async def test_async_cancellation_stops_running_reader():
    started, stopped = threading.Event(), threading.Event()
    def download():
        try:
            with transfer_scope(TransferBudget(10000000)) as transfer:
                started.set()
                while True:
                    transfer.check()
                    stopped.wait(.01)
        finally:
            stopped.set()
    task = asyncio.create_task(run_stream_download(download))
    await asyncio.to_thread(started.wait, 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_user_cancel_flag_stops_running_reader():
    stopped = threading.Event()
    def download():
        try:
            with transfer_scope(TransferBudget(100)) as transfer:
                while True:
                    transfer.check()
                    stopped.wait(.01)
        finally:
            stopped.set()
    async def cancelled():
        return True
    with pytest.raises(TransferStopped, match="cancelled"):
        await asyncio.wait_for(run_stream_download(download, should_cancel=cancelled), 2)
    assert stopped.is_set()


def test_steady_progress_outlives_the_idle_timeout(monkeypatch):
    from src import stream_transfer
    now = [0.0]
    monkeypatch.setattr(stream_transfer.time, "monotonic", lambda: now[0])
    with transfer_scope(TransferBudget(1000), timeout=5, max_seconds=100) as transfer:
        for _ in range(10):  # 40 seconds in total, never 5 without data.
            now[0] += 4.0
            assert LimitedResponse(BytesIO(b"x"), transfer).read(1) == b"x"


def test_stalled_transfer_times_out(monkeypatch):
    from src import stream_transfer
    now = [0.0]
    monkeypatch.setattr(stream_transfer.time, "monotonic", lambda: now[0])
    with pytest.raises(TransferStopped, match="timed out"):
        with transfer_scope(TransferBudget(1000), timeout=5, max_seconds=100) as transfer:
            now[0] = 4.0
            LimitedResponse(BytesIO(b"x"), transfer).read(1)
            now[0] = 9.5
            transfer.check()


def test_hard_cap_stops_a_steady_transfer(monkeypatch):
    from src import stream_transfer
    now = [0.0]
    monkeypatch.setattr(stream_transfer.time, "monotonic", lambda: now[0])
    with pytest.raises(TransferStopped, match="timed out"):
        with transfer_scope(TransferBudget(1000), timeout=5, max_seconds=20) as transfer:
            for _ in range(10):
                now[0] += 4.0
                LimitedResponse(BytesIO(b"x"), transfer).read(1)
