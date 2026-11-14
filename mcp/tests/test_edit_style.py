import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from supoclip_mcp import server
from supoclip_mcp.client import AuthNotConfiguredError, SupoClipError

URL = "https://youtu.be/abcdefghijk"
JOB = "0123456789abcdef0123456789abcdef"
# Exactly the keys a plain highlights request had before dynamic edits.
LEGACY_BODY_KEYS = [
    "source", "processing_mode", "clip_length", "output_format", "add_subtitles", "caption_template", "include_broll",
    "cut_long_pauses", "remove_filler_words",
]


def _create(monkeypatch, **kwargs):
    request = AsyncMock(return_value={"task_id": "task-1"})
    monkeypatch.setattr(server, "_client", lambda: SimpleNamespace(request=request))
    result = asyncio.run(server.supoclip_create_clip_task(url=URL, **kwargs))
    return request, result


def test_highlights_body_is_unchanged(monkeypatch):
    request, _ = _create(monkeypatch)
    body = request.await_args.kwargs["json_body"]
    assert list(body) == LEGACY_BODY_KEYS
    assert "edit_style" not in body


@pytest.mark.parametrize(
    "kwargs",
    [
        {"intensity": "off"},
        {"edit_style": {"intensity": "off", "music": False}},
        {"edit_style": {}},
        {"intensity": "", "edit_style": None},
        # An animated template with intensity off is "style only": the template carries it.
        {"caption_template": "emoji_drop"},
    ],
)
def test_off_sends_no_edit_style(monkeypatch, kwargs):
    request, _ = _create(monkeypatch, **kwargs)
    body = request.await_args.kwargs["json_body"]
    assert "edit_style" not in body
    assert list(body) == LEGACY_BODY_KEYS


def test_bold_sends_edit_style(monkeypatch):
    request, _ = _create(monkeypatch, intensity="bold")
    body = request.await_args.kwargs["json_body"]
    assert list(body) == [*LEGACY_BODY_KEYS, "edit_style"]
    assert body["edit_style"] == {"intensity": "bold"}


def test_edit_style_passes_through_in_canonical_form(monkeypatch):
    request, _ = _create(
        monkeypatch,
        intensity="max",
        caption_template="behind_head",
        edit_style={"music": False, "censor": "bleep", "sfx": True, "grade": "noir", "intensity": "subtle"},
    )
    body = request.await_args.kwargs["json_body"]
    # Defaults dropped, keys sorted, the intensity argument wins.
    assert body["edit_style"] == {"censor": "bleep", "grade": "noir", "intensity": "max", "music": False}
    assert list(body["edit_style"]) == sorted(body["edit_style"])
    assert body["caption_template"] == "behind_head"


def test_intensity_inside_edit_style_alone(monkeypatch):
    request, _ = _create(monkeypatch, edit_style={"intensity": "subtle", "progress_bar": True})
    assert request.await_args.kwargs["json_body"]["edit_style"] == {"intensity": "subtle", "progress_bar": True}


def test_edit_style_works_with_workflows(monkeypatch):
    request, _ = _create(monkeypatch, workflow="trailer", intensity="bold")
    body = request.await_args.kwargs["json_body"]
    assert body["workflow"] == "trailer"
    assert body["edit_style"] == {"intensity": "bold"}


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"intensity": "nuclear"}, "intensity must be"),
        ({"edit_style": {"intensity": "nuclear"}}, "edit_style.intensity must be one of"),
        ({"edit_style": {"volume": 11}}, "no key 'volume'"),
        ({"edit_style": {"music": "yes"}}, "edit_style.music must be true or false"),
        ({"edit_style": {"censor": "beep"}}, "edit_style.censor must be one of"),
        ({"edit_style": {"music_mood": "sad"}}, "edit_style.music_mood must be one of"),
        ({"edit_style": "bold"}, "edit_style must be an object"),
    ],
)
def test_invalid_edit_style_does_not_create_a_task(monkeypatch, kwargs, message):
    request, result = _create(monkeypatch, **kwargs)
    assert message in result
    request.assert_not_awaited()


