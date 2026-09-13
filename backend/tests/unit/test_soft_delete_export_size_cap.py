"""
Tests for:
  - task soft-delete -> restore -> purge lifecycle (TaskService + TaskRepository, real DB)
  - GET /export-presets returning all 6 presets with expected metadata fields
  - video_utils.enforce_size_cap behavior on a synthetic file (ffmpeg/ffprobe mocked)
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text

from src import video_utils
from src.clip_editor import EXPORT_PRESETS
from tests.fixtures.factories import create_clip, create_source, create_task


# --- soft delete -> restore -> purge lifecycle (real DB, over the HTTP API) ---


@pytest.mark.asyncio
async def test_soft_delete_restore_purge_lifecycle(client, db_session):
    # This deployment runs local-first (no REQUIRE_AUTH): every request resolves
    # to the implicit "local" user regardless of headers, so tasks are owned by it.
    source = await create_source(db_session, title="Trash test video")
    task = await create_task(
        db_session, user_id="local", source_id=source["id"], status="completed"
    )
    task_id = task["id"]

    # Freshly created task shows up in the normal listing, not in trash.
    list_response = await client.get("/tasks/")
    assert task_id in [t["id"] for t in list_response.json()["tasks"]]
    trash_response = await client.get("/tasks/trash")
    assert task_id not in [t["id"] for t in trash_response.json()["tasks"]]

    # DELETE now soft-deletes: task moves to trash, disappears from the normal listing.
    delete_response = await client.delete(f"/tasks/{task_id}")
    assert delete_response.status_code == 200

    list_response = await client.get("/tasks/")
    assert task_id not in [t["id"] for t in list_response.json()["tasks"]]
    trash_response = await client.get("/tasks/trash")
    trashed_ids = [t["id"] for t in trash_response.json()["tasks"]]
    assert task_id in trashed_ids
    trashed_entry = next(t for t in trash_response.json()["tasks"] if t["id"] == task_id)
    assert trashed_entry["deleted_at"] is not None

    # Restore brings it back.
    restore_response = await client.post(f"/tasks/{task_id}/restore")
    assert restore_response.status_code == 200

    list_response = await client.get("/tasks/")
    assert task_id in [t["id"] for t in list_response.json()["tasks"]]
    trash_response = await client.get("/tasks/trash")
    assert task_id not in [t["id"] for t in trash_response.json()["tasks"]]

    # Restoring an already-active (non-trashed) task fails.
    restore_again_response = await client.post(f"/tasks/{task_id}/restore")
    assert restore_again_response.status_code == 400

    # Soft delete again, then purge: the task must be permanently gone.
    await client.delete(f"/tasks/{task_id}")
    purge_response = await client.delete(f"/tasks/{task_id}/purge")
    assert purge_response.status_code == 200

    trash_response = await client.get("/tasks/trash")
    assert task_id not in [t["id"] for t in trash_response.json()["tasks"]]
    get_response = await client.get(f"/tasks/{task_id}")
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_purge_task_best_effort_removes_clip_files(client, db_session, tmp_path):
    source = await create_source(db_session, title="Purge test video")
    task = await create_task(
        db_session, user_id="local", source_id=source["id"], status="completed"
    )
    task_id = task["id"]

    clip_file = tmp_path / "clip.mp4"
    clip_file.write_bytes(b"fake clip bytes")
    await create_clip(db_session, task_id=task_id, text_value="hello")
    await db_session.execute(
        text(
            "UPDATE generated_clips SET file_path = :path WHERE task_id = :task_id"
        ),
        {"path": str(clip_file), "task_id": task_id},
    )
    await db_session.commit()

    await client.delete(f"/tasks/{task_id}")
    purge_response = await client.delete(f"/tasks/{task_id}/purge")
    assert purge_response.status_code == 200

    assert not clip_file.exists()
    get_response = await client.get(f"/tasks/{task_id}")
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_purge_task_missing_clip_file_does_not_block_purge(client, db_session, tmp_path):
    source = await create_source(db_session, title="Purge missing-file test video")
    task = await create_task(
        db_session, user_id="local", source_id=source["id"], status="completed"
    )
    task_id = task["id"]

    missing_file = tmp_path / "already_gone.mp4"  # never created on disk
    await create_clip(db_session, task_id=task_id, text_value="world")
    await db_session.execute(
        text(
            "UPDATE generated_clips SET file_path = :path WHERE task_id = :task_id"
        ),
        {"path": str(missing_file), "task_id": task_id},
    )
    await db_session.commit()

    await client.delete(f"/tasks/{task_id}")
    # Must not raise/500 even though the referenced file never existed.
    purge_response = await client.delete(f"/tasks/{task_id}/purge")
    assert purge_response.status_code == 200


# --- GET /export-presets ---


@pytest.mark.asyncio
async def test_export_presets_endpoint_returns_all_presets(client):
    response = await client.get("/export-presets")
    assert response.status_code == 200
    body = response.json()
    presets = body["presets"]

    assert [p["name"] for p in presets] == list(EXPORT_PRESETS.keys())
    assert len(presets) == 6
    assert {"tiktok", "reels", "shorts", "youtube_shorts", "facebook_reels", "threads"} == {
        p["name"] for p in presets
    }

    expected_fields = {
        "name",
        "width",
        "height",
        "video_bitrate",
        "audio_bitrate",
        "max_duration_seconds",
        "safe_area_top_pct",
        "safe_area_bottom_pct",
        "target_lufs",
    }
    for preset in presets:
        assert expected_fields <= set(preset.keys())
        assert preset["width"] == 1080
        assert preset["height"] == 1920
        assert preset["max_duration_seconds"] > 0
        assert preset["target_lufs"] < 0

    facebook = next(p for p in presets if p["name"] == "facebook_reels")
    assert facebook["target_lufs"] == -16.0
    tiktok = next(p for p in presets if p["name"] == "tiktok")
    assert tiktok["target_lufs"] == -14.0


# --- enforce_size_cap ---


def test_enforce_size_cap_leaves_small_file_untouched(tmp_path, monkeypatch):
    small_file = tmp_path / "small.mp4"
    small_file.write_bytes(b"x" * 1024)  # 1KB, well under any cap

    def fail_if_called(*args, **kwargs):
        raise AssertionError("ffprobe/ffmpeg should not be invoked for a file under the cap")

    monkeypatch.setattr(video_utils, "ffprobe_duration", fail_if_called)
    monkeypatch.setattr(video_utils, "run_ffmpeg_command", fail_if_called)

    changed = video_utils.enforce_size_cap(small_file, target_bytes=10 * 1024)
    assert changed is False
    assert small_file.read_bytes() == b"x" * 1024


def test_enforce_size_cap_reencodes_oversized_file(tmp_path, monkeypatch):
    big_file = tmp_path / "big.mp4"
    big_file.write_bytes(b"x" * (2 * 1024 * 1024))  # 2MB
    target_bytes = 1 * 1024 * 1024  # 1MB cap -> file is over the cap

    monkeypatch.setattr(video_utils, "ffprobe_duration", lambda _path: 30.0)
    monkeypatch.setattr(video_utils, "ffprobe_has_audio", lambda _path: True)

    calls = []

    def fake_run_ffmpeg_command(command, timeout=900):
        calls.append(command)
        # Pass 2 writes the output file (last positional arg before the flags,
        # actually the very last element of the command list).
        if "-pass" in command and command[command.index("-pass") + 1] == "2":
            output_path = Path(command[-1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"y" * (512 * 1024))  # smaller re-encoded output
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(video_utils, "run_ffmpeg_command", fake_run_ffmpeg_command)

    changed = video_utils.enforce_size_cap(big_file, target_bytes=target_bytes)

    assert changed is True
    assert big_file.exists()
    assert big_file.stat().st_size == 512 * 1024
    # Two-pass encode: both passes were invoked.
    assert len(calls) == 2
    assert any("-pass" in c and c[c.index("-pass") + 1] == "1" for c in calls)
    assert any("-pass" in c and c[c.index("-pass") + 1] == "2" for c in calls)
    # Computed bitrate respects the quality floor.
    bitrate_index = calls[0].index("-b:v") + 1
    assert int(calls[0][bitrate_index]) >= video_utils.MIN_VIDEO_BITRATE_BPS


def test_enforce_size_cap_returns_false_when_pass1_fails(tmp_path, monkeypatch):
    big_file = tmp_path / "big.mp4"
    original_bytes = b"x" * (2 * 1024 * 1024)
    big_file.write_bytes(original_bytes)

    monkeypatch.setattr(video_utils, "ffprobe_duration", lambda _path: 30.0)
    monkeypatch.setattr(video_utils, "ffprobe_has_audio", lambda _path: True)
    monkeypatch.setattr(
        video_utils,
        "run_ffmpeg_command",
        lambda command, timeout=900: MagicMock(returncode=1, stdout="", stderr="boom"),
    )

    changed = video_utils.enforce_size_cap(big_file, target_bytes=1024 * 1024)

    assert changed is False
    # Original file must be left untouched on failure.
    assert big_file.read_bytes() == original_bytes
