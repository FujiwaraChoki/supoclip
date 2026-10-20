from contextlib import asynccontextmanager
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.editor_document import EditDocument, atomic_json, editor_dir
from src.services.editor_service import (
    combine_editor,
    generated_emphasis,
    initial_caption_style,
    prepare_editor,
    read_state,
)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
async def test_combine_worker_keeps_inputs_and_creates_editable_draft(
    tmp_path, monkeypatch
):
    from src.config import Config

    config = Config()
    config.temp_dir = str(tmp_path)
    monkeypatch.setattr("src.config.get_config", lambda: config)
    clips = [
        {"id": f"clip{i}", "filename": f"clip{i}.mp4", "clip_order": i} for i in (1, 2)
    ]
    entries = []
    for clip in clips:
        directory = editor_dir(str(tmp_path), "task", clip["id"], clip["filename"])
        directory.mkdir(parents=True)
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=blue:size=320x180:rate=30:duration=1",
                "-c:v",
                "libx264",
                str(directory / "clean.mp4"),
            ],
            capture_output=True,
            check=True,
        )
        doc = EditDocument.model_validate(
            {
                "segments": [{"id": "a", "start": 0.1, "end": 0.9}],
                "words": [{"id": "w", "start": 0.2, "end": 0.8, "text": clip["id"]}],
                "framing": {"aspect": "original"},
            }
        )
        entries.append(
            {
                **clip,
                "document": doc.model_dump(),
                "state": {"width": 320, "height": 180, "hasAudio": False},
                "ranges": [[0, 1]],
            }
        )
    directory = editor_dir(str(tmp_path), "task", "clip1", "clip1.mp4")
    job = "b" * 32
    atomic_json(directory / f"request-{job}.json", {"inputs": entries})
    atomic_json(directory / f"job-{job}.json", {"status": "queued"})
    created = []

    async def create(_db, *args, **kwargs):
        created.append(args)
        return kwargs["clip_id"]

    repository = SimpleNamespace(
        get_clips_by_task=AsyncMock(return_value=clips),
        create_clip=create,
        reorder_task_clips=AsyncMock(),
    )
    service = SimpleNamespace(
        clip_repo=repository, _seconds_to_mmss=lambda s: f"00:{int(s):02d}"
    )

    @asynccontextmanager
    async def sessions():
        yield object()

    @asynccontextmanager
    async def transaction(*args):
        yield

    monkeypatch.setattr("src.database.AsyncSessionLocal", sessions)
    monkeypatch.setattr("src.services.task_service.TaskService", lambda _db: service)
    monkeypatch.setattr(
        "src.repositories.edit_transaction.task_edit_transaction", transaction
    )
    await combine_editor({}, "task", "clip1", "clip1.mp4", job)
    state = json.loads((directory / f"job-{job}.json").read_text())
    assert state["status"] == "completed", state
    assert state["clip_id"]
    assert len(clips) == 2
    assert len(created) == 1
    filename = created[0][1]
    combined = read_state(editor_dir(str(tmp_path), "task", state["clip_id"], filename))
    assert combined["status"] == "ready"
    assert [w["text"] for w in combined["draft"]["document"]["words"]] == [
        "clip1",
        "clip2",
    ]
    assert combined["duration"] == pytest.approx(1.6, abs=0.1)
    assert combined["draft"]["document"]["hookTitle"] == ""
    repository.reorder_task_clips.assert_awaited_once()


async def test_prepare_failure_is_recoverable(tmp_path, monkeypatch):
    from src.config import Config

    config = Config()
    config.temp_dir = str(tmp_path)
    monkeypatch.setattr("src.config.get_config", lambda: config)

    @asynccontextmanager
    async def sessions():
        yield object()

    monkeypatch.setattr("src.database.AsyncSessionLocal", sessions)
    service = SimpleNamespace(
        clip_repo=SimpleNamespace(get_clip_by_id=AsyncMock(return_value=None)),
        task_repo=SimpleNamespace(get_task_by_id=AsyncMock(return_value=None)),
    )
    monkeypatch.setattr("src.services.task_service.TaskService", lambda _: service)
    await prepare_editor({}, "task", "missing", "missing.mp4")
    state = read_state(editor_dir(str(tmp_path), "task", "missing", "missing.mp4"))
    assert state["status"] == "failed"


async def test_storage_failure_during_prepare_does_not_leave_preparing(tmp_path, monkeypatch):
    from src.config import Config
    from src.media_storage import MediaStorage
    config = Config()
    config.temp_dir = str(tmp_path)
    monkeypatch.setattr("src.config.get_config", lambda: config)
    directory = editor_dir(str(tmp_path), "task", "clip", "clip.mp4")
    atomic_json(directory / "asset.json", {"status": "preparing"})
    monkeypatch.setattr(MediaStorage, "restore_editor", lambda *a, **k: (_ for _ in ()).throw(OSError("S3 unavailable")))
    monkeypatch.setattr(MediaStorage, "save_editor", lambda *a, **k: None)
    await prepare_editor({}, "task", "clip", "clip.mp4")
    assert read_state(directory)["status"] == "failed"


def test_editor_starts_from_the_generated_caption_template():
    from src.media.captions import get_safe_vertical_position

    style = initial_caption_style(
        {"caption_template": "hormozi", "font_family": None, "font_color": None},
        {},
        True,
        1080,
        1920,
    )
    assert style["karaoke"] is True
    assert style["template"] == "hormozi"
    assert style["uppercase"] is True
    assert style["accent"] == "#00FF66"
    assert style["background"] is False
    assert style["wordsPerLine"] == 3
    assert style["font"] == "THEBOLDFONT"
    # Same pixel size and centre row as the generated 1080x1920 clip.
    assert style["size"] == round(32 * 1080 / 560)
    assert style["y"] * 1920 == get_safe_vertical_position(1920, int(62 * 1.5), 0.74)
    EditDocument.model_validate(
        {"segments": [{"id": "s", "start": 0, "end": 1}], "captions": style}
    )

    minimal = initial_caption_style(
        {"caption_template": "minimal", "font_size": 24}, {"position_y": 0.5}, False, 1080, 1920
    )
    assert minimal["karaoke"] is False
    assert minimal["background"] is True
    assert minimal["enabled"] is False
    assert minimal["y"] < 0.5  # centred, not top-aligned, like the generated clip


def test_editor_keeps_generated_font_and_size_extremes():
    tiktok = initial_caption_style({"caption_template": "tiktok"}, {}, True, 1080, 1920)
    assert tiktok["font"] == "TikTokSans-Bold"

    largest = initial_caption_style({"font_size": 72}, {}, True, 1080, 1920)
    assert largest["size"] == 132

    # "original" clips keep the source size; the editor re-expresses it per 1080 px.
    landscape = initial_caption_style({"font_size": 24}, {}, True, 1920, 1080)
    assert landscape["size"] == round(round(24 * 1920 / 560) * 1080 / 1920)
    for style in (tiktok, largest, landscape):
        EditDocument.model_validate(
            {"segments": [{"id": "s", "start": 0, "end": 1}], "captions": style}
        )


def test_editor_seeds_the_generated_emphasis_as_highlights():
    words = [{"text": t, "start": i, "end": i + 0.5} for i, t in enumerate("this changed everything for 90% of people".split())]
    emphasis = generated_emphasis(words, {"caption_template": "default"})
    assert {words[i]["text"] for i in emphasis} >= {"90%"}
    assert "of" not in {words[i]["text"] for i in emphasis}
