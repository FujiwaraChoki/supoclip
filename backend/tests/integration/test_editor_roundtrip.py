"""Real PostgreSQL + ffmpeg path; queue dispatch is executed inline by the test."""

import asyncio
import json
import shutil
import subprocess
from unittest.mock import AsyncMock

from src.config import get_config
from src.clip_source_map import save_clip_broll
from src.editor_document import EditDocument, atomic_json, editor_dir
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


async def test_combine_migrates_legacy_broll_and_preserves_cutaway_continuations(
    client, db_session, auth_headers, tmp_path, monkeypatch
):
    config = get_config()
    config.temp_dir = str(tmp_path)
    main, footage = tmp_path / "red.mp4", tmp_path / "blue.mp4"
    for path, color in ((main, "red"), (footage, "blue")):
        subprocess.run([
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
            f"color=c={color}:s=320x180:r=24:d=6", "-c:v", "libx264", str(path),
        ], check=True, capture_output=True)
    user = await create_user(db_session, user_id="user-1")
    source = await create_source(db_session, source_type="video_url", url="upload://red.mp4")
    task = await create_task(db_session, user_id=user["id"], source_id=source["id"])
    stock = {"id": 42, "width": 320, "height": 180, "duration": 6, "fileUrl": "https://videos.pexels.com/42.mp4"}
    ids, filenames = [], []
    for n in (1, 2):
        path = tmp_path / f"input{n}.mp4"
        shutil.copy2(main, path)
        clip_id = await ClipRepository.create_clip(
            db_session, task["id"], path.name, str(path), "00:00", "00:06", 6,
            "Test clip", 1, "test", n,
        )
        ids.append(clip_id)
        filenames.append(path.name)
        directory = editor_dir(str(tmp_path), task["id"], clip_id, path.name)
        directory.mkdir(parents=True, exist_ok=True)
        shutil.copy2(main, directory / "clean.mp4")
        document = EditDocument.model_validate({
            "segments": ([{"id": "a", "start": 0, "end": 2}, {"id": "b", "start": 3, "end": 6}]
                         if n == 1 else [{"id": "s", "start": 0, "end": 3}]),
            "captions": {"enabled": False}, "framing": {"aspect": "original"},
            "broll": [{"id": "cutaway", "start": 1, "end": 4, "footage": stock}],
        }).model_dump()
        if n == 2:
            document.pop("broll")  # A draft prepared before editable B-roll existed.
            save_clip_broll(path, [{
                "start": 1, "duration": 1, "pexels_id": 42, "width": 320, "height": 180,
                "footage_duration": 6, "file_url": stock["fileUrl"],
            }])
        atomic_json(directory / "draft.json", {"revision": 0, "document": document})
        atomic_json(directory / "original.json", document)
        atomic_json(directory / "asset.json", {
            "status": "ready", "duration": 6, "width": 320, "height": 180,
            "fps": 24, "hasAudio": False, "waveform": [],
        })
    await ClipRepository.reorder_task_clips(db_session, task["id"])
    await db_session.commit()
    monkeypatch.setattr("src.api.routes.editor.JobQueue.enqueue_job", AsyncMock(return_value="job"))
    monkeypatch.setattr("src.broll.ensure_footage_file", AsyncMock(return_value=footage))

    response = await client.post(
        f"/tasks/{task['id']}/editor/combine", headers=auth_headers, json={"clip_ids": ids},
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    await combine_editor({}, task["id"], ids[0], filenames[0], job_id)
    endpoint = f"/tasks/{task['id']}/clips/{ids[0]}/editor"
    job = (await client.get(f"{endpoint}/exports/{job_id}", headers=auth_headers)).json()
    assert job["status"] == "completed", job
    reopened = (await client.get(
        f"/tasks/{task['id']}/clips/{job['clip_id']}/editor", headers=auth_headers,
    )).json()
    cutaways = reopened["draft"]["document"]["broll"]
    assert [(b["start"], b["end"], b["fadeIn"], b["fadeOut"]) for b in cutaways] == [
        (1, 2, True, False), (2, 3, False, True), (6, 7, True, True),
    ]
    assert [b["fadeDuration"] for b in cutaways] == [0.25, 0.25, 0.25]
    rendered = editor_dir(str(tmp_path), task["id"], ids[0], filenames[0]) / f"export-{job_id}.mp4"
    # At the internal cut and at the legacy input's cutaway, the speaker stays covered.
    for at in (1.96, 2.0, 2.08, 6.5):
        pixel = subprocess.check_output([
            "ffmpeg", "-v", "error", "-ss", str(at), "-i", str(rendered), "-frames:v", "1",
            "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ])
        assert pixel[2] > 200 and pixel[0] < 60, (at, list(pixel))
