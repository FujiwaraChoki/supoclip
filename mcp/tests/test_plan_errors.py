"""Backend billing/permission errors come back to the model as plain guidance."""

import asyncio
import json
from pathlib import Path
import sys

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from supoclip_mcp import server
from supoclip_mcp.client import SupoClipClient, _error_message
from supoclip_mcp.config import Settings

APP = "https://www.supoclip.test"


def _body(detail):
    return json.dumps({"detail": detail})


FREE_402 = {
    "code": "SUBSCRIPTION_REQUIRED",
    "message": "Choose a paid plan to process videos.",
    "billing": {"plan": "free", "minute_limit": 0, "remaining_minutes": 0, "reason": "Choose a paid plan to process videos."},
}


def test_free_account_gets_the_upgrade_link():
    message = _error_message(402, _body(FREE_402), "/tasks/", APP)
    assert "no paid plan" in message
    assert f"{APP}/settings#plan" in message
    assert "billing" not in message  # no raw payload dump


def test_lapsed_subscription_counts_as_no_plan():
    detail = {**FREE_402, "billing": {**FREE_402["billing"], "plan": "pro"}}
    assert "no paid plan" in _error_message(402, _body(detail), "/tasks/", APP)


def test_out_of_minutes_says_how_many_and_when_they_reset():
    detail = {
        "code": "SUBSCRIPTION_REQUIRED",
        "message": "You've used this month's video minutes.",
        "billing": {"plan": "pro", "minute_limit": 600, "usage_minutes": 600, "remaining_minutes": 0,
                    "period_end": "2026-12-01T00:00:00+00:00"},
    }
    message = _error_message(402, _body(detail), "/tasks/", APP)
    assert message.startswith("You've used this month's video minutes.")
    assert "Used 600 of 600 minutes" in message
    assert "2026-12-01" in message
    assert f"{APP}/settings#plan" in message


def test_video_longer_than_remaining_minutes_keeps_the_backend_reason():
    detail = {
        "code": "SUBSCRIPTION_REQUIRED",
        "message": "This video is 42 minutes long and you have 10 minutes left this month.",
        "billing": {"plan": "pro", "minute_limit": 600, "usage_minutes": 590, "remaining_minutes": 10},
    }
    message = _error_message(402, _body(detail), "/tasks/", APP)
    assert "42 minutes long" in message
    assert "no paid plan" not in message


def test_structured_errors_show_their_message():
    detail = {"code": "WORKFLOW_NOT_AVAILABLE", "message": "Workflows aren't available on your account yet."}
    message = _error_message(403, _body(detail), "/tasks/", APP)
    assert message == "Permission denied (403): Workflows aren't available on your account yet. (WORKFLOW_NOT_AVAILABLE)"
    too_long = {"code": "VIDEO_TOO_LONG", "message": "This video is too long."}
    assert "This video is too long. (VIDEO_TOO_LONG)" in _error_message(422, _body(too_long), "/tasks/", APP)


def test_paid_only_403_links_to_plans():
    message = _error_message(403, _body("Custom font uploads are available for paid plans only"), "/fonts/upload", APP)
    assert f"{APP}/settings#plan" in message


def test_create_task_tool_returns_the_plain_message(monkeypatch):
    def handler(request):
        if request.url.path == "/tasks/":
            return httpx.Response(402, json={"detail": FREE_402})
        return httpx.Response(404)

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    client = SupoClipClient(Settings(
        api_url="https://api.supoclip.test", api_key="sk_free_user", user_id=None, auth_secret=None,
        download_dir="downloads", timeout=5, mcp_transport="stdio", mcp_host="127.0.0.1", mcp_port=9100,
        mcp_mount_path="/", mcp_public_url=None, mcp_require_bearer_auth=False, app_url=APP,
    ))
    monkeypatch.setattr(server, "_client", lambda: client)
    result = asyncio.run(server.supoclip_create_clip_task(url="https://youtu.be/abcdefghijk"))
    assert result.startswith("Error: This SupoClip account has no paid plan")
    assert f"{APP}/settings#plan" in result
