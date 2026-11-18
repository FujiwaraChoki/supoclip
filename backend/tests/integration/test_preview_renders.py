"""Preview clips and their on-demand full-quality renders, against PostgreSQL.

The render job runs for real (claim, fetch inputs, swap, cleanup outbox) with
local storage; ffmpeg is replaced by a stub that writes a marker file, except
in the end-to-end test that renders real media.
"""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.config import get_config
from src.media_storage import MediaStorage
from src.repositories.clip_repository import ClipRepository
from src.services import full_render_service
from src.services.full_render_service import (
    FULL_RENDER_JOB_NAME,
    FULL_RENDER_QUEUE,
    render_full_clip,
)
from tests.fixtures.factories import create_source, create_task, create_user

FULL_BYTES = b"full-quality-mp4"
PREVIEW_BYTES = b"preview-mp4"


@pytest.fixture
def local_media(app, tmp_path, monkeypatch):
    config = get_config()
    monkeypatch.setattr(config, "media_storage", "local")
    monkeypatch.setattr(config, "temp_dir", str(tmp_path))
    assert MediaStorage().enabled is False
    return tmp_path


def write_inputs(directory: Path, *, max_short_side=None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "render-source.bin").write_bytes(b"stitched-source")
    (directory / "render.json").write_text(json.dumps({
        "version": 1, "output_format": "vertical", "vertical_plan": None, "speaker_plan": None,
        "speaker_turns": [], "speaker_words": [], "fonts_family": None, "broll_applied": False,
        "captions": False, "max_short_side": max_short_side,
    }))
    return directory / "render.json"


async def seed_preview(db_session, root: Path, *, user_id="user-1", clips=1, task_id=None):
    """A completed task whose clips are previews with render inputs on disk."""
    if task_id is None:
        user = await create_user(db_session, user_id=user_id, email=f"{user_id}@example.com")
        source = await create_source(db_session, title="Preview source")
        task = await create_task(db_session, user_id=user["id"], source_id=source["id"])
        task_id = task["id"]
    clip_ids = []
    for index in range(clips):
        clip_id = str(uuid4())
        preview = root / "clips" / f"clip_{index + 1}_{uuid4().hex[:6]}.mp4"
        preview.parent.mkdir(parents=True, exist_ok=True)
        preview.write_bytes(PREVIEW_BYTES)
        recipe = write_inputs(root / "render-inputs" / task_id / uuid4().hex)
        await db_session.execute(
            text(
                """
                INSERT INTO generated_clips (
                    id, task_id, filename, file_path, start_time, end_time, duration, text,
                    relevance_score, reasoning, clip_order, render_quality, render_inputs_path
                ) VALUES (
                    :id, :task_id, :name, :path, '00:00', '00:10', 10, 'Preview clip',
                    0.9, 'Preview clip', :order, 'preview', :inputs
                )
                """
            ),
            {"id": clip_id, "task_id": task_id, "path": str(preview), "name": preview.name,
             "order": index + 1, "inputs": str(recipe)},
        )
        clip_ids.append(clip_id)
    await db_session.commit()
    return task_id, clip_ids


def fake_ffmpeg(monkeypatch, calls=None, *, fail=False):
    def render(inputs_dir, output_path, broll_overlays=None):
        if calls is not None:
            calls.append({"inputs": Path(inputs_dir), "output": Path(output_path)})
        if fail:
            raise RuntimeError("ffmpeg exploded")
        Path(output_path).write_bytes(FULL_BYTES)

    monkeypatch.setattr(full_render_service, "render_full_clip_file", render)


def worker_queue(monkeypatch, app, *, run=True):
    """Record jobs; with ``run`` the job executes right away, like an idle worker."""
    jobs = []

    async def enqueue(cls, function_name, *args, **kwargs):
        jobs.append({"function_name": function_name, "args": args, "kwargs": kwargs})
        if run and function_name == FULL_RENDER_JOB_NAME:
            await render_full_clip({}, *args)
        return "job"

    monkeypatch.setattr(app.state.queue_adapter, "enqueue_job", classmethod(enqueue))
    return jobs


async def clip_row(db_session, clip_id):
    await db_session.rollback()
    return await ClipRepository.get_clip_by_id(db_session, clip_id)


