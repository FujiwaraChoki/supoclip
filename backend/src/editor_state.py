"""Clip studio sessions and render jobs, kept in PostgreSQL.

These used to be JSON files in ``TEMP_DIR/editor/<key>/`` (``asset.json``,
``draft.json``, ``original.json``, ``job-*.json``, ``request-*.json`` and
``cancel-*`` markers) guarded by a file lock. The API and a worker on another
machine do not share that directory. The state is now rows; the directory is
a scratch cache of the session's media, and object storage holds the durable
copy of that media.

A session that only exists as files (written before this module) is imported
the first time it is opened, see ``ensure_session``.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
import uuid

from sqlalchemy import text

from .editor_document import editor_dir

logger = logging.getLogger(__name__)

MAX_ACTIVE_JOBS = 3
# A running render writes its progress about once a second.
STALE_RENDER_SECONDS = 180
# A running preparation touches its row every PREPARE_HEARTBEAT_SECONDS.
PREPARE_HEARTBEAT_SECONDS = 20
STALE_PREPARE_SECONDS = 300
# Enqueue happens right after the row is written. A job that is still not in
# Redis after this long was lost, not slow.
LOST_JOB_GRACE_SECONDS = 60
ACTIVE = ("queued", "rendering")
INTERRUPTED_PREPARE = "Preparing this clip was interrupted. Retry."
INTERRUPTED_RENDER = "The export stopped before it finished. Your draft is safe; retry the export."
LOST_RENDER = "The rendering worker did not pick up this export. Your draft is safe; retry the export."


def editor_key(task_id: str, clip_id: str, filename: str) -> str:
    """Session id: also the scratch directory name and the object storage prefix."""
    return editor_dir("", task_id, clip_id, filename).name


def render_job_id(job_id: str) -> str:
    """Queue id of an export or combine job, so a lost job can be told from a waiting one."""
    return f"editor-job:{job_id}"


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def _present_session(row) -> dict:
    draft = _json(row["draft"])
    return {
        "asset": _json(row["asset"]),
        "draft": None if draft is None else {"revision": row["draft_revision"], "document": draft},
        "original": _json(row["original"]),
        "idle_seconds": float(row["idle_seconds"]),
    }


def present_job(row) -> dict:
    """The shape the studio polls: what ``job-<id>.json`` used to contain."""
    job = {"id": row["id"], "status": row["status"], "progress": int(row["progress"] or 0)}
    if row["error"]:
        job["error"] = row["error"]
    return {**job, **(_json(row["result"]) or {})}


class EditorStore:
    """Every statement opens its own short session, so a render never holds one."""

    def _session(self):
        from .database import get_session_maker

        return get_session_maker()()

    async def load(self, key: str) -> dict | None:
        async with self._session() as db:
            row = (await db.execute(text("""
                SELECT asset, draft, draft_revision, original,
                       EXTRACT(EPOCH FROM (NOW() - updated_at)) AS idle_seconds
                FROM editor_sessions WHERE id = :id
            """), {"id": key})).mappings().first()
        return _present_session(row) if row else None

    async def claim_prepare(self, key: str, task_id: str, clip_id: str, filename: str, job: str) -> str:
        """``claimed`` when the caller must enqueue; otherwise the current ``ready``/``preparing``."""
        asset = json.dumps({"status": "preparing", "job": job})
        async with self._session() as db:
            claimed = (await db.execute(text("""
                INSERT INTO editor_sessions (id, task_id, clip_id, filename, asset)
                VALUES (:id, :task, :clip, :filename, CAST(:asset AS JSONB))
                ON CONFLICT (id) DO UPDATE SET asset = EXCLUDED.asset, updated_at = NOW()
                WHERE editor_sessions.asset->>'status' NOT IN ('ready', 'preparing')
                RETURNING id
            """), {"id": key, "task": task_id, "clip": clip_id, "filename": filename, "asset": asset})).first()
            await db.commit()
            if claimed:
                return "claimed"
            current = (await db.execute(
                text("SELECT asset->>'status' FROM editor_sessions WHERE id = :id"), {"id": key}
            )).scalar()
        return current or "preparing"

    async def begin_prepare(self, key: str, task_id: str, clip_id: str, filename: str) -> None:
        """A worker has the job. Also covers a job queued before this row existed."""
        async with self._session() as db:
            await db.execute(text("""
                INSERT INTO editor_sessions (id, task_id, clip_id, filename, asset)
                VALUES (:id, :task, :clip, :filename, jsonb_build_object('status', 'preparing', 'started', true))
                ON CONFLICT (id) DO UPDATE
                SET asset = CASE WHEN editor_sessions.asset->>'status' = 'ready' THEN editor_sessions.asset
                                 ELSE jsonb_build_object('status', 'preparing', 'started', true)
                                      || jsonb_strip_nulls(jsonb_build_object('job', editor_sessions.asset->'job')) END,
                    updated_at = NOW()
            """), {"id": key, "task": task_id, "clip": clip_id, "filename": filename})
            await db.commit()

    async def touch_prepare(self, key: str) -> None:
        async with self._session() as db:
            await db.execute(text("""
                UPDATE editor_sessions SET updated_at = NOW()
                WHERE id = :id AND asset->>'status' = 'preparing'
            """), {"id": key})
            await db.commit()

    async def finish_prepare(self, key: str, task_id: str, clip_id: str, filename: str, asset: dict, document: dict) -> None:
        """Mark the media ready. An existing draft and its original are kept."""
        async with self._session() as db:
            await db.execute(text("""
                INSERT INTO editor_sessions (id, task_id, clip_id, filename, asset, draft, original)
                VALUES (:id, :task, :clip, :filename, CAST(:asset AS JSONB), CAST(:document AS JSONB), CAST(:document AS JSONB))
                ON CONFLICT (id) DO UPDATE
                SET asset = EXCLUDED.asset,
                    draft = COALESCE(editor_sessions.draft, EXCLUDED.draft),
                    original = COALESCE(editor_sessions.original, EXCLUDED.original),
                    updated_at = NOW()
            """), {"id": key, "task": task_id, "clip": clip_id, "filename": filename,
                   "asset": json.dumps(asset, allow_nan=False), "document": json.dumps(document, allow_nan=False)})
            await db.commit()

    async def fail_prepare(self, key: str, task_id: str, clip_id: str, filename: str, error: str, *, only_stale: bool = False) -> None:
        """Record a failed preparation. A ready session and any saved draft stay."""
        asset = json.dumps({"status": "failed", "error": error})
        guard = "= 'preparing'" if only_stale else "<> 'ready'"
        async with self._session() as db:
            await db.execute(text(f"""
                INSERT INTO editor_sessions (id, task_id, clip_id, filename, asset)
                VALUES (:id, :task, :clip, :filename, CAST(:asset AS JSONB))
                ON CONFLICT (id) DO UPDATE SET asset = EXCLUDED.asset, updated_at = NOW()
                WHERE editor_sessions.asset->>'status' {guard}
            """), {"id": key, "task": task_id, "clip": clip_id, "filename": filename, "asset": asset})
            await db.commit()

    async def save_draft(self, key: str, document: dict, revision: int) -> int | None:
        """Compare-and-swap on the revision. None means another save won."""
        async with self._session() as db:
            saved = (await db.execute(text("""
                UPDATE editor_sessions
                SET draft = CAST(:document AS JSONB), draft_revision = draft_revision + 1, updated_at = NOW()
                WHERE id = :id AND draft_revision = :revision AND asset->>'status' = 'ready'
                RETURNING draft_revision
            """), {"id": key, "revision": revision, "document": json.dumps(document, allow_nan=False)})).scalar()
            await db.commit()
        return saved

    async def delete(self, key: str) -> None:
        async with self._session() as db:
            await db.execute(text("DELETE FROM editor_sessions WHERE id = :id"), {"id": key})
            await db.commit()

    async def create_job(self, key: str, job_id: str, kind: str, request: dict, *, limit: int = MAX_ACTIVE_JOBS) -> bool:
        """False when the session already has ``limit`` jobs queued or rendering."""
        async with self._session() as db:
            await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 113471))"), {"key": key})
            active = (await db.execute(text("""
                SELECT COUNT(*) FROM editor_jobs WHERE session_id = :key AND status IN ('queued', 'rendering')
            """), {"key": key})).scalar()
            if active >= limit:
                await db.rollback()
                return False
            await db.execute(text("""
                INSERT INTO editor_jobs (id, session_id, kind, status, request)
                VALUES (:id, :key, :kind, 'queued', CAST(:request AS JSONB))
            """), {"id": job_id, "key": key, "kind": kind, "request": json.dumps(request, allow_nan=False)})
            await db.commit()
        return True

    async def job(self, key: str, job_id: str) -> dict | None:
        async with self._session() as db:
            row = (await db.execute(text("""
                SELECT id, status, progress, error, result FROM editor_jobs
                WHERE id = :id AND session_id = :key
            """), {"id": job_id, "key": key})).mappings().first()
        return present_job(row) if row else None

    async def jobs(self, key: str, limit: int = 20) -> list[dict]:
        async with self._session() as db:
            rows = (await db.execute(text("""
                SELECT id, status, progress, error, result FROM editor_jobs
                WHERE session_id = :key ORDER BY created_at DESC, id LIMIT :limit
            """), {"key": key, "limit": limit})).mappings().all()
        return [present_job(row) for row in rows]

    async def active_jobs(self, key: str) -> list[dict]:
        async with self._session() as db:
            rows = (await db.execute(text("""
                SELECT id, status,
                       EXTRACT(EPOCH FROM (NOW() - updated_at)) AS idle_seconds
                FROM editor_jobs WHERE session_id = :key AND status IN ('queued', 'rendering')
            """), {"key": key})).mappings().all()
        return [{"id": r["id"], "status": r["status"], "idle_seconds": float(r["idle_seconds"])} for r in rows]

    async def fail_job(self, job_id: str, error: str, *, expected: str) -> bool:
        """Fail a job that is still ``expected`` (``queued`` or ``rendering``)."""
        async with self._session() as db:
            failed = (await db.execute(text("""
                UPDATE editor_jobs SET status = 'failed', progress = 0, error = :error, updated_at = NOW()
                WHERE id = :id AND status = :expected RETURNING id
            """), {"id": job_id, "error": error, "expected": expected})).first()
            await db.commit()
        return failed is not None

    async def cancel_job(self, key: str, job_id: str) -> str | None:
        """Status to report after the request, or None for an unknown job."""
        async with self._session() as db:
            row = (await db.execute(text("""
                SELECT status FROM editor_jobs WHERE id = :id AND session_id = :key FOR UPDATE
            """), {"id": job_id, "key": key})).first()
            if row is None:
                await db.rollback()
                return None
            status = row.status
            if status == "queued":
                # The worker's claim skips a job that is no longer queued.
                await db.execute(text("""
                    UPDATE editor_jobs SET status = 'cancelled', progress = 0, cancel_requested = TRUE, updated_at = NOW()
                    WHERE id = :id
                """), {"id": job_id})
                status = "cancelled"
            elif status == "rendering":
                await db.execute(text("UPDATE editor_jobs SET cancel_requested = TRUE WHERE id = :id"), {"id": job_id})
                status = "cancelling"
            await db.commit()
        return status

    async def start_job(self, job_id: str) -> dict | None:
        """Claim a queued job for this worker. None when there is nothing to run.

        A job another worker stopped heartbeating can be taken over, so a
        retry after a crash renders once and two live workers never do. The
        returned ``claim`` is this run's proof of ownership: a worker that was
        only slow, and lost the job, can no longer report on it.
        """
        claim = uuid.uuid4().hex
        async with self._session() as db:
            row = (await db.execute(text("""
                UPDATE editor_jobs SET status = 'rendering', progress = 0, claim = :claim, updated_at = NOW()
                WHERE id = :id AND NOT cancel_requested
                  AND (status = 'queued'
                       OR (status = 'rendering' AND updated_at < NOW() - make_interval(secs => :stale)))
                RETURNING id, session_id, kind, request
            """), {"id": job_id, "claim": claim, "stale": STALE_RENDER_SECONDS})).mappings().first()
            await db.commit()
        if row is None:
            return None
        return {"id": row["id"], "session_id": row["session_id"], "kind": row["kind"],
                "request": _json(row["request"]), "claim": claim}

    async def requeue_job(self, job_id: str, claim: str) -> None:
        """Give a job this worker could not finish back to the queue's retry."""
        async with self._session() as db:
            await db.execute(text("""
                UPDATE editor_jobs SET status = 'queued', progress = 0, claim = NULL, updated_at = NOW()
                WHERE id = :id AND status = 'rendering' AND claim = :claim
            """), {"id": job_id, "claim": claim})
            await db.commit()

    async def job_exists(self, job_id: str) -> bool:
        async with self._session() as db:
            return bool((await db.execute(
                text("SELECT 1 FROM editor_jobs WHERE id = :id"), {"id": job_id}
            )).first())

    async def beat_job(self, job_id: str, claim: str, progress: int) -> bool:
        """Record progress. False tells the render to stop: cancelled, or no longer this worker's."""
        async with self._session() as db:
            row = (await db.execute(text("""
                UPDATE editor_jobs SET progress = GREATEST(progress, :progress), updated_at = NOW()
                WHERE id = :id AND status = 'rendering' AND claim = :claim RETURNING cancel_requested
            """), {"id": job_id, "claim": claim, "progress": max(0, min(99, int(progress)))})).first()
            await db.commit()
        return row is not None and not row.cancel_requested

    async def finish_job(self, job_id: str, claim: str, status: str, *, error: str | None = None, result: dict | None = None) -> bool:
        """Close this worker's job. False when it was closed or taken over by someone else."""
        async with self._session() as db:
            closed = (await db.execute(text("""
                UPDATE editor_jobs
                SET status = :status, progress = :progress, error = :error,
                    result = CAST(:result AS JSONB), updated_at = NOW()
                WHERE id = :id AND status = 'rendering' AND claim = :claim RETURNING id
            """), {"id": job_id, "claim": claim, "status": status, "progress": 100 if status == "completed" else 0,
                   "error": error, "result": json.dumps(result or {})})).first()
            await db.commit()
        return closed is not None

    async def import_legacy(self, key: str, task_id: str, clip_id: str, filename: str, legacy: dict) -> None:
        """Copy a file-based session into the tables. A row that already exists wins."""
        draft = legacy.get("draft") or {}
        async with self._session() as db:
            created = (await db.execute(text("""
                INSERT INTO editor_sessions (id, task_id, clip_id, filename, asset, draft, draft_revision, original)
                VALUES (:id, :task, :clip, :filename, CAST(:asset AS JSONB),
                        CAST(:draft AS JSONB), :revision, CAST(:original AS JSONB))
                ON CONFLICT (id) DO NOTHING RETURNING id
            """), {
                "id": key, "task": task_id, "clip": clip_id, "filename": filename,
                "asset": json.dumps(legacy["asset"]),
                "draft": json.dumps(draft["document"]) if draft.get("document") is not None else None,
                "revision": int(draft.get("revision") or 0),
                "original": json.dumps(legacy["original"]) if legacy.get("original") is not None else None,
            })).first()
            if created:
                for job in legacy.get("jobs", []):
                    await db.execute(text("""
                        INSERT INTO editor_jobs (id, session_id, kind, status, progress, error, result, request, created_at)
                        VALUES (:id, :key, :kind, :status, :progress, :error, CAST(:result AS JSONB),
                                CAST(:request AS JSONB), to_timestamp(:created))
                        ON CONFLICT (id) DO NOTHING
                    """), {
                        "id": job["id"], "key": key, "kind": job["kind"], "status": job["status"],
                        "progress": job["progress"], "error": job.get("error"),
                        "result": json.dumps(job.get("result") or {}),
                        "request": json.dumps(job.get("request") or {}), "created": job["created"],
                    })
            await db.commit()


