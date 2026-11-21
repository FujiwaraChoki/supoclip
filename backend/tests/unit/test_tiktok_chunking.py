"""TikTok upload chunking follows the Content Posting API media transfer rules.

Every chunk is 5-64 MB except the final one, which takes the trailing bytes
(up to 128 MB); total_chunk_count is floor(video_size / chunk_size); files
under 5 MB go up whole and files over 64 MB must be split. TikTok doesn't say
whether MB means 10^6 or 2^20 bytes, so the checks hold under both readings.
"""

import pytest

from src.config import Config
from src.social.base import PublishRequest
from src.social.tiktok import TikTokProvider

MiB = 1024 * 1024
MIN_CHUNK = 5 * MiB  # the larger reading of "5 MB"
MAX_CHUNK = 64_000_000  # the smaller reading of "64 MB"
MAX_FINAL_CHUNK = 128_000_000


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = ""

    def json(self):
        return self._payload


class RecordingSession:
    """Records the init body and each PUT's headers without keeping the bytes."""

    def __init__(self):
        self.init_body = None
        self.puts = []

    def post(self, url, **kwargs):
        if "creator_info/query" in url:
            return _Response(
                200,
                {"data": {"privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"]}, "error": {"code": "ok"}},
            )
        if "video/init" in url:
            self.init_body = kwargs["json"]
            return _Response(
                200,
                {"data": {"publish_id": "pub", "upload_url": "https://upload.tiktok/x"}, "error": {"code": "ok"}},
            )
        raise AssertionError(f"Unexpected POST {url}")

    def put(self, url, **kwargs):
        headers = kwargs["headers"]
        self.puts.append((headers["Content-Range"], int(headers["Content-Length"]), len(kwargs["data"])))
        return _Response(201 if len(self.puts) == self.init_body["source_info"]["total_chunk_count"] else 206, {})


def _publish(tmp_path, size):
    path = tmp_path / "clip.mp4"
    with open(path, "wb") as handle:
        handle.truncate(size)  # sparse: no real disk use
    config = Config()
    config.tiktok_client_key = "tt-key"
    config.tiktok_client_secret = "tt-secret"
    config.tiktok_app_audited = True
    session = RecordingSession()
    TikTokProvider(config, session=session).publish(
        "token",
        PublishRequest(file_path=str(path), title="Title", caption="", hashtags=[], privacy_level="public"),
    )
    return session


SIZES = [
    4 * MiB,
    5 * MiB,
    64_000_000,
    64_000_001,
    64 * MiB,
    64 * MiB + 1,
    90 * MiB,
    100 * MiB,
    300 * MiB,
    1024 * MiB,
]


@pytest.mark.parametrize("size", SIZES)
def test_tiktok_chunks_follow_media_transfer_rules(tmp_path, size):
    session = _publish(tmp_path, size)
    source = session.init_body["source_info"]
    chunk_size = source["chunk_size"]
    total = source["total_chunk_count"]

    assert source["video_size"] == size
    assert total == size // chunk_size
    assert 1 <= total <= 1000
    if total == 1:
        assert size <= MAX_CHUNK
        assert chunk_size == size
    else:
        assert MIN_CHUNK <= chunk_size <= MAX_CHUNK

    # The PUTs match the init body: one per announced chunk, contiguous,
    # every one but the last exactly chunk_size, the last ending on the final byte.
    assert len(session.puts) == total
    expected_start = 0
    for index, (content_range, content_length, sent) in enumerate(session.puts):
        span, total_bytes = content_range.removeprefix("bytes ").split("/")
        start, end = (int(part) for part in span.split("-"))
        assert int(total_bytes) == size
        assert start == expected_start
        assert content_length == sent == end - start + 1
        if index < total - 1:
            assert sent == chunk_size
        else:
            assert end == size - 1
            assert chunk_size <= sent <= MAX_FINAL_CHUNK
        expected_start = end + 1
    assert expected_start == size


@pytest.mark.parametrize(
    ("size", "chunk_size", "total"),
    [
        (4 * MiB, 4 * MiB, 1),
        (5 * MiB, 5 * MiB, 1),
        (64_000_000, 64_000_000, 1),
        (64 * MiB, 32 * MiB, 2),
        (90 * MiB, 45 * MiB, 2),
        (100 * MiB, 50 * MiB, 2),
        (300 * MiB, 50 * MiB, 6),
        (1024 * MiB, 1024 * MiB // 21, 21),
    ],
)
def test_tiktok_chunk_plan_values(tmp_path, size, chunk_size, total):
    source = _publish(tmp_path, size).init_body["source_info"]
    assert (source["chunk_size"], source["total_chunk_count"]) == (chunk_size, total)