async def pending_deletions(db_session):
    await db_session.rollback()
    rows = await db_session.execute(text("SELECT file_path, is_prefix FROM media_deletions"))
    return {(row.file_path, row.is_prefix) for row in rows}


async def test_full_render_replaces_the_preview_and_is_cached(
    client, app, db_session, auth_headers, local_media, monkeypatch
):
    calls = []
    fake_ffmpeg(monkeypatch, calls)
    jobs = worker_queue(monkeypatch, app)
    task_id, [clip_id] = await seed_preview(db_session, local_media)
    before = await clip_row(db_session, clip_id)
    assert before["render_quality"] == "preview"
    assert before["full_render"] == {"status": "none", "progress": 0, "error": None}

    started = await client.post(
        f"/tasks/{task_id}/clips/{clip_id}/full-render", headers=auth_headers, json={"trigger": "download"}
    )
    assert started.status_code == 202
    assert started.json() == {
        "clip_id": clip_id,
        "render_quality": "full",
        "full_render": {"status": "ready", "progress": 100, "error": None},
    }
    assert [job["kwargs"]["_queue_name"] for job in jobs] == [FULL_RENDER_QUEUE]
    assert calls[0]["inputs"] == Path(before["render_inputs_path"]).parent

    after = await clip_row(db_session, clip_id)
    assert after["render_quality"] == "full"
    assert after["render_inputs_path"] is None
    assert after["filename"] == before["filename"]
    full_path = Path(after["file_path"])
    assert full_path.read_bytes() == FULL_BYTES
    assert full_path.parent == local_media / "clips"
    # The preview and its inputs go through the deletion outbox.
    deletions = await pending_deletions(db_session)
    assert (before["file_path"], False) in deletions
    assert (str(Path(before["render_inputs_path"]).parent), True) in deletions

    # A second download is served from the stored full render.
    download = await client.get(f"/tasks/{task_id}/clips/{clip_id}/file?download=true", headers=auth_headers)
    assert download.status_code == 200
    assert download.content == FULL_BYTES
    again = await client.post(f"/tasks/{task_id}/clips/{clip_id}/full-render", headers=auth_headers)
    assert again.json()["full_render"]["status"] == "ready"
    assert len(jobs) == 1


async def test_web_playback_serves_the_preview_and_a_download_waits_for_full_quality(
    client, app, db_session, auth_headers, local_media, monkeypatch
):
    fake_ffmpeg(monkeypatch)
    jobs = worker_queue(monkeypatch, app)
    task_id, [clip_id] = await seed_preview(db_session, local_media)
    url = f"/tasks/{task_id}/clips/{clip_id}/file"

    inline = await client.get(url, headers=auth_headers)
    assert inline.status_code == 200
    assert inline.content == PREVIEW_BYTES
    assert jobs == []

    download = await client.get(f"{url}?download=true", headers=auth_headers)
    assert download.status_code == 200
    assert download.content == FULL_BYTES
    assert "attachment" in download.headers["content-disposition"]
    assert [job["function_name"] for job in jobs] == [FULL_RENDER_JOB_NAME]


@pytest.mark.parametrize(
    "extra_headers",
    [{"x-supoclip-client": "ios"}, {"user-agent": "supoclip-ios/42 CFNetwork/1498"}],
)
async def test_clients_that_cannot_ask_for_a_download_always_get_full_quality(
    client, app, db_session, auth_headers, local_media, monkeypatch, extra_headers
):
    fake_ffmpeg(monkeypatch)
    jobs = worker_queue(monkeypatch, app)
    task_id, [clip_id] = await seed_preview(db_session, local_media)

    response = await client.get(
        f"/tasks/{task_id}/clips/{clip_id}/file", headers={**auth_headers, **extra_headers}
    )
    assert response.status_code == 200
    assert response.content == FULL_BYTES
    assert len(jobs) == 1