def test_list_edit_styles(monkeypatch):
    info = {"enabled": True, "intensities": ["off", "subtle", "bold", "max"], "templates": ["emoji_drop"]}
    request = AsyncMock(return_value=info)
    monkeypatch.setattr(server, "_client", lambda: SimpleNamespace(request=request))
    result = asyncio.run(server.supoclip_list_edit_styles())
    assert request.await_args.args == ("GET", "/tasks/edit-styles")
    assert '"enabled": true' in result


def _templates_client(edit_styles):
    calls = []

    async def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path == "/tasks/edit-styles":
            if isinstance(edit_styles, Exception):
                raise edit_styles
            return edit_styles
        return {"templates": [{"id": "default"}]}

    return calls, SimpleNamespace(request=request)


def test_caption_templates_include_fx_only_when_enabled(monkeypatch):
    calls, client = _templates_client({"enabled": True})
    monkeypatch.setattr(server, "_client", lambda: client)
    asyncio.run(server.supoclip_list_caption_templates())
    assert calls[0][1] == "/tasks/edit-styles"
    assert calls[1] == ("GET", "/caption-templates", {"params": {"include": "fx"}, "authenticated": False})


@pytest.mark.parametrize(
    "edit_styles",
    [
        {"enabled": False},
        {"enabled": "true"},
        {},
        AuthNotConfiguredError("No credentials configured."),
        SupoClipError("Not found (404)"),
    ],
)
def test_caption_templates_stay_legacy_otherwise(monkeypatch, edit_styles):
    calls, client = _templates_client(edit_styles)
    monkeypatch.setattr(server, "_client", lambda: client)
    result = asyncio.run(server.supoclip_list_caption_templates())
    assert calls[-1] == ("GET", "/caption-templates", {"authenticated": False})
    assert "Error" not in result


def test_wait_for_a_clip_job_then_refetches_the_task(monkeypatch):
    states = iter([
        {"status": "running", "kind": "settings", "progress": 0.5},
        {"status": "done", "kind": "settings", "progress": 1.0, "result": {"message": "Task settings updated"}},
    ])
    paths = []

    async def request(method, path, **kwargs):
        paths.append(path)
        if path.endswith(f"/clip-jobs/{JOB}"):
            return next(states)
        return {"id": "task-1", "status": "completed", "clips": []}

    monkeypatch.setattr(server, "_client", lambda: SimpleNamespace(request=request))
    monkeypatch.setattr(server.asyncio, "sleep", AsyncMock())
    result = asyncio.run(server.supoclip_wait_for_task(task_id="task-1", clip_job_id=JOB.upper(), poll_interval_seconds=2))
    assert paths == [f"/tasks/task-1/clip-jobs/{JOB}", f"/tasks/task-1/clip-jobs/{JOB}", "/tasks/task-1"]
    assert '"status": "done"' in result
    assert '"task"' in result


def test_wait_for_a_failed_clip_job(monkeypatch):
    request = AsyncMock(return_value={"status": "failed", "kind": "captions", "progress": 0, "error": {"code": "WORKER_UNAVAILABLE"}})
    monkeypatch.setattr(server, "_client", lambda: SimpleNamespace(request=request))
    result = asyncio.run(server.supoclip_wait_for_task(task_id="task-1", clip_job_id=JOB))
    assert '"status": "failed"' in result
    assert "WORKER_UNAVAILABLE" in result
    request.assert_awaited_once()


def test_wait_rejects_a_bad_clip_job_id(monkeypatch):
    request = AsyncMock()
    monkeypatch.setattr(server, "_client", lambda: SimpleNamespace(request=request))
    result = asyncio.run(server.supoclip_wait_for_task(task_id="task-1", clip_job_id="../../tasks"))
    assert "clip_job_id must be" in result
    request.assert_not_awaited()
