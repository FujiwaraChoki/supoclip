"""The direct yt-dlp full-file fallback stays inside the job's byte budget while it streams."""

import functools
import http.server
import sys
import threading
from pathlib import Path

import pytest
import yt_dlp
from yt_dlp.utils import DownloadError

from src import youtube_utils
from src.config import Config, set_config_override
from src.youtube_proxy import ByteBudget, DownloadBudgetExceeded, _progress_hook

URL = "https://www.youtube.com/watch?v=abcdefghijk"
LIMIT = 2_000_000_000
SECTIONS_SPENT = 1_320_000_000
SOURCE_SIZE = 1_500_000_000
STEP = 50_000_000


@pytest.fixture
def config(tmp_path):
    config = Config()
    config.temp_dir = str(tmp_path)
    config.youtube_download_provider = youtube_utils.YOUTUBE_DOWNLOAD_PROVIDER_YTDLP
    config.youtube_proxy_url = ""
    config.rapidapi_youtube_api_key = ""
    config.apify_api_token = ""
    config.youtube_download_max_bytes = LIMIT
    set_config_override(config)
    yield config
    set_config_override(None)


def _streaming_ytdlp(emitted: list[int], *, wrap: bool = False, size: int = SOURCE_SIZE):
    """Reports progress like yt-dlp's downloader; writes the file only if it finishes."""

    class StreamingYoutubeDL:
        downloads = 0

        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls):
            type(self).downloads += 1
            target = Path(self.opts["outtmpl"].replace("%(ext)s", "mp4"))
            try:
                for done in range(STEP, size + STEP, STEP):
                    emitted.append(min(done, size))
                    for hook in self.opts.get("progress_hooks", []):
                        hook({"status": "downloading", "filename": str(target),
                              "downloaded_bytes": min(done, size)})
            except DownloadBudgetExceeded:
                if wrap:
                    raise DownloadError("ERROR: budget", exc_info=sys.exc_info())
                raise
            target.write_bytes(b"video")

    return StreamingYoutubeDL


@pytest.mark.parametrize("wrap", [False, True])
def test_direct_fallback_stops_at_the_job_limit_while_streaming(config, monkeypatch, tmp_path, wrap):
    # Section downloads already spent most of the job's budget before the full-file fallback.
    budget = ByteBudget(LIMIT)
    budget.add(SECTIONS_SPENT)
    emitted: list[int] = []
    fake = _streaming_ytdlp(emitted, wrap=wrap)
    monkeypatch.setattr(youtube_utils.yt_dlp, "YoutubeDL", fake)
    monkeypatch.setattr(youtube_utils.time, "sleep", lambda _s: pytest.fail("budget errors must not retry"))

    with pytest.raises(DownloadBudgetExceeded):
        youtube_utils.download_youtube_video(URL, budget=budget, skip_proxy=True)

    assert fake.downloads == 1
    # Stopped mid-stream, one progress step past the limit, not after the whole source.
    assert emitted[-1] < SOURCE_SIZE
    assert LIMIT < budget.snapshot() <= LIMIT + STEP
    assert not list(tmp_path.glob("abcdefghijk.mp4"))


def test_direct_fallback_under_the_limit_is_charged_once(config, monkeypatch, tmp_path):
    budget = ByteBudget(LIMIT)
    budget.add(SECTIONS_SPENT)
    size = 600_000_000
    monkeypatch.setattr(youtube_utils.yt_dlp, "YoutubeDL", _streaming_ytdlp([], size=size))
    monkeypatch.setattr(youtube_utils, "_get_local_video_dimensions", lambda _path: (1920, 1080))

    path = youtube_utils.download_youtube_video(URL, budget=budget, skip_proxy=True)

    assert path == tmp_path / "abcdefghijk.mp4"
    # The streamed bytes count; the small file on disk is not added again.
    assert budget.snapshot() == SECTIONS_SPENT + size


def test_real_ytdlp_progress_hook_aborts_the_transfer(tmp_path):
    """Pinned yt-dlp lets the hook's error out instead of finishing the file."""
    served = tmp_path / "served"
    served.mkdir()
    (served / "big.mp4").write_bytes(b"\0" * 30_000_000)
    handler = functools.partial(_QuietHandler, directory=str(served))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    budget = ByteBudget(5_000_000)
    options = {
        "outtmpl": str(tmp_path / "out" / "x.%(ext)s"),
        "quiet": True,
        "noprogress": True,
        "progress_hooks": [_progress_hook(budget, {})],
        "http_chunk_size": 1_048_576,
    }
    try:
        with pytest.raises(Exception) as caught:
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.download([f"http://127.0.0.1:{server.server_address[1]}/big.mp4"])
    finally:
        server.shutdown()
        server.server_close()

    with pytest.raises(DownloadBudgetExceeded):
        youtube_utils._reraise_budget_exceeded(caught.value)
    assert budget.snapshot() < 30_000_000
    assert not (tmp_path / "out" / "x.mp4").exists()


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def handle(self):
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError):
            pass  # the client hung up on purpose