async def test_a_slow_render_answers_503_with_retry_after_and_keeps_rendering(
    client, app, db_session, auth_headers, local_media, monkeypatch
):
    monkeypatch.setattr(get_config(), "full_render_download_wait_seconds", 0)
    jobs = worker_queue(monkeypatch, app, run=False)
    task_id, [clip_id] = await seed_preview(db_session, local_media)

    response = await client.get(
        f"/tasks/{task_id}/clips/{clip_id}/file?download=true", headers=auth_headers
    )
    assert response.status_code == 503
    assert response.headers["retry-after"] == "10"
    detail = response.json()["detail"]
    assert detail["code"] == "FULL_QUALITY_PREPARING"
    assert detail["full_render"]["status"] == "queued"
    # Asking again neither fails nor queues a second job.
    retry = await client.get(f"/tasks/{task_id}/clips/{clip_id}/file?download=true", headers=auth_headers)
    assert retry.status_code == 503
    assert len(jobs) == 1

    status = await client.get(f"/tasks/{task_id}/clips/{clip_id}/full-render", headers=auth_headers)
    assert status.json()["full_render"] == {"status": "queued", "progress": 0, "error": None}


async def test_a_failed_render_is_reported_and_queued_again_on_the_next_request(
    client, app, db_session, auth_headers, local_media, monkeypatch
):
    fake_ffmpeg(monkeypatch, fail=True)
    jobs = worker_queue(monkeypatch, app)
    task_id, [clip_id] = await seed_preview(db_session, local_media)
    url = f"/tasks/{task_id}/clips/{clip_id}"

    download = await client.get(f"{url}/file?download=true", headers=auth_headers)
    assert download.status_code == 502
    assert download.json()["detail"]["code"] == "FULL_QUALITY_FAILED"
    status = (await client.get(f"{url}/full-render", headers=auth_headers)).json()
    assert status["render_quality"] == "preview"
    assert status["full_render"]["status"] == "failed"
    assert status["full_render"]["error"] == "Could not prepare the full-quality clip. Try again."
    # The preview is untouched.
    assert Path((await clip_row(db_session, clip_id))["file_path"]).read_bytes() == PREVIEW_BYTES

    fake_ffmpeg(monkeypatch)
    retried = await client.post(f"{url}/full-render", headers=auth_headers)
    assert retried.json()["full_render"]["status"] == "ready"
    assert len(jobs) == 2


async def test_missing_inputs_fail_with_a_regenerate_message(
    client, app, db_session, auth_headers, local_media, monkeypatch
):
    fake_ffmpeg(monkeypatch)
    worker_queue(monkeypatch, app)
    task_id, [clip_id] = await seed_preview(db_session, local_media)
    inputs = Path((await clip_row(db_session, clip_id))["render_inputs_path"])
    (inputs.parent / "render.json").unlink()

    started = await client.post(f"/tasks/{task_id}/clips/{clip_id}/full-render", headers=auth_headers)
    assert started.json()["full_render"]["status"] == "failed"
    assert "Regenerate" in started.json()["full_render"]["error"]


async def test_zip_renders_every_preview_before_listing_files(
    client, app, db_session, auth_headers, local_media, monkeypatch
):
    fake_ffmpeg(monkeypatch)
    jobs = worker_queue(monkeypatch, app)
    task_id, clip_ids = await seed_preview(db_session, local_media, clips=2)

    summary = (await client.get(f"/tasks/{task_id}/full-render", headers=auth_headers)).json()
    assert (summary["total"], summary["ready"], summary["status"]) == (2, 0, "preparing")

    preflight = await client.get(f"/tasks/{task_id}/clips/zip/preflight", headers=auth_headers)
    assert preflight.status_code == 200, preflight.text
    assert preflight.json()["clip_count"] == 2
    assert preflight.json()["total_bytes"] == 2 * len(FULL_BYTES)
    assert len(jobs) == 2
    summary = (await client.get(f"/tasks/{task_id}/full-render", headers=auth_headers)).json()
    assert (summary["ready"], summary["progress"], summary["status"]) == (2, 100, "ready")


async def test_task_level_request_starts_every_preview(
    client, app, db_session, auth_headers, local_media, monkeypatch
):
    jobs = worker_queue(monkeypatch, app, run=False)
    task_id, clip_ids = await seed_preview(db_session, local_media, clips=3)

    started = await client.post(f"/tasks/{task_id}/full-render", headers=auth_headers, json={"trigger": "zip"})
    assert started.status_code == 202
    body = started.json()
    assert (body["total"], body["ready"], body["status"]) == (3, 0, "preparing")
    assert {state["full_render"]["status"] for state in body["clips"]} == {"queued"}
    assert sorted(job["args"][0] for job in jobs) == sorted(clip_ids)


