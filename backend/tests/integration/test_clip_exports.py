"""Queued preset exports against PostgreSQL, with ffmpeg replaced by a stub."""

import hashlib
import hmac
import time
from pathlib import Path

from sqlalchemy import text

from src.config import get_config
from src.repositories.clip_export_repository import ClipExportRepository
from src.repositories.clip_repository import ClipRepository
from src.services.clip_export_service import expire_clip_exports
from src.workers.media_cleanup import drain_deletions
from src.media_storage import MediaStorage
from tests.fixtures.factories import create_clip, create_source, create_task, create_user


def record_jobs(monkeypatch, app):
    jobs = []

    async def record(cls, function_name, *args, **kwargs):
        jobs.append({"function_name": function_name, "args": args, "kwargs": kwargs})
        return "job-test"

    monkeypatch.setattr(app.state.queue_adapter, "enqueue_job", classmethod(record))
    return jobs


def headers_for(user_id: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    payload = f"{user_id}:{timestamp}".encode()
    signature = hmac.new(b"test-backend-auth-secret", payload, hashlib.sha256).hexdigest()
    return {
        "x-supoclip-user-id": user_id,
        "x-supoclip-ts": timestamp,
        "x-supoclip-signature": signature,
    }


async def seed_clip(db_session, *, user_id: str = "user-1", file_path: str = "/tmp/seeded.mp4"):
    user = await create_user(db_session, user_id=user_id, email=f"{user_id}@example.com")
    source = await create_source(db_session, title=f"Source {user_id}")
    task = await create_task(db_session, user_id=user["id"], source_id=source["id"])
    clip = await create_clip(db_session, task_id=task["id"])
    await db_session.execute(
        text("UPDATE generated_clips SET file_path = :path, filename = :filename WHERE id = :id"),
        {"path": file_path, "filename": "interview.mp4", "id": clip["id"]},
    )
    await db_session.commit()
    return task["id"], clip["id"]


def exports_url(task_id: str, clip_id: str) -> str:
    return f"/tasks/{task_id}/clips/{clip_id}/exports"


async def test_start_is_idempotent_until_the_options_or_source_change(
    client, app, db_session, auth_headers, tmp_path, monkeypatch
):
    jobs = record_jobs(monkeypatch, app)
    task_id, clip_id = await seed_clip(db_session, file_path=str(tmp_path / "interview.mp4"))
    url = exports_url(task_id, clip_id)

    first = await client.post(url, headers=auth_headers, json={"preset": "tiktok"})
    second = await client.post(url, headers=auth_headers, json={"preset": "TikTok"})
    query = await client.post(f"{url}?preset=reels", headers=auth_headers)
    assert first.status_code == second.status_code == query.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["status"] == "queued"
    assert first.json()["download_url"] is None
    assert query.json()["id"] != first.json()["id"]
    assert query.json()["preset"] == "reels"

    await db_session.execute(
        text("UPDATE generated_clips SET file_path = :path WHERE id = :id"),
        {"path": str(tmp_path / "interview-v2.mp4"), "id": clip_id},
    )
    await db_session.commit()
    regenerated = await client.post(url, headers=auth_headers, json={"preset": "tiktok"})
    assert regenerated.status_code == 202
    assert regenerated.json()["id"] != first.json()["id"]

    names = [job["function_name"] for job in jobs]
    assert names == ["export_clip_preset", "export_clip_preset", "export_clip_preset"]
    assert jobs[0]["kwargs"]["_job_id"].endswith(first.json()["id"])


async def test_stale_and_failed_jobs_can_be_started_again(
    client, app, db_session, auth_headers, tmp_path, monkeypatch
):
    task_id, clip_id = await seed_clip(db_session, file_path=str(tmp_path / "clip.mp4"))
    url = exports_url(task_id, clip_id)

    async def unavailable(cls, *args, **kwargs):
        raise ConnectionError("redis down")

    monkeypatch.setattr(app.state.queue_adapter, "enqueue_job", classmethod(unavailable))
    failed = await client.post(url, headers=auth_headers, json={"preset": "shorts"})
    assert failed.status_code == 503

    jobs = record_jobs(monkeypatch, app)
    retry = await client.post(url, headers=auth_headers, json={"preset": "shorts"})
    assert retry.status_code == 202
    assert retry.json()["status"] == "queued"
    assert len(jobs) == 1


def pretend_arq_jobs(monkeypatch, *, alive: bool):
    async def check(export_ids):
        return {export_id: alive for export_id in export_ids}

    monkeypatch.setattr("src.services.clip_export_service.arq_jobs_alive", check)


async def age_export(db_session, export_id: str, *, minutes: int, status: str | None = None):
    assignments = [
        "created_at = NOW() - make_interval(mins => :minutes)",
        "updated_at = NOW() - make_interval(mins => :minutes)",
    ]
    params: dict = {"id": export_id, "minutes": minutes}
    if status is not None:
        assignments.append("status = :status")
        params["status"] = status
    await db_session.execute(
        text(f"UPDATE clip_exports SET {', '.join(assignments)} WHERE id = :id"),
        params,
    )
    await db_session.commit()


async def test_queued_export_waiting_past_three_minutes_is_not_failed(
    client, app, db_session, auth_headers, tmp_path, monkeypatch
):
    jobs = record_jobs(monkeypatch, app)
    pretend_arq_jobs(monkeypatch, alive=True)
    task_id, clip_id = await seed_clip(db_session, file_path=str(tmp_path / "waiting.mp4"))
    url = exports_url(task_id, clip_id)
    started = await client.post(url, headers=auth_headers, json={"preset": "tiktok"})
    export_id = started.json()["id"]
    await age_export(db_session, export_id, minutes=10)

    status = await client.get(f"{url}/{export_id}", headers=auth_headers)
    assert status.status_code == 200
    assert status.json()["status"] == "queued"
    assert status.json()["error"] is None

    again = await client.post(url, headers=auth_headers, json={"preset": "tiktok"})
    assert again.status_code == 202
    assert again.json()["id"] == export_id
    assert len(jobs) == 1


async def test_running_export_with_a_stale_heartbeat_is_failed(
    client, app, db_session, auth_headers, tmp_path, monkeypatch
):
    jobs = record_jobs(monkeypatch, app)
    task_id, clip_id = await seed_clip(db_session, file_path=str(tmp_path / "running.mp4"))
    url = exports_url(task_id, clip_id)
    started = await client.post(url, headers=auth_headers, json={"preset": "reels"})
    export_id = started.json()["id"]
    await age_export(db_session, export_id, minutes=10, status="running")

    status = await client.get(f"{url}/{export_id}", headers=auth_headers)
    assert status.json()["status"] == "failed"
    assert status.json()["error"] == "Export timed out before it finished."

    replacement = await client.post(url, headers=auth_headers, json={"preset": "reels"})
    assert replacement.status_code == 202
    assert replacement.json()["id"] != export_id
    assert replacement.json()["status"] == "queued"
    assert len(jobs) == 2


async def test_lost_queue_job_and_the_hour_cap_free_the_idempotency_key(
    client, app, db_session, auth_headers, tmp_path, monkeypatch
):
    jobs = record_jobs(monkeypatch, app)
    pretend_arq_jobs(monkeypatch, alive=False)
    task_id, clip_id = await seed_clip(db_session, file_path=str(tmp_path / "lost.mp4"))
    url = exports_url(task_id, clip_id)
    started = await client.post(url, headers=auth_headers, json={"preset": "shorts"})
    export_id = started.json()["id"]
    await age_export(db_session, export_id, minutes=10)

    replacement = await client.post(url, headers=auth_headers, json={"preset": "shorts"})
    assert replacement.json()["id"] != export_id
    lost = await client.get(f"{url}/{export_id}", headers=auth_headers)
    assert lost.json()["status"] == "failed"
    assert lost.json()["error"] == "The export worker is unavailable."

    pretend_arq_jobs(monkeypatch, alive=True)
    await age_export(db_session, replacement.json()["id"], minutes=61)
    capped = await client.post(url, headers=auth_headers, json={"preset": "shorts"})
    assert capped.json()["id"] != replacement.json()["id"]
    waiting = await client.get(f"{url}/{replacement.json()['id']}", headers=auth_headers)
    assert waiting.json()["status"] == "failed"
    assert waiting.json()["error"] == "Export timed out before it finished."
    assert len(jobs) == 3


async def test_worker_stores_a_local_file_and_the_download_requires_the_owner(
    client, db_session, auth_headers, tmp_path, monkeypatch
):
    config = get_config()
    config.media_storage = "local"
    config.temp_dir = str(tmp_path)
    assert MediaStorage().enabled is False
    task_id, clip_id = await seed_clip(db_session, file_path=str(tmp_path / "source.mp4"))
    url = exports_url(task_id, clip_id)
    started = await client.post(url, headers={**auth_headers, "content-type": "application/json"}, json={"preset": "reels"})
    export_id = started.json()["id"]
    not_ready = await client.get(f"{url}/{export_id}/file", headers=auth_headers)
    assert not_ready.status_code == 409

    def fake_export(input_path, output_dir, preset_name, *, output_path, on_progress, should_stop):
        assert preset_name == "reels"
        assert Path(input_path) == tmp_path / "source.mp4"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"preset-mp4")
        on_progress(80)
        assert should_stop() is False
        return output_path

    monkeypatch.setattr("src.services.clip_export_service.export_with_preset", fake_export)
    from src.services.clip_export_service import export_clip_preset

    result = await export_clip_preset({}, export_id)
    assert result["status"] == "succeeded"

    status = await client.get(f"{url}/{export_id}", headers=auth_headers)
    body = status.json()
    assert body["status"] == "succeeded"
    assert body["progress"] == 100
    assert body["error"] is None
    assert body["download_url"] == f"/tasks/{task_id}/clips/{clip_id}/exports/{export_id}/file"

    early = await client.get(f"{url}/missing/file", headers=auth_headers)
    assert early.status_code == 404
    downloaded = await client.get(body["download_url"], headers=auth_headers)
    assert downloaded.status_code == 200
    assert downloaded.content == b"preset-mp4"
    assert "interview_reels.mp4" in downloaded.headers["content-disposition"]
    repeated = await client.post(url, headers=auth_headers, json={"preset": "reels"})
    assert repeated.status_code == 202
    assert repeated.json()["id"] == export_id
    assert repeated.json()["status"] == "succeeded"
    assert repeated.json()["download_url"]

    outsider = headers_for("user-2")
    await create_user(db_session, user_id="user-2", email="other@example.com")
    assert (await client.get(f"{url}/{export_id}", headers=outsider)).status_code == 403
    assert (await client.post(url, headers=outsider, json={"preset": "reels"})).status_code == 403


