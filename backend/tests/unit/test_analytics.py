from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from src import analytics, observability


class FakePostHog:
    def __init__(self, error: Exception | None = None):
        self.calls = []
        self.error = error

    def capture(self, event, **kwargs):
        if self.error:
            raise self.error
        self.calls.append((event, kwargs))


class FakeRequest:
    def __init__(self, headers=None, user_id=None):
        self.headers = {key.lower(): value for key, value in (headers or {}).items()}
        self.state = SimpleNamespace(user_id=user_id) if user_id else SimpleNamespace()


@pytest.fixture
def posthog(monkeypatch):
    client = FakePostHog()
    monkeypatch.setattr(analytics, "get_posthog_client", lambda: client)
    return client


def test_capture_is_a_no_op_without_a_token(monkeypatch):
    monkeypatch.delenv("POSTHOG_PROJECT_TOKEN", raising=False)
    monkeypatch.setattr(observability, "_posthog_client", None)
    assert observability.get_posthog_client() is None
    analytics.capture_event("task_created", "user-1", {"workflow": "highlights"})


def test_capture_sends_event_with_user_trace_and_set(posthog):
    observability.set_trace_id("task-123")
    try:
        analytics.capture_event(
            "task_completed",
            "user-1",
            {"workflow": "series", "clip_count": 3, "cache_hit": None},
            set_properties={"plan": "pro"},
        )
    finally:
        observability.clear_trace_id()

    [(event, kwargs)] = posthog.calls
    assert event == "task_completed"
    assert kwargs["distinct_id"] == "user-1"
    # None values are dropped rather than sent as nulls.
    assert kwargs["properties"] == {
        "workflow": "series",
        "clip_count": 3,
        "trace_id": "task-123",
        "$set": {"plan": "pro"},
    }


def test_capture_skips_events_without_a_user(posthog):
    analytics.capture_event("task_created", None, {"workflow": "highlights"})
    analytics.capture_event("task_created", "", {"workflow": "highlights"})
    assert posthog.calls == []


def test_capture_never_raises(monkeypatch):
    monkeypatch.setattr(analytics, "get_posthog_client", lambda: FakePostHog(RuntimeError("down")))
    analytics.capture_event("task_failed", "user-1", {"error_code": "task_error"})

    def broken_client():
        raise RuntimeError("no client")

    monkeypatch.setattr(analytics, "get_posthog_client", broken_client)
    analytics.capture_event("task_failed", "user-1", {"error_code": "task_error"})


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({}, "web"),
        ({"x-supoclip-client": "ios"}, "ios"),
        ({"user-agent": "SupoClip supoclip-ios/12"}, "ios"),
        ({"authorization": "Bearer sk_test"}, "api_key"),
        ({"x-api-key": "sk_test", "user-agent": "python-httpx/0.28"}, "api_key"),
        ({"authorization": "Bearer sk_test", "user-agent": "supoclip-mcp/0.1.0"}, "mcp"),
        # Only API-key traffic can be MCP; a forged user agent alone is web.
        ({"user-agent": "supoclip-mcp/0.1.0"}, "web"),
    ],
)
def test_request_client(headers, expected):
    assert analytics.request_client(FakeRequest(headers)) == expected


@pytest.mark.parametrize(
    ("source_type", "url", "expected"),
    [
        ("youtube", "https://www.youtube.com/watch?v=abcdefghijk", "youtube"),
        ("twitch", "https://www.twitch.tv/videos/1", "twitch"),
        ("kick", "https://kick.com/video/x", "kick"),
        ("video_url", "upload://abc/video.mp4", "upload"),
        ("video_url", "https://cdn.example.com/a.mp4", "video_url"),
        ("something", None, "unknown"),
        (None, None, "unknown"),
    ],
)
def test_source_kind(source_type, url, expected):
    assert analytics.source_kind(source_type, url) == expected


def test_elapsed_seconds():
    start = datetime(2026, 1, 1, 12, 0, 0)
    assert analytics.elapsed_seconds(start, start + timedelta(seconds=90.04)) == 90.0
    assert analytics.elapsed_seconds(None) is None
    assert analytics.elapsed_seconds(start + timedelta(seconds=5), start) == 0.0


def test_clip_export_is_sent_for_the_owner_only(posthog):
    task = {"id": "task-1", "user_id": "owner", "workflow": None}

    analytics.capture_clip_export(FakeRequest(user_id="admin"), task, surface="preset", format="tiktok")
    analytics.capture_clip_export(FakeRequest(), task, surface="preset", format="tiktok")
    analytics.capture_clip_export(FakeRequest(user_id="owner"), None, surface="preset", format="tiktok")
    assert posthog.calls == []

    analytics.capture_clip_export(
        FakeRequest(user_id="owner"), task, surface="zip", format="zip", clip_count=4
    )
    [(event, kwargs)] = posthog.calls
    assert event == "clip_exported"
    assert kwargs["distinct_id"] == "owner"
    assert kwargs["properties"] == {
        "task_id": "task-1",
        "surface": "zip",
        "format": "zip",
        "workflow": "highlights",
        "clip_count": 4,
        "client": "web",
    }
