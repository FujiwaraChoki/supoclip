"""In-memory stand-in for ``src.editor_state.EditorStore``.

Unit tests of the studio run without PostgreSQL. This keeps the same
contract as the real store; ``tests/integration/test_editor_state.py`` checks
the real one against a database.
"""

from __future__ import annotations

import copy
import time
import uuid

from src.editor_state import MAX_ACTIVE_JOBS, STALE_RENDER_SECONDS, editor_key, present_job, read_state


class MemoryEditorStore:
    def __init__(self):
        self.sessions: dict[str, dict] = {}
        self.jobs_by_id: dict[str, dict] = {}

    def _touch(self, key):
        self.sessions[key]["updated"] = time.time()

    async def load(self, key):
        row = self.sessions.get(key)
        if row is None:
            return None
        draft = row["draft"]
        return copy.deepcopy({
            "asset": row["asset"],
            "draft": None if draft is None else {"revision": row["revision"], "document": draft},
            "original": row["original"],
            "idle_seconds": time.time() - row["updated"],
        })

    def _upsert(self, key, task_id, clip_id, filename):
        return self.sessions.setdefault(key, {
            "task_id": task_id, "clip_id": clip_id, "filename": filename,
            "asset": None, "draft": None, "revision": 0, "original": None, "updated": time.time(),
        })

    async def claim_prepare(self, key, task_id, clip_id, filename, job):
        row = self.sessions.get(key)
        if row and row["asset"]["status"] in {"ready", "preparing"}:
            return row["asset"]["status"]
        self._upsert(key, task_id, clip_id, filename)["asset"] = {"status": "preparing", "job": job}
        self._touch(key)
        return "claimed"

    async def begin_prepare(self, key, task_id, clip_id, filename):
        row = self._upsert(key, task_id, clip_id, filename)
        if not row["asset"] or row["asset"]["status"] != "ready":
            job = (row["asset"] or {}).get("job")
            row["asset"] = {"status": "preparing", "started": True, **({"job": job} if job else {})}
        self._touch(key)

    async def touch_prepare(self, key):
        if key in self.sessions and self.sessions[key]["asset"]["status"] == "preparing":
            self._touch(key)

    async def finish_prepare(self, key, task_id, clip_id, filename, asset, document):
        row = self._upsert(key, task_id, clip_id, filename)
        row["asset"] = copy.deepcopy(asset)
        if row["draft"] is None:
            row["draft"] = copy.deepcopy(document)
        if row["original"] is None:
            row["original"] = copy.deepcopy(document)
        self._touch(key)

    async def fail_prepare(self, key, task_id, clip_id, filename, error, *, only_stale=False):
        row = self.sessions.get(key)
        if row and row["asset"]:
            status = row["asset"]["status"]
            if status == "ready" or (only_stale and status != "preparing"):
                return
        self._upsert(key, task_id, clip_id, filename)["asset"] = {"status": "failed", "error": error}
        self._touch(key)

    async def save_draft(self, key, document, revision):
        row = self.sessions.get(key)
        if not row or row["revision"] != revision or row["asset"]["status"] != "ready":
            return None
        row["draft"] = copy.deepcopy(document)
        row["revision"] += 1
        self._touch(key)
        return row["revision"]

    async def delete(self, key):
        self.sessions.pop(key, None)
        for job_id in [j for j, job in self.jobs_by_id.items() if job["session_id"] == key]:
            del self.jobs_by_id[job_id]

    async def create_job(self, key, job_id, kind, request, *, limit=MAX_ACTIVE_JOBS):
        active = [j for j in self.jobs_by_id.values() if j["session_id"] == key and j["status"] in {"queued", "rendering"}]
        if len(active) >= limit:
            return False
        now = time.time()
        self.jobs_by_id[job_id] = {
            "id": job_id, "session_id": key, "kind": kind, "status": "queued", "progress": 0,
            "error": None, "result": {}, "request": copy.deepcopy(request),
            "cancel_requested": False, "created": now, "updated": now,
        }
        return True

    async def job(self, key, job_id):
        job = self.jobs_by_id.get(job_id)
        return present_job(job) if job and job["session_id"] == key else None

    async def jobs(self, key, limit=20):
        rows = sorted(
            (j for j in self.jobs_by_id.values() if j["session_id"] == key),
            key=lambda j: j["created"], reverse=True,
        )
        return [present_job(job) for job in rows[:limit]]

    async def active_jobs(self, key):
        now = time.time()
        return [
            {"id": j["id"], "status": j["status"], "idle_seconds": now - j["updated"]}
            for j in self.jobs_by_id.values()
            if j["session_id"] == key and j["status"] in {"queued", "rendering"}
        ]

    async def fail_job(self, job_id, error, *, expected):
        job = self.jobs_by_id.get(job_id)
        if not job or job["status"] != expected:
            return False
        job.update(status="failed", progress=0, error=error, updated=time.time())
        return True

    async def cancel_job(self, key, job_id):
        job = self.jobs_by_id.get(job_id)
        if not job or job["session_id"] != key:
            return None
        if job["status"] == "queued":
            job.update(status="cancelled", progress=0, cancel_requested=True, updated=time.time())
            return "cancelled"
        if job["status"] == "rendering":
            job["cancel_requested"] = True
            return "cancelling"
        return job["status"]

    async def start_job(self, job_id):
        job = self.jobs_by_id.get(job_id)
        if not job or job["cancel_requested"]:
            return None
        stale = job["status"] == "rendering" and time.time() - job["updated"] > STALE_RENDER_SECONDS
        if job["status"] != "queued" and not stale:
            return None
        job.update(status="rendering", progress=0, claim=uuid.uuid4().hex, updated=time.time())
        return copy.deepcopy({k: job[k] for k in ("id", "session_id", "kind", "request", "claim")})

    def _owned(self, job_id, claim):
        job = self.jobs_by_id.get(job_id)
        return job if job and job["status"] == "rendering" and job.get("claim") == claim else None

    async def requeue_job(self, job_id, claim):
        job = self._owned(job_id, claim)
        if job:
            job.update(status="queued", progress=0, claim=None, updated=time.time())

    async def job_exists(self, job_id):
        return job_id in self.jobs_by_id

    async def beat_job(self, job_id, claim, progress):
        job = self._owned(job_id, claim)
        if not job:
            return False
        job.update(progress=max(job["progress"], max(0, min(99, int(progress)))), updated=time.time())
        return not job["cancel_requested"]

    async def finish_job(self, job_id, claim, status, *, error=None, result=None):
        job = self._owned(job_id, claim)
        if not job:
            return False
        job.update(status=status, progress=100 if status == "completed" else 0, error=error,
                   result=copy.deepcopy(result or {}), updated=time.time())
        return True

    async def import_legacy(self, key, task_id, clip_id, filename, legacy):
        if key in self.sessions:
            return
        draft = legacy.get("draft") or {}
        self.sessions[key] = {
            "task_id": task_id, "clip_id": clip_id, "filename": filename,
            "asset": copy.deepcopy(legacy["asset"]), "draft": copy.deepcopy(draft.get("document")),
            "revision": int(draft.get("revision") or 0), "original": copy.deepcopy(legacy.get("original")),
            "updated": time.time(),
        }
        for job in legacy.get("jobs", []):
            self.jobs_by_id.setdefault(job["id"], {
                "id": job["id"], "session_id": key, "kind": job["kind"], "status": job["status"],
                "progress": job["progress"], "error": job.get("error"), "result": job.get("result") or {},
                "request": job.get("request") or {}, "cancel_requested": False,
                "created": job["created"], "updated": time.time(),
            })

    # Test helpers, not part of the store contract.

    def put_ready(self, task_id, clip_id, filename, asset, document, *, revision=0, original=None):
        """Seed a prepared session, as ``finish_prepare`` would leave it."""
        key = editor_key(task_id, clip_id, filename)
        self.sessions[key] = {
            "task_id": task_id, "clip_id": clip_id, "filename": filename,
            "asset": {"status": "ready", **asset}, "draft": copy.deepcopy(document), "revision": revision,
            "original": copy.deepcopy(original if original is not None else document), "updated": time.time(),
        }
        return key


async def stored_state(task_id, clip_id, filename, store=None):
    """The studio's view of a session, for assertions."""
    from src.editor_state import get_store

    store = store or get_store()
    key = editor_key(task_id, clip_id, filename)
    return await read_state(key, await store.load(key), task_id=task_id, clip_id=clip_id, filename=filename)