async def test_invalid_preset_and_foreign_clip_are_rejected(client, db_session, auth_headers):
    task_id, clip_id = await seed_clip(db_session)
    source = await create_source(db_session, title="Second source")
    other_task = await create_task(db_session, user_id="user-1", source_id=source["id"])
    url = exports_url(task_id, clip_id)
    assert (await client.post(url, headers=auth_headers, json={"preset": "stories"})).status_code == 400
    rejected = await client.post(
        url,
        headers={**auth_headers, "content-type": "application/json"},
        content=b"{",
    )
    assert rejected.status_code == 400
    missing = await client.post(exports_url(task_id, "missing-clip"), headers=auth_headers, json={"preset": "tiktok"})
    assert missing.status_code == 404
    foreign = await client.post(
        exports_url(other_task["id"], clip_id), headers=auth_headers, json={"preset": "tiktok"}
    )
    assert foreign.status_code == 404


async def test_succeeded_s3_export_redirects_to_a_presigned_url(client, db_session, auth_headers, monkeypatch):
    task_id, clip_id = await seed_clip(db_session)
    url = exports_url(task_id, clip_id)
    started = await client.post(url, headers=auth_headers, json={"preset": "tiktok"})
    export_id = started.json()["id"]
    reference = "s3://test-bucket/media/exports/finished.mp4"
    await db_session.execute(
        text(
            """
            UPDATE clip_exports
            SET status = 'succeeded', progress = 100, file_path = :path, updated_at = NOW()
            WHERE id = :id
            """
        ),
        {"path": reference, "id": export_id},
    )
    await db_session.commit()
    monkeypatch.setattr(MediaStorage, "exists", lambda self, _reference: True)
    monkeypatch.setattr(
        MediaStorage,
        "signed_url",
        lambda self, _reference, filename, attachment=False, expires_in=None: f"https://storage.test/{filename}",
    )

    status = await client.get(f"{url}/{export_id}", headers=auth_headers)
    assert status.json()["download_url"] == "https://storage.test/interview_tiktok.mp4"
    download = await client.get(f"{url}/{export_id}/file", headers=auth_headers)
    assert download.status_code == 307
    assert download.headers["location"] == "https://storage.test/interview_tiktok.mp4"


