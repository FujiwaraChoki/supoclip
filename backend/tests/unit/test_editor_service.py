from contextlib import asynccontextmanager
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.editor_document import EditDocument, atomic_json, editor_dir
from src.services.editor_service import combine_editor, prepare_editor, read_state


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

    async def create(_db, *args):
        created.append(args)
        return "combined"

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
    assert state["clip_id"] == "combined"
    assert len(clips) == 2
    assert len(created) == 1
    filename = created[0][1]
    combined = read_state(editor_dir(str(tmp_path), "task", "combined", filename))
    assert combined["status"] == "ready"
    assert [w["text"] for w in combined["draft"]["document"]["words"]] == [
        "clip1",
        "clip2",
    ]
    assert combined["duration"] == pytest.approx(1.6, abs=0.1)
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
