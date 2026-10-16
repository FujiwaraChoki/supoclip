"""HTTP API: submit a download, poll it, fetch the file."""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import re
import signal
import time
import urllib.request
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlparse

import yt_dlp.version
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, Field

from .config import Settings
from .jobs import JobManager
from .routing import Router

logger = logging.getLogger("supoclip_ytdl")

VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
HEIGHTS = {360, 480, 720, 1080, 1440, 2160}
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
                 "youtube-nocookie.com", "www.youtube-nocookie.com", "youtu.be"}


def parse_video_id(value: str) -> str | None:
    value = value.strip()
    if VIDEO_ID.fullmatch(value):
        return value
    try:
        parsed = urlparse(value if "://" in value else f"https://{value}")
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host not in YOUTUBE_HOSTS:
        return None
    if host == "youtu.be":
        candidate = parsed.path.strip("/").split("/")[0]
    elif parsed.path == "/watch":
        candidate = (parse_qs(parsed.query).get("v") or [""])[0]
    else:
        parts = [p for p in parsed.path.split("/") if p]
        candidate = parts[1] if len(parts) >= 2 and parts[0] in {"shorts", "live", "embed", "v"} else ""
    return candidate if VIDEO_ID.fullmatch(candidate) else None


class DownloadRequest(BaseModel):
    url: str | None = Field(default=None, max_length=2048)
    video_id: str | None = Field(default=None, max_length=11)
    max_height: int | None = None
    max_bytes: int | None = Field(default=None, gt=0)


def latest_ytdlp_version(channel: str) -> Version | None:
    with urllib.request.urlopen("https://pypi.org/pypi/yt-dlp/json", timeout=20) as response:
        releases = json.load(response)["releases"]
    versions = []
    for raw, files in releases.items():
        try:
            version = Version(raw)
        except InvalidVersion:
            continue
        if files and not any(f.get("yanked") for f in files) and (channel == "nightly" or not version.is_prerelease):
            versions.append(version)
    return max(versions, default=None)


async def _sweep_loop(manager: JobManager) -> None:
    while True:
        await asyncio.sleep(600)
        try:
            await asyncio.to_thread(manager.sweep)
        except Exception:
            logger.exception("Cache sweep failed")


async def _update_loop(settings: Settings, manager: JobManager) -> None:
    """Restart into a newer yt-dlp; the entrypoint upgrades before starting.

    Restarts only when idle so running downloads are never cut off; after six
    hours of continuous load, stop accepting work and drain.
    """
    installed = Version(yt_dlp.version.__version__)
    while True:
        await asyncio.sleep(settings.update_check_hours * 3600)
        try:
            latest = await asyncio.to_thread(latest_ytdlp_version, settings.update_channel)
        except Exception as exc:
            logger.warning("yt-dlp update check failed: %s", exc)
            continue
        if not latest or latest <= installed:
            continue
        logger.info("yt-dlp %s is available (running %s); restarting when idle", latest, installed)
        pending_since = time.monotonic()
        while manager.active:
            if time.monotonic() - pending_since > 6 * 3600:
                manager.draining = True
            await asyncio.sleep(15)
        os.kill(os.getpid(), signal.SIGTERM)
        return


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    router = Router(settings.routes, settings.circuit_threshold, settings.circuit_cooldown_seconds)
    manager = JobManager(settings, router)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        manager.start()
        loops = [asyncio.create_task(_sweep_loop(manager))]
        if settings.update_check_hours > 0:
            loops.append(asyncio.create_task(_update_loop(settings, manager)))
        logger.info("ytdl ready: yt-dlp %s, routes %s, PO token server %s",
                    yt_dlp.version.__version__, [r.name for r in settings.routes],
                    settings.pot_base_url or "none")
        yield
        for loop in loops:
            loop.cancel()
        for job in manager.jobs.values():
            job.progress.cancel.set()

    app = FastAPI(title="SupoClip YouTube downloader", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.manager = manager
    app.state.router = router

    def authorize(request: Request) -> None:
        if not settings.api_token:
            return
        header = request.headers.get("authorization", "")
        token = header[7:] if header.lower().startswith("bearer ") else ""
        if not hmac.compare_digest(token.encode(), settings.api_token.encode()):
            raise HTTPException(status_code=401, detail="Unauthorized")

    def job_or_404(job_id: str):
        job = manager.jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Unknown download")
        return job

    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "yt_dlp": yt_dlp.version.__version__, "draining": manager.draining}

    @app.post("/v1/downloads", dependencies=[Depends(authorize)])
    async def create_download(body: DownloadRequest):
        if manager.draining:
            return JSONResponse({"detail": "Restarting for an update"}, status_code=503,
                                headers={"Retry-After": "30"})
        video_id = parse_video_id(body.video_id or body.url or "")
        if not video_id:
            raise HTTPException(status_code=422, detail="Not a YouTube video link")
        max_height = body.max_height or settings.default_max_height
        if max_height not in HEIGHTS:
            raise HTTPException(status_code=422, detail=f"max_height must be one of {sorted(HEIGHTS)}")
        max_bytes = min(body.max_bytes or settings.max_bytes, settings.max_bytes)
        job = manager.submit(video_id, max_height, max_bytes)
        return JSONResponse(job.public(), status_code=200 if job.status == "completed" else 202)

    @app.get("/v1/downloads/{job_id}", dependencies=[Depends(authorize)])
    async def get_download(job_id: str):
        return job_or_404(job_id).public()

    @app.get("/v1/downloads/{job_id}/file", dependencies=[Depends(authorize)])
    async def get_file(job_id: str):
        job = job_or_404(job_id)
        if job.status != "completed":
            raise HTTPException(status_code=409, detail=f"Download is {job.status}")
        if not job.path or not job.path.exists():
            raise HTTPException(status_code=410, detail="Download expired; submit it again")
        return FileResponse(job.path, media_type="video/mp4", filename=f"{job.video_id}.mp4")

    @app.delete("/v1/downloads/{job_id}", dependencies=[Depends(authorize)])
    async def cancel_download(job_id: str):
        job = job_or_404(job_id)
        manager.cancel(job)
        return job.public()

    @app.get("/v1/stats", dependencies=[Depends(authorize)])
    async def stats():
        return {
            "yt_dlp": yt_dlp.version.__version__,
            "active": manager.active,
            "completed": manager.completed,
            "failed": manager.failed,
            "cache_hits": manager.cache_hits,
            "cache_bytes": await asyncio.to_thread(manager.cache_bytes),
            "routes": router.snapshot(),
        }

    return app


def main() -> None:
    import uvicorn

    logging.basicConfig(level=os.getenv("YTDL_LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings()
    if not settings.api_token and os.getenv("YTDL_ALLOW_UNAUTHENTICATED", "").lower() != "true":
        raise SystemExit("Set YTDL_API_TOKEN (or YTDL_ALLOW_UNAUTHENTICATED=true for local use)")
    uvicorn.run(create_app(settings), host=os.getenv("YTDL_HOST", "0.0.0.0"),
                port=int(os.getenv("YTDL_PORT", "8080")), access_log=False)