async def test_deleting_a_clip_queues_its_export_file_and_expiry_removes_the_row(
    client, db_session, auth_headers, tmp_path
):
    get_config().temp_dir = str(tmp_path)
    task_id, clip_id = await seed_clip(db_session, file_path=str(tmp_path / "source.mp4"))
    started = await client.post(exports_url(task_id, clip_id), headers=auth_headers, json={"preset": "tiktok"})
    export_id = started.json()["id"]
    stored = tmp_path / "clip-exports" / export_id / f"{export_id}.mp4"
    stored.parent.mkdir(parents=True)
    stored.write_bytes(b"mp4")
    await db_session.execute(
        text(
            """
            UPDATE clip_exports
            SET status = 'succeeded', progress = 100, file_path = :path,
                expires_at = NOW() - INTERVAL '1 minute', updated_at = NOW()
            WHERE id = :id
            """
        ),
        {"path": str(stored), "id": export_id},
    )
    await db_session.commit()

    storage = MediaStorage()
    summary = await expire_clip_exports(db_session, storage)
    assert summary["expired"] == 1
    assert await ClipExportRepository.get(db_session, export_id) is None
    queued = await db_session.execute(
        text("SELECT file_path FROM media_deletions WHERE file_path = :path"),
        {"path": str(stored)},
    )
    assert queued.scalar() == str(stored)
    await drain_deletions(db_session, storage)
    assert not stored.exists()

    task_id, clip_id = await seed_clip(db_session, file_path=str(tmp_path / "source.mp4"))
    kept = tmp_path / "kept-export.mp4"
    kept.write_bytes(b"keep")
    started = await client.post(exports_url(task_id, clip_id), headers=auth_headers, json={"preset": "tiktok"})
    await db_session.execute(
        text("UPDATE clip_exports SET status = 'succeeded', file_path = :path, progress = 100 WHERE id = :id"),
        {"path": str(kept), "id": started.json()["id"]},
    )
    await db_session.commit()
    await ClipRepository.delete_clip(db_session, clip_id)
    remaining = await db_session.execute(
        text("SELECT COUNT(*) FROM clip_exports WHERE clip_id = :id"),
        {"id": clip_id},
    )
    assert remaining.scalar() == 0
    queued = await db_session.execute(
        text("SELECT file_path FROM media_deletions WHERE file_path = :path"),
        {"path": str(kept)},
    )
    assert queued.scalar() == str(kept)
