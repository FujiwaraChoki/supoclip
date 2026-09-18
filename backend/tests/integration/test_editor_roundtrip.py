"""Real PostgreSQL + ffmpeg path; queue dispatch is executed inline by the test."""

import asyncio
import json
import subprocess
from unittest.mock import AsyncMock

from src.config import get_config
from src.repositories.clip_repository import ClipRepository
from src.services.editor_service import prepare_editor, export_editor, combine_editor
from tests.fixtures.factories import create_user, create_source, create_task


async def test_editor_prepare_save_render_and_combine(
    client, db_session, auth_headers, tmp_path, monkeypatch
):
    config = get_config()
    config.temp_dir = str(tmp_path)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    source_path = uploads / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=24:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=2",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(source_path),
        ],
        capture_output=True,
        check=True,
    )
    user = await create_user(db_session, user_id="user-1")
    source = await create_source(
        db_session, source_type="video_url", url="upload://source.mp4"
    )
    task = await create_task(db_session, user_id=user["id"], source_id=source["id"])
    task_id = task["id"]
    ids = []
    for n in (1, 2):
        ids.append(
            await ClipRepository.create_clip(
                db_session,
                task_id,
                source_path.name,
                str(source_path),
                "00:00",
                "00:02",
                2,
                "Hello world",
                1,
                "test",
                n,
            )
        )
    await ClipRepository.reorder_task_clips(db_session, task_id)
    await db_session.commit()
    monkeypatch.setattr(
        "src.services.task_service.TaskService._load_task_source_settings",
        AsyncMock(return_value={"output_format": "original"}),
    )
    queue = AsyncMock(return_value="queue-job")
    monkeypatch.setattr("src.api.routes.editor.JobQueue.enqueue_job", queue)
    for clip_id in ids:
        endpoint = f"/tasks/{task_id}/clips/{clip_id}/editor"
        assert (await client.get(endpoint, headers=auth_headers)).json()[
            "status"
        ] == "unprepared"
        assert (
            await client.post(endpoint + "/prepare", headers=auth_headers)
        ).status_code == 202
        await prepare_editor({}, task_id, clip_id, source_path.name)
        state = (await client.get(endpoint, headers=auth_headers)).json()
        assert state["status"] == "ready", state
        assert state["width"] == 320 and state["duration"] > 1.9
        media = await client.get(
            endpoint + "/media/clean.mp4",
            headers={**auth_headers, "Range": "bytes=0-99"},
        )
        assert media.status_code == 206 and len(media.content) == 100
    endpoint = f"/tasks/{task_id}/clips/{ids[0]}/editor"
    state = (await client.get(endpoint, headers=auth_headers)).json()
    doc = state["draft"]["document"]
    doc["segments"] = [
        {"id": "a", "start": 0.25, "end": 1.25},
        {"id": "b", "start": 1.5, "end": 1.9},
    ]
    doc["muted"] = True
    payload = {"basis": state["basis"], "revision": 0, "document": doc}
    concurrent = await asyncio.gather(
        *[client.patch(endpoint, json=payload, headers=auth_headers) for _ in range(2)]
    )
    assert sorted(r.status_code for r in concurrent) == [200, 409]
    # Original generated media is untouched after saving.
    original = await ClipRepository.get_clip_by_id(db_session, ids[0])
    assert original["filename"] == source_path.name and original["duration"] == 2
    job = (
        await client.post(
            endpoint + "/exports",
            json={"basis": state["basis"], "document": doc, "preset": "shorts"},
            headers=auth_headers,
        )
    ).json()
    await export_editor({}, task_id, ids[0], source_path.name, job["id"])
    assert (
        await client.get(endpoint + f"/exports/{job['id']}", headers=auth_headers)
    ).json()["status"] == "completed"
    response = await client.get(
        endpoint + f"/exports/{job['id']}/file", headers=auth_headers
    )
    assert (
        response.status_code == 200 and response.headers["content-type"] == "video/mp4"
    )
    rendered = tmp_path / "result.mp4"
    rendered.write_bytes(response.content)
    probe = json.loads(
        subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(rendered),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    assert abs(float(probe["format"]["duration"]) - 1.4) < 0.15
    await db_session.rollback()
    combined = (
        await client.post(
            f"/tasks/{task_id}/editor/combine",
            json={"clip_ids": ids},
            headers=auth_headers,
        )
    ).json()
    await combine_editor({}, task_id, ids[0], source_path.name, combined["id"])
    job = (
        await client.get(endpoint + f"/exports/{combined['id']}", headers=auth_headers)
    ).json()
    assert job["status"] == "completed", job
    saved = await ClipRepository.get_clips_by_task(db_session, task_id)
    assert len(saved) == 3
    assert [c["id"] for c in saved[:2]] == ids
    assert saved[2]["id"] == job["clip_id"]
    combined_state = (
        await client.get(
            f"/tasks/{task_id}/clips/{job['clip_id']}/editor", headers=auth_headers
        )
    ).json()
    assert combined_state["status"] == "ready"
