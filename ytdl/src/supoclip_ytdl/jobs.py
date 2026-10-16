"""Job queue, de-duplication and the on-disk result cache."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings
from .downloader import Progress, Result, download
from .errors import Cancelled, DownloadFailed, Failure
from .routing import Router

logger = logging.getLogger(__name__)

ACTIVE = {"queued", "running"}


@dataclass
class Job:
    id: str
    video_id: str
    max_height: int
    max_bytes: int
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    error_code: str | None = None
    error: str | None = None
    cached: bool = False
    meta: dict = field(default_factory=dict)
    path: Path | None = None
    progress: Progress = field(default_factory=Progress)

    @property
    def key(self) -> str:
        return cache_key(self.video_id, self.max_height, self.max_bytes)

    def public(self) -> dict:
        p = self.progress
        return {
            "id": self.id,
            "video_id": self.video_id,
            "max_height": self.max_height,
            "status": self.status,
            "stage": p.stage if self.status == "running" else self.status,
            "cached": self.cached,
            "attempt": p.attempt,
            "route": p.route,
            "downloaded_bytes": p.downloaded_bytes,
            "total_bytes": p.total_bytes,
            "error_code": self.error_code,
            "error": self.error,
            "result": self.meta or None,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


def cache_key(video_id: str, max_height: int, max_bytes: int) -> str:
    return f"{video_id}-{max_height}-{max_bytes}"


class JobManager:
    def __init__(self, settings: Settings, router: Router):
        self.settings = settings
        self.router = router
        self.jobs: dict[str, Job] = {}
        self.by_key: dict[str, str] = {}
        self.draining = False
        self.completed = 0
        self.failed = 0
        self.cache_hits = 0
        self._semaphore = asyncio.Semaphore(settings.max_concurrent)
        self._tasks: set[asyncio.Task] = set()

    def start(self) -> None:
        self.settings.cache_dir.mkdir(parents=True, exist_ok=True)
        # In-flight work does not survive a restart; clients resubmit.
        shutil.rmtree(self.settings.work_dir, ignore_errors=True)
        self.settings.work_dir.mkdir(parents=True, exist_ok=True)

    @property
    def active(self) -> int:
        return sum(1 for job in self.jobs.values() if job.status in ACTIVE)

    def submit(self, video_id: str, max_height: int, max_bytes: int) -> Job:
        key = cache_key(video_id, max_height, max_bytes)
        existing = self.jobs.get(self.by_key.get(key, ""))
        if existing and existing.status in ACTIVE:
            return existing
        if existing and existing.status == "completed" and existing.path and existing.path.exists():
            if existing.path.stat().st_size <= max_bytes:
                self._touch(key)
                self.cache_hits += 1
                return existing
        job = Job(id=uuid.uuid4().hex, video_id=video_id, max_height=max_height, max_bytes=max_bytes)
        cached = self._load_cached(key, max_bytes)
        if cached:
            job.status, job.cached, job.path, job.meta = "completed", True, *cached
            job.finished_at = time.time()
            self.cache_hits += 1
        self.jobs[job.id] = job
        self.by_key[key] = job.id
        if not cached:
            task = asyncio.get_running_loop().create_task(self._run(job))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return job

    def cancel(self, job: Job) -> None:
        job.progress.cancel.set()

    async def _run(self, job: Job) -> None:
        async with self._semaphore:
            if job.progress.cancel.is_set():
                self._finish(job, "cancelled", "cancelled", "Download cancelled.")
                return
            job.status = "running"
            work = self.settings.work_dir / job.id
            try:
                result: Result = await asyncio.to_thread(
                    download, job.video_id, job.max_height, job.max_bytes, self.settings,
                    self.router, job.progress, work)
                job.path, job.meta = self._store(job, result)
                job.status, job.finished_at = "completed", time.time()
                self.completed += 1
                logger.info("[%s] completed via %s in %s attempt(s): %s bytes, %s MB transferred",
                            job.video_id, result.route, result.attempts, result.size,
                            round(result.transferred_bytes / 1e6, 1))
            except Cancelled:
                self._finish(job, "cancelled", "cancelled", "Download cancelled.")
            except DownloadFailed as exc:
                self._finish(job, "failed", exc.failure.code, exc.failure.message)
            except Exception:
                logger.exception("[%s] unexpected download failure", job.video_id)
                self._finish(job, "failed", "internal", "YouTube download failed.")
            finally:
                shutil.rmtree(work, ignore_errors=True)

    def _finish(self, job: Job, status: str, code: str, message: str) -> None:
        job.status, job.error_code, job.error = status, code, message
        job.finished_at = time.time()
        if status == "failed":
            self.failed += 1
            logger.warning("[%s] failed: %s", job.video_id, code)

    def _store(self, job: Job, result: Result) -> tuple[Path, dict]:
        if result.size > job.max_bytes:
            raise DownloadFailed(Failure("too_large", "fatal", "This YouTube video is too large."))
        entry = self.settings.cache_dir / job.key
        staging = self.settings.cache_dir / f".{job.key}.{job.id}"
        staging.mkdir(parents=True, exist_ok=True)
        target = staging / "video.mp4"
        shutil.move(str(result.path), target)
        meta = {
            "title": result.title,
            "duration": result.duration,
            "width": result.width,
            "height": result.height,
            "size": result.size,
            "format_id": result.format_id,
            "route": result.route,
            "attempts": result.attempts,
            "transferred_bytes": result.transferred_bytes,
        }
        (staging / "meta.json").write_text(json.dumps(meta))
        shutil.rmtree(entry, ignore_errors=True)
        staging.rename(entry)
        return entry / "video.mp4", meta

    def _load_cached(self, key: str, max_bytes: int) -> tuple[Path, dict] | None:
        entry = self.settings.cache_dir / key
        video, meta_path = entry / "video.mp4", entry / "meta.json"
        try:
            if time.time() - entry.stat().st_mtime > self.settings.cache_ttl_seconds:
                return None
            meta = json.loads(meta_path.read_text())
            if video.stat().st_size != meta.get("size") or meta["size"] > max_bytes:
                return None
        except (OSError, ValueError, KeyError):
            return None
        self._touch(key)
        return video, meta

    def _touch(self, key: str) -> None:
        try:
            os.utime(self.settings.cache_dir / key)
        except OSError:
            pass

    def sweep(self) -> None:
        """Expire old cache entries, enforce the size budget, forget old job records."""
        now = time.time()
        busy = {job.key for job in self.jobs.values() if job.status in ACTIVE}
        entries = []
        for entry in self.settings.cache_dir.iterdir():
            if not entry.is_dir():
                continue
            if entry.name.startswith("."):
                if now - entry.stat().st_mtime > 3600:
                    shutil.rmtree(entry, ignore_errors=True)
                continue
            age = now - entry.stat().st_mtime
            size = sum(f.stat().st_size for f in entry.iterdir() if f.is_file())
            if age > self.settings.cache_ttl_seconds and entry.name not in busy:
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entries.append((entry.stat().st_mtime, size, entry))
        total = sum(size for _, size, _ in entries)
        for _, size, entry in sorted(entries):
            if total <= self.settings.cache_max_bytes:
                break
            if entry.name not in busy:
                shutil.rmtree(entry, ignore_errors=True)
                total -= size
        for job_id, job in list(self.jobs.items()):
            if job.finished_at and now - job.finished_at > self.settings.cache_ttl_seconds:
                del self.jobs[job_id]
                if self.by_key.get(job.key) == job_id:
                    del self.by_key[job.key]

    def cache_bytes(self) -> int:
        total = 0
        for entry in self.settings.cache_dir.glob("*/video.mp4"):
            try:
                total += entry.stat().st_size
            except OSError:
                pass
        return total
