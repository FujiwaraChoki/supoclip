import pytest

from src.services.video_service import VideoService


@pytest.fixture(autouse=True)
def one_minute_uploads(monkeypatch):
    """Integration tests create tasks from placeholder upload:// references with
    no file behind them; treat each as a one-minute video when it's probed."""
    monkeypatch.setattr(VideoService, "probe_upload_duration", staticmethod(lambda *_args: 60.0))
