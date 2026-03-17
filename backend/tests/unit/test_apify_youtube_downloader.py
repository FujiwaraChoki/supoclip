import pytest

from src.apify_youtube_downloader import (
    ApifyDownloadError,
    download_video_via_apify,
    normalize_apify_quality,
)


class _FakeResponse:
    def __init__(self, chunks=None, headers=None):
        self._chunks = chunks or [b"video-bytes"]
        self.headers = headers or {"Content-Type": "video/mp4"}

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=0):
        del chunk_size
        yield from self._chunks


def test_download_video_via_apify_saves_file(tmp_path, monkeypatch):
    actor_calls = {}

    class FakeActor:
        def call(self, run_input):
            actor_calls["run_input"] = run_input
            return {"defaultDatasetId": "dataset-1"}

    class FakeDataset:
        def iterate_items(self):
            yield {"downloadUrl": "https://cdn.example.com/video.mp4"}

    class FakeClient:
        def __init__(self, token):
            actor_calls["token"] = token

        def actor(self, actor_id):
            actor_calls["actor_id"] = actor_id
            return FakeActor()

        def dataset(self, dataset_id):
            actor_calls["dataset_id"] = dataset_id
            return FakeDataset()

    monkeypatch.setattr("src.apify_youtube_downloader.ApifyClient", FakeClient)
    monkeypatch.setattr(
        "src.apify_youtube_downloader.requests.get",
        lambda *args, **kwargs: _FakeResponse(),
    )

    path = download_video_via_apify(
        url="https://www.youtube.com/watch?v=abcdefghijk",
        video_id="abcdefghijk",
        temp_dir=tmp_path,
        api_token="apify-token",
        quality="720",
    )

    assert path == tmp_path / "abcdefghijk.mp4"
    assert path.read_bytes() == b"video-bytes"
    assert actor_calls["token"] == "apify-token"
    assert actor_calls["actor_id"] == "epctex/youtube-video-downloader"
    assert actor_calls["dataset_id"] == "dataset-1"
    assert actor_calls["run_input"] == {
        "startUrls": ["https://www.youtube.com/watch?v=abcdefghijk"],
        "quality": "720",
        "proxy": {"useApifyProxy": True},
    }


def test_download_video_via_apify_raises_when_dataset_is_empty(tmp_path, monkeypatch):
    class FakeActor:
        def call(self, run_input):
            del run_input
            return {"defaultDatasetId": "dataset-1"}

    class FakeDataset:
        def iterate_items(self):
            yield from []

    class FakeClient:
        def __init__(self, token):
            del token

        def actor(self, actor_id):
            del actor_id
            return FakeActor()

        def dataset(self, dataset_id):
            del dataset_id
            return FakeDataset()

    monkeypatch.setattr("src.apify_youtube_downloader.ApifyClient", FakeClient)

    with pytest.raises(ApifyDownloadError, match="no dataset items"):
        download_video_via_apify(
            url="https://www.youtube.com/watch?v=abcdefghijk",
            video_id="abcdefghijk",
            temp_dir=tmp_path,
            api_token="apify-token",
        )


def test_download_video_via_apify_raises_when_download_url_missing(tmp_path, monkeypatch):
    class FakeActor:
        def call(self, run_input):
            del run_input
            return {"defaultDatasetId": "dataset-1"}

    class FakeDataset:
        def iterate_items(self):
            yield {"title": "No download URL here"}

    class FakeClient:
        def __init__(self, token):
            del token

        def actor(self, actor_id):
            del actor_id
            return FakeActor()

        def dataset(self, dataset_id):
            del dataset_id
            return FakeDataset()

    monkeypatch.setattr("src.apify_youtube_downloader.ApifyClient", FakeClient)

    with pytest.raises(ApifyDownloadError, match="download URL"):
        download_video_via_apify(
            url="https://www.youtube.com/watch?v=abcdefghijk",
            video_id="abcdefghijk",
            temp_dir=tmp_path,
            api_token="apify-token",
        )


def test_download_video_via_apify_wraps_actor_exception(tmp_path, monkeypatch):
    class FakeActor:
        def call(self, run_input):
            del run_input
            raise RuntimeError("actor exploded")

    class FakeClient:
        def __init__(self, token):
            del token

        def actor(self, actor_id):
            del actor_id
            return FakeActor()

    monkeypatch.setattr("src.apify_youtube_downloader.ApifyClient", FakeClient)

    with pytest.raises(ApifyDownloadError, match="actor exploded"):
        download_video_via_apify(
            url="https://www.youtube.com/watch?v=abcdefghijk",
            video_id="abcdefghijk",
            temp_dir=tmp_path,
            api_token="apify-token",
        )


def test_normalize_apify_quality_defaults_for_invalid_values():
    assert normalize_apify_quality("360") == "360"
    assert normalize_apify_quality("bad-value") == "1080"
    assert normalize_apify_quality("") == "1080"
    assert normalize_apify_quality(None) == "1080"