_store: EditorStore | None = None


def get_store() -> EditorStore:
    global _store
    if _store is None:
        _store = EditorStore()
    return _store


def set_store(store) -> None:
    """Tests replace the store; None restores the PostgreSQL one."""
    global _store
    _store = store


def read_legacy_state(directory: Path) -> dict | None:
    """A session as the file-based editor left it in ``directory``, if any."""
    asset_path = directory / "asset.json"
    try:
        asset = json.loads(asset_path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(asset, dict) or asset.get("status") not in {"ready", "failed", "preparing"}:
        return None
    legacy = {"asset": asset, "draft": None, "original": None, "jobs": []}
    if asset["status"] == "preparing":
        # Whatever was preparing it wrote to that file, not to this table.
        legacy["asset"] = {"status": "failed", "error": INTERRUPTED_PREPARE}
    if asset["status"] == "ready":
        try:
            legacy["draft"] = json.loads((directory / "draft.json").read_text())
            legacy["original"] = json.loads((directory / "original.json").read_text())
        except (OSError, ValueError):
            return None
    for path in directory.glob("job-*.json"):
        job_id = path.stem[4:]
        if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
            continue
        try:
            job = json.loads(path.read_text())
            created = path.stat().st_mtime
        except (OSError, ValueError):
            continue
        request = {}
        try:
            request = json.loads((directory / f"request-{job_id}.json").read_text())
        except (OSError, ValueError):
            pass
        status = job.get("status")
        if status not in {"queued", "rendering", "completed", "cancelled", "failed"}:
            continue
        legacy["jobs"].append({
            "id": job_id,
            "kind": "combine" if "inputs" in request else "export",
            "status": status,
            "progress": max(0, min(100, int(job.get("progress") or 0))),
            "error": job.get("error"),
            "result": {"clip_id": job["clip_id"]} if job.get("clip_id") else {},
            "request": request,
            "created": created,
        })
    return legacy


async def ensure_session(key: str, directory: Path, *, task_id: str, clip_id: str, filename: str, s3_backed: bool = False) -> dict | None:
    """The stored session, importing a file-based one on first use."""
    store = get_store()
    session = await store.load(key)
    if session is not None:
        return session
    from .media_storage import MediaStorage, STORAGE_ERRORS
    from .utils.async_helpers import run_in_thread

    def legacy():
        try:
            MediaStorage().restore_legacy_editor_state(directory, s3_backed=s3_backed)
        except STORAGE_ERRORS:
            # Files already on this disk are still worth importing.
            logger.warning("Could not check object storage for an earlier editor session")
        return read_legacy_state(directory)

    found = await run_in_thread(legacy)
    if found is None:
        return None
    await store.import_legacy(key, task_id, clip_id, filename, found)
    return await store.load(key)


async def _jobs_in_queue(job_ids: list[str]) -> dict[str, bool] | None:
    """Whether each queue job still exists in Redis. None when Redis cannot say."""
    if not job_ids:
        return {}
    try:
        from arq.constants import in_progress_key_prefix, job_key_prefix
        from .workers.job_queue import JobQueue

        pool = await JobQueue.get_pool()
        pipe = pool.pipeline()
        for job_id in job_ids:
            pipe.exists(job_key_prefix + job_id, in_progress_key_prefix + job_id)
        found = await pipe.execute()
    except Exception:
        logger.warning("Could not check queued editor jobs", exc_info=True)
        return None
    return {job_id: bool(count) for job_id, count in zip(job_ids, found)}


async def read_state(key: str, session: dict | None, *, task_id: str, clip_id: str, filename: str) -> dict:
    """What the studio shows for a session. Resolves a preparation whose worker is gone."""
    if session is None:
        return {"status": "unprepared"}
    state = dict(session["asset"])
    job = state.pop("job", None)
    started = state.pop("started", False)
    if state.get("status") == "preparing":
        idle = session["idle_seconds"]
        lost = False
        if started:
            lost = idle > STALE_PREPARE_SECONDS
        elif job and idle > LOST_JOB_GRACE_SECONDS:
            lost = (await _jobs_in_queue([job]) or {}).get(job) is False
        if lost:
            await get_store().fail_prepare(key, task_id, clip_id, filename, INTERRUPTED_PREPARE, only_stale=True)
            return {"status": "failed", "error": INTERRUPTED_PREPARE}
    if state.get("status") == "ready":
        state["draft"] = session["draft"]
        state["original"] = session["original"]
    return state


async def reap_jobs(key: str) -> None:
    """Fail renders whose worker stopped, and queued jobs that are no longer in the queue."""
    store = get_store()
    active = await store.active_jobs(key)
    for job in active:
        if job["status"] == "rendering" and job["idle_seconds"] > STALE_RENDER_SECONDS:
            await store.fail_job(job["id"], INTERRUPTED_RENDER, expected="rendering")
    waiting = [job["id"] for job in active if job["status"] == "queued" and job["idle_seconds"] > LOST_JOB_GRACE_SECONDS]
    alive = await _jobs_in_queue([render_job_id(job_id) for job_id in waiting])
    if alive is None:
        return
    for job_id in waiting:
        if not alive[render_job_id(job_id)]:
            await store.fail_job(job_id, LOST_RENDER, expected="queued")


def new_job_id() -> str:
    return uuid.uuid4().hex


@dataclass
class OpenSession:
    """One clip's editing session as a request sees it."""

    key: str
    directory: Path
    task_id: str
    clip_id: str
    filename: str
    stored: dict | None

    async def state(self) -> dict:
        return await read_state(
            self.key, self.stored, task_id=self.task_id, clip_id=self.clip_id, filename=self.filename
        )


async def open_session(temp_dir: str, task_id: str, clip: dict) -> OpenSession:
    key = editor_key(task_id, clip["id"], clip["filename"])
    directory = editor_dir(temp_dir, task_id, clip["id"], clip["filename"])
    stored = await ensure_session(
        key,
        directory,
        task_id=task_id,
        clip_id=clip["id"],
        filename=clip["filename"],
        s3_backed=str(clip.get("file_path") or "").startswith("s3://"),
    )
    return OpenSession(key, directory, task_id, clip["id"], clip["filename"], stored)