async def test_preset_exports_start_from_the_full_quality_clip(
    client, app, db_session, auth_headers, local_media, monkeypatch
):
    fake_ffmpeg(monkeypatch)
    worker_queue(monkeypatch, app)
    task_id, [clip_id] = await seed_preview(db_session, local_media)

    started = await client.post(
        f"/tasks/{task_id}/clips/{clip_id}/exports", headers=auth_headers, json={"preset": "tiktok"}
    )
    assert started.status_code == 202, started.text
    clip = await clip_row(db_session, clip_id)
    assert clip["render_quality"] == "full"
    source = (
        await db_session.execute(
            text("SELECT source_file_path FROM clip_exports WHERE id = :id"), {"id": started.json()["id"]}
        )
    ).scalar()
    assert source == clip["file_path"]


async def test_the_synchronous_export_encodes_the_full_quality_clip(
    client, app, db_session, auth_headers, local_media, monkeypatch
):
    fake_ffmpeg(monkeypatch)
    worker_queue(monkeypatch, app)
    task_id, [clip_id] = await seed_preview(db_session, local_media)
    seen = []

    def export(input_path, output_dir, preset_name):
        seen.append(Path(input_path).read_bytes())
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        output = Path(output_dir) / "export.mp4"
        output.write_bytes(b"exported")
        return output

    monkeypatch.setattr("src.api.routes.tasks.export_with_preset", export)
    response = await client.get(f"/tasks/{task_id}/clips/{clip_id}/export?preset=tiktok", headers=auth_headers)
    assert response.status_code == 200
    assert seen == [FULL_BYTES]


async def test_publishing_renders_full_quality_before_the_post_exists(
    client, app, db_session, auth_headers, local_media, monkeypatch
):
    fake_ffmpeg(monkeypatch)
    jobs = worker_queue(monkeypatch, app)
    task_id, [clip_id] = await seed_preview(db_session, local_media)

    async def allowed(self, user_id):
        return True

    monkeypatch.setattr("src.services.social_service.SocialService.has_access", allowed)
    response = await client.post(
        "/social/posts",
        headers=auth_headers,
        json={"task_id": task_id, "clip_id": clip_id, "social_account_id": "missing-account"},
    )
    # The account doesn't exist, but the clip was made full quality first.
    assert response.status_code in {400, 404, 422}
    assert len(jobs) == 1
    assert (await clip_row(db_session, clip_id))["render_quality"] == "full"


async def test_legacy_trim_cuts_the_full_quality_clip(
    client, app, db_session, auth_headers, local_media, monkeypatch
):
    fake_ffmpeg(monkeypatch)
    worker_queue(monkeypatch, app)
    task_id, [clip_id] = await seed_preview(db_session, local_media)
    trimmed = []

    async def trim(self, task_id_arg, clip_id_arg, start, end):
        clip = await ClipRepository.get_clip_by_id(self.db, clip_id_arg)
        trimmed.append(Path(clip["file_path"]).read_bytes())
        return {"id": clip_id_arg}

    monkeypatch.setattr("src.services.task_service.TaskService.trim_clip", trim)
    response = await client.patch(
        f"/tasks/{task_id}/clips/{clip_id}", headers=auth_headers, json={"start_offset": 1, "end_offset": 0}
    )
    assert response.status_code == 200, response.text
    assert trimmed == [FULL_BYTES]


async def test_opening_the_studio_starts_the_render_without_waiting(
    app, db_session, local_media, monkeypatch
):
    from types import SimpleNamespace

    from src.api.routes.editor import _start_full_render_for_studio

    jobs = worker_queue(monkeypatch, app, run=False)
    task_id, [clip_id] = await seed_preview(db_session, local_media)
    clip = await clip_row(db_session, clip_id)
    request = SimpleNamespace(app=app, headers={})

    await _start_full_render_for_studio(request, db_session, {"user_id": "user-1"}, clip)
    assert [job["args"] for job in jobs] == [(clip_id,)]
    queued = await clip_row(db_session, clip_id)
    assert queued["full_render"]["status"] == "queued"
    # Reopening while it renders does not queue it again.
    await _start_full_render_for_studio(request, db_session, {"user_id": "user-1"}, queued)
    assert len(jobs) == 1


async def test_a_clip_edited_during_its_render_keeps_the_edit(
    app, db_session, local_media, monkeypatch
):
    """The swap is compare-and-swap on the preview's path; the stray render is removed."""
    task_id, [clip_id] = await seed_preview(db_session, local_media)
    replaced = local_media / "clips" / "edited.mp4"
    replaced.write_bytes(b"edited")
    outputs = []

    def render(inputs_dir, output_path, broll_overlays=None):
        Path(output_path).write_bytes(FULL_BYTES)

    original_store = full_render_service._store_output

    def store(storage, output, task, clips_dir):
        stored = original_store(storage, output, task, clips_dir)
        outputs.append(Path(stored))
        return stored

    monkeypatch.setattr(full_render_service, "render_full_clip_file", render)
    monkeypatch.setattr(full_render_service, "_store_output", store)
    await db_session.execute(
        text("UPDATE generated_clips SET full_render_status = 'queued' WHERE id = :id"), {"id": clip_id}
    )
    await db_session.commit()

    real_claim = full_render_service._claim

    async def claim_then_edit(db, claimed_id):
        claimed = await real_claim(db, claimed_id)
        await db.execute(
            text("UPDATE generated_clips SET file_path = :path WHERE id = :id"),
            {"path": str(replaced), "id": claimed_id},
        )
        await db.commit()
        return claimed

    monkeypatch.setattr(full_render_service, "_claim", claim_then_edit)
    result = await render_full_clip({}, clip_id)
    assert result["status"] == "abandoned"
    assert (await clip_row(db_session, clip_id))["file_path"] == str(replaced)
    assert outputs and not outputs[0].exists()


async def test_deleting_a_preview_clip_queues_its_render_inputs(
    client, db_session, auth_headers, local_media
):
    task_id, [clip_id] = await seed_preview(db_session, local_media)
    inputs = (await clip_row(db_session, clip_id))["render_inputs_path"]

    response = await client.delete(f"/tasks/{task_id}/clips/{clip_id}", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert (str(Path(inputs).parent), True) in await pending_deletions(db_session)


async def test_clip_listing_reports_quality_without_storage_paths(
    client, db_session, auth_headers, local_media
):
    task_id, [clip_id] = await seed_preview(db_session, local_media)
    clips = (await client.get(f"/tasks/{task_id}/clips", headers=auth_headers)).json()["clips"]
    assert clips[0]["render_quality"] == "preview"
    assert clips[0]["full_render"]["status"] == "none"
    assert "render_inputs_path" not in clips[0]
    assert "file_path" not in clips[0]


async def test_create_clip_resets_render_state_when_a_position_is_rendered_again(db_session, local_media):
    task_id, [clip_id] = await seed_preview(db_session, local_media)
    full = local_media / "clips" / "rerendered.mp4"
    full.write_bytes(FULL_BYTES)

    new_id = await ClipRepository.create_clip(
        db_session, task_id=task_id, filename=full.name, file_path=str(full),
        start_time="00:00", end_time="00:10", duration=10, text="again",
        relevance_score=0.9, reasoning="again", clip_order=1,
    )
    await db_session.commit()
    clip = await clip_row(db_session, new_id)
    assert clip["render_quality"] == "full"
    assert clip["render_inputs_path"] is None


async def test_task_creation_records_the_flag_for_the_worker(
    client, app, db_session, auth_headers, feature_flag, monkeypatch
):
    async def info(_url, **_kwargs):
        return {"duration": 600, "title": "Preview source"}

    monkeypatch.setattr("src.api.routes.tasks.async_get_youtube_video_info", info)
    await create_user(db_session, user_id="user-1")
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    async def create(video_id: str):
        response = await client.post(
            "/tasks/", headers=auth_headers, json={"source": {"url": f"https://www.youtube.com/watch?v={video_id}"}}
        )
        assert response.status_code == 200, response.text
        task_id = response.json()["task_id"]
        await db_session.rollback()
        return (
            await db_session.execute(text("SELECT preview_renders FROM tasks WHERE id = :id"), {"id": task_id})
        ).scalar()

    assert url
    await feature_flag("preview_renders", web_enabled=False, admins_bypass=False)
    assert await create("dQw4w9WgXcQ") is False
    await feature_flag("preview_renders", web_enabled=True)
    from src import feature_flags

    feature_flags.clear_cache()
    assert await create("9bZkp7q19f0") is True
