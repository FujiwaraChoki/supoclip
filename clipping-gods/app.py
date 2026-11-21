from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import re
import shutil
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import yt_dlp
from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"), override=False)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("clipping_gods")

DATA_DIR = Path(os.getenv("DATA_DIR", "data")).expanduser().resolve()
JOBS_DIR = DATA_DIR / "jobs"
DATABASE_PATH = DATA_DIR / "clipping-gods.sqlite3"
APP_PASSWORD = os.getenv("APP_PASSWORD", "")
GONKA_BASE_URL = os.getenv("GONKA_BASE_URL", "https://api.gonkarouter.io/v1").rstrip("/")
GONKA_MODEL = os.getenv("GONKA_MODEL", "zai-org/GLM-5.3-Flash")
MAX_VIDEO_BYTES = int(os.getenv("MAX_VIDEO_BYTES", str(1_000_000_000)))
MAX_VIDEO_SECONDS = int(os.getenv("MAX_VIDEO_SECONDS", "5400"))
MAX_CLIPS = int(os.getenv("MAX_CLIPS", "8"))
MAX_URL_LENGTH = 2048
TRANSCRIPTION_TIMEOUT_SECONDS = 900
SUPPORTED_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "www.youtu.be",
    "tiktok.com",
    "www.tiktok.com",
    "vm.tiktok.com",
    "vt.tiktok.com",
    "facebook.com",
    "www.facebook.com",
    "m.facebook.com",
    "fb.watch",
}

TEAM_ROLES = (
    ("hook editor", "Judge whether the opening seconds immediately earn attention."),
    ("context editor", "Judge whether a viewer can understand the moment without missing context."),
    ("story editor", "Judge whether the moment has a clear progression and payoff."),
    ("audience editor", "Judge whether the moment offers concrete value or is worth sharing."),
    ("source-fidelity editor", "Judge whether the spoken words preserve the source meaning and support the proposed clip."),
)
MIN_SUCCESSFUL_REVIEWERS = len(TEAM_ROLES) - 1

_job_lock = asyncio.Lock()
_worker_task: asyncio.Task[None] | None = None
_job_tasks: set[asyncio.Task[None]] = set()


class CreateJobRequest(BaseModel):
    url: str = Field(min_length=8, max_length=MAX_URL_LENGTH)


def detect_source(url: str) -> str | None:
    """Accept only public video URL hosts and reject credentialed or unusual URLs."""
    try:
        parsed = urlparse(url.strip())
        host = (parsed.hostname or "").lower()
        if (
            parsed.scheme.lower() != "https"
            or host not in SUPPORTED_HOSTS
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 80, 443}
        ):
            return None
    except (TypeError, ValueError):
        return None

    path = parsed.path.rstrip("/") or "/"
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        video_id = parse_qs(parsed.query).get("v", [""])[0]
        if path == "/watch":
            if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", video_id):
                return None
        elif not re.fullmatch(
            r"/(?:shorts|live|embed)/[A-Za-z0-9_-]{6,20}",
            path,
        ):
            return None
    elif host in {"youtu.be", "www.youtu.be"}:
        if not re.fullmatch(r"/[A-Za-z0-9_-]{6,20}", path):
            return None
    elif host in {"tiktok.com", "www.tiktok.com"}:
        if not (
            re.fullmatch(r"/@[A-Za-z0-9._-]+/video/\d+", path)
            or re.fullmatch(r"/t/[A-Za-z0-9_-]+", path)
        ):
            return None
    elif host in {"vm.tiktok.com", "vt.tiktok.com"}:
        if not re.fullmatch(r"/[A-Za-z0-9_-]+", path):
            return None
    elif host == "fb.watch":
        if not re.fullmatch(r"/[A-Za-z0-9_-]+", path):
            return None
    elif host in {"facebook.com", "www.facebook.com", "m.facebook.com"}:
        if not (
            (path == "/watch" and bool(re.search(r"(?:^|&)v=[^&]+", parsed.query)))
            or re.fullmatch(r"/reel/\d+", path)
            or re.fullmatch(r"/videos/\d+", path)
            or re.fullmatch(r"/[A-Za-z0-9._-]+/videos/\d+", path)
            or re.fullmatch(r"/share/[vr]/[A-Za-z0-9_-]+", path)
        ):
            return None
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}:
        return "youtube"
    if host in {"tiktok.com", "www.tiktok.com", "vm.tiktok.com", "vt.tiktok.com"}:
        return "tiktok"
    return "facebook"


def _database() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def _initialize_storage() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    with _database() as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                source TEXT NOT NULL,
                status TEXT NOT NULL,
                progress TEXT NOT NULL,
                error TEXT,
                clips_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            "UPDATE jobs SET status='queued', progress='Waiting to resume after restart', "
            "updated_at=? WHERE status='processing'",
            (_now(),),
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_app_password(
    x_app_password: str = Header(default=""),
    cg_access: str = Cookie(default=""),
) -> None:
    if APP_PASSWORD and not (
        hmac.compare_digest(x_app_password, APP_PASSWORD)
        or hmac.compare_digest(cg_access, APP_PASSWORD)
    ):
        raise HTTPException(status_code=401, detail="Enter the app password to continue.")


def _job_record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "url": row["url"],
        "source": row["source"],
        "status": row["status"],
        "progress": row["progress"],
        "error": row["error"],
        "clips": json.loads(row["clips_json"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _get_job(job_id: str) -> dict[str, Any] | None:
    with _database() as connection:
        row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return _job_record(row) if row else None


def _update_job(job_id: str, **fields: Any) -> None:
    allowed = {"status", "progress", "error", "clips_json"}
    if not fields or set(fields) - allowed:
        raise ValueError("Invalid job update fields")
    fields["updated_at"] = _now()
    assignments = ", ".join(f"{key}=?" for key in fields)
    with _database() as connection:
        cursor = connection.execute(
            f"UPDATE jobs SET {assignments} WHERE id=?",
            (*fields.values(), job_id),
        )
        if cursor.rowcount != 1:
            raise KeyError(f"Unknown job {job_id}")


def _get_queued_jobs() -> list[str]:
    with _database() as connection:
        rows = connection.execute(
            "SELECT id FROM jobs WHERE status='queued' ORDER BY created_at"
        ).fetchall()
    return [row["id"] for row in rows]


def _assert_gonka_configured() -> None:
    if not os.getenv("GONKA_API_KEY", "").strip():
        raise RuntimeError("Set GONKA_API_KEY before starting an analysis.")
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,160}", GONKA_MODEL):
        raise RuntimeError("GONKA_MODEL contains unsupported characters.")


def _download_video(url: str, job_dir: Path) -> Path:
    files_dir = job_dir / "source"
    files_dir.mkdir(parents=True, exist_ok=True)

    def limit_file_size(status: dict[str, Any]) -> None:
        byte_count = status.get("downloaded_bytes") or status.get("total_bytes") or 0
        if byte_count > MAX_VIDEO_BYTES:
            raise ValueError("This video exceeds the configured download size limit.")

    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(options) as downloader:
        metadata = downloader.extract_info(url, download=False)
    duration = metadata.get("duration")
    if duration and duration > MAX_VIDEO_SECONDS:
        raise ValueError(f"Videos must be {MAX_VIDEO_SECONDS // 60} minutes or shorter.")

    options.update({
        "format": "bestvideo*+bestaudio/best",
        "merge_output_format": "mp4",
        "outtmpl": str(files_dir / "source.%(ext)s"),
        "max_filesize": MAX_VIDEO_BYTES,
        "progress_hooks": [limit_file_size],
    })
    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=True)

    allowed_suffixes = {".mp4", ".mkv", ".mov", ".webm"}
    candidates = [
        path for path in files_dir.iterdir()
        if path.is_file() and path.suffix.lower() in allowed_suffixes
    ]
    if not candidates:
        raise FileNotFoundError("The source download finished without a video file.")
    video_path = max(candidates, key=lambda path: path.stat().st_mtime)
    if video_path.stat().st_size > MAX_VIDEO_BYTES:
        raise ValueError("This video exceeds the configured download size limit.")
    return video_path


async def _extract_audio(video_path: Path, audio_path: Path) -> None:
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-nostdin", "-y", "-i", str(video_path), "-vn",
        "-ac", "1", "-ar", "16000", "-b:a", "64k", str(audio_path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=900)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError("Audio extraction timed out.")
    if process.returncode:
        message = stderr.decode("utf-8", errors="replace")[-1200:]
        raise RuntimeError(f"Audio extraction failed: {message}")


async def _upload_audio(audio_path: Path) -> str:
    api_key = os.getenv("ASSEMBLYAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Set ASSEMBLYAI_API_KEY to transcribe imported video.")

    async def stream_file():
        with audio_path.open("rb") as audio_file:
            while chunk := audio_file.read(1024 * 1024):
                yield chunk

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            "https://api.assemblyai.com/v2/upload",
            headers={"authorization": api_key, "content-type": "application/octet-stream"},
            content=stream_file(),
        )
        response.raise_for_status()
        audio_url = response.json().get("upload_url")
    if not audio_url:
        raise RuntimeError("Transcription provider returned no audio URL.")
    return audio_url


async def _transcribe(audio_path: Path, job_id: str) -> list[dict[str, Any]]:
    api_key = os.getenv("ASSEMBLYAI_API_KEY", "").strip()
    audio_url = await _upload_audio(audio_path)
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            "https://api.assemblyai.com/v2/transcript",
            headers={"authorization": api_key},
            json={"audio_url": audio_url, "speaker_labels": False},
        )
        response.raise_for_status()
        transcript_id = response.json().get("id")
        if not transcript_id:
            raise RuntimeError("Transcription provider returned no transcript ID.")

        deadline = asyncio.get_running_loop().time() + TRANSCRIPTION_TIMEOUT_SECONDS
        while asyncio.get_running_loop().time() < deadline:
            _update_job(job_id, progress="Transcribing audio")
            await asyncio.sleep(4)
            response = await client.get(
                f"https://api.assemblyai.com/v2/transcript/{transcript_id}",
                headers={"authorization": api_key},
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("status") == "completed":
                words = payload.get("words") or []
                if not words:
                    raise RuntimeError("Transcription completed without word timestamps.")
                return words
            if payload.get("status") == "error":
                raise RuntimeError(payload.get("error") or "Transcription failed.")
        raise TimeoutError("Transcription exceeded the 15 minute processing limit.")


def _word_windows(words: list[dict[str, Any]], window_ms: int = 8 * 60 * 1000):
    window_start = 0
    group: list[dict[str, Any]] = []
    for word in words:
        if group and word["end"] - window_start > window_ms:
            yield window_start, group
            window_start = word["start"]
            group = []
        group.append(word)
    if group:
        yield window_start, group


def _transcript_text(words: list[dict[str, Any]]) -> str:
    return " ".join(str(word.get("text", "")) for word in words).strip()


def _openai_client() -> AsyncOpenAI:
    _assert_gonka_configured()
    return AsyncOpenAI(
        api_key=os.environ["GONKA_API_KEY"],
        base_url=GONKA_BASE_URL,
        timeout=120,
        max_retries=2,
    )


async def _chat_json(client: AsyncOpenAI, system: str, prompt: str) -> dict[str, Any]:
    response = await client.chat.completions.create(
        model=GONKA_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("GonkaRouter returned an empty analysis.")
    try:
        result = json.loads(content)
    except json.JSONDecodeError as error:
        raise RuntimeError("GonkaRouter returned invalid JSON.") from error
    if not isinstance(result, dict):
        raise RuntimeError("GonkaRouter returned an unexpected response shape.")
    return result


async def _select_candidates(
    client: AsyncOpenAI,
    words: list[dict[str, Any]],
    job_id: str,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    windows = list(_word_windows(words))
    scout_rules = (
        "You are a short-form video clip scout. Return a JSON object with a 'clips' array. "
        "Each item has start_seconds, end_seconds, title, and reason. Pick self-contained "
        "moments with a strong hook and payoff. Use only timestamps grounded in the "
        "transcript, and target 20 to 60 seconds per clip. Return at most 5 per transcript window."
    )
    for index, (window_start, window_words) in enumerate(windows, start=1):
        _update_job(job_id, progress=f"Finding moments · section {index} of {len(windows)}")
        start_seconds = window_start / 1000
        prompt = (
            f"Transcript window starts at {start_seconds:.1f} seconds. "
            "Every word below has an absolute millisecond start/end.\n\n"
            + "\n".join(
                f"[{word['start'] / 1000:.1f}-{word['end'] / 1000:.1f}] {word['text']}"
                for word in window_words
            )
        )
        result = await _chat_json(client, scout_rules, prompt)
        clips = result.get("clips")
        if not isinstance(clips, list):
            raise RuntimeError("GonkaRouter clip scout response is missing its clips array.")
        for clip in clips:
            if not isinstance(clip, dict):
                continue
            try:
                start = float(clip["start_seconds"])
                end = float(clip["end_seconds"])
            except (KeyError, TypeError, ValueError):
                continue
            if end - start < 15 or end - start > 75 or start < 0:
                continue
            window_end = window_words[-1]["end"] / 1000
            if start < window_start / 1000 or end > window_end:
                continue
            transcript = [
                word for word in words
                if word["end"] / 1000 > start and word["start"] / 1000 < end
            ]
            if not transcript:
                continue
            candidates.append(
                {
                    "start_seconds": start,
                    "end_seconds": end,
                    "title": str(clip.get("title", "Clip moment"))[:80],
                    "reason": str(clip.get("reason", ""))[:240],
                    "transcript": _transcript_text(transcript),
                    "score": 50,
                }
            )

    candidates.sort(key=lambda clip: clip["start_seconds"])
    deduped: list[dict[str, Any]] = []
    for candidate in candidates:
        if all(
            abs(candidate["start_seconds"] - existing["start_seconds"]) > 20
            for existing in deduped
        ):
            deduped.append(candidate)
    return deduped[:24]


async def _review_candidates(
    client: AsyncOpenAI,
    candidates: list[dict[str, Any]],
    job_id: str,
) -> list[dict[str, Any]]:
    if len(candidates) < 2:
        return candidates
    semaphore = asyncio.Semaphore(2)
    score_rows: list[list[tuple[int, int, str]]] = []

    async def review(role: str, focus: str) -> list[tuple[int, int, str]]:
        async with semaphore:
            _update_job(job_id, progress=f"Five-reviewer team · {role}")
            result = await _chat_json(
                client,
                f"You are the {role}, a careful short-video editor. Focus on {focus} "
                "Rank each candidate's spoken words, not its title. Use no assumptions "
                "about events outside the clip. Return a JSON object with 'rankings': "
                "a best-first array containing candidate_id, score (integer 0 to 100), "
                "and a short reason. Rank each candidate once.",
                json.dumps(
                    [
                        {
                            "candidate_id": index,
                            "transcript": candidate["transcript"],
                            "duration_seconds": round(candidate["end_seconds"] - candidate["start_seconds"]),
                        }
                        for index, candidate in enumerate(candidates)
                    ],
                    ensure_ascii=False,
                ),
            )
            rankings = result.get("rankings")
            if not isinstance(rankings, list):
                raise RuntimeError(f"{role} response is missing rankings.")
            parsed: list[tuple[int, int, str]] = []
            seen: set[int] = set()
            for ranking in rankings:
                if not isinstance(ranking, dict):
                    continue
                try:
                    candidate_id = int(ranking["candidate_id"])
                    score = int(ranking["score"])
                except (KeyError, TypeError, ValueError):
                    continue
                if (
                    candidate_id in seen
                    or not 0 <= candidate_id < len(candidates)
                    or not 0 <= score <= 100
                ):
                    continue
                seen.add(candidate_id)
                parsed.append((candidate_id, score, str(ranking.get("reason", ""))[:240]))
            if not parsed:
                raise RuntimeError(f"{role} response contained no usable rankings.")
            return parsed

    results = await asyncio.gather(
        *(review(role, focus) for role, focus in TEAM_ROLES),
        return_exceptions=True,
    )
    for (role, _), result in zip(TEAM_ROLES, results):
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, BaseException):
            logger.warning("Gonka reviewer %s failed (%s)", role, type(result).__name__)
            continue
        score_rows.append(result)
    if not score_rows:
        raise RuntimeError("All five specialist reviews failed.")
    if len(score_rows) < MIN_SUCCESSFUL_REVIEWERS:
        raise RuntimeError(
            f"Only {len(score_rows)} of five specialist reviews completed; "
            f"at least {MIN_SUCCESSFUL_REVIEWERS} are required."
        )

    for index, candidate in enumerate(candidates):
        ratings = [
            score
            for row in score_rows
            for candidate_id, score, _ in row
            if candidate_id == index
        ]
        if ratings:
            candidate["score"] = round(sum(ratings) / len(ratings))
    candidates.sort(key=lambda clip: clip["score"], reverse=True)
    return candidates


async def _render_clips(
    video_path: Path,
    clips: list[dict[str, Any]],
    output_dir: Path,
    job_id: str,
) -> list[dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    duration_seconds = None
    probe = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(video_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await probe.communicate()
    if probe.returncode:
        raise RuntimeError(f"Could not inspect downloaded video: {stderr.decode(errors='replace')[-600:]}")
    try:
        duration_seconds = float(stdout.decode().strip())
    except ValueError as error:
        raise RuntimeError("Downloaded video has an invalid duration.") from error

    rendered: list[dict[str, Any]] = []
    selected = clips[:MAX_CLIPS]
    for index, clip in enumerate(selected, start=1):
        start = max(0.0, min(float(clip["start_seconds"]), duration_seconds))
        end = max(start, min(float(clip["end_seconds"]), duration_seconds))
        if end - start < 15:
            continue
        filename = f"clip-{index:02d}.mp4"
        target = output_dir / filename
        _update_job(job_id, progress=f"Rendering clip {index} of {len(selected)}")
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-nostdin", "-y", "-ss", f"{start:.3f}", "-i", str(video_path),
            "-t", f"{end - start:.3f}", "-vf",
            "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(target),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=600)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError(f"Rendering clip {index} timed out.")
        if process.returncode:
            raise RuntimeError(
                f"Rendering clip {index} failed: {stderr.decode(errors='replace')[-1000:]}"
            )
        rendered.append(
            {
                **clip,
                "filename": filename,
                "url": f"/api/jobs/{job_id}/clips/{filename}",
            }
        )
    return rendered


async def _process_job(job_id: str) -> None:
    async with _job_lock:
        record = _get_job(job_id)
        if not record or record["status"] != "queued":
            return
        job_dir = JOBS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        try:
            _assert_gonka_configured()
            _update_job(job_id, status="processing", progress="Downloading public video")
            video_path = await asyncio.to_thread(_download_video, record["url"], job_dir)
            audio_path = job_dir / "source" / "audio.wav"
            _update_job(job_id, progress="Preparing audio")
            await _extract_audio(video_path, audio_path)
            words = await _transcribe(audio_path, job_id)
            client = _openai_client()
            try:
                candidates = await _select_candidates(client, words, job_id)
                if not candidates:
                    raise RuntimeError("The scout found no clip moments that meet the duration rules.")
                candidates = await _review_candidates(client, candidates, job_id)
                clips = await _render_clips(video_path, candidates, job_dir / "clips", job_id)
            finally:
                await client.close()
            if not clips:
                raise RuntimeError("No clips could be rendered from the selected moments.")
            _update_job(
                job_id,
                status="completed",
                progress=f"Ready · {len(clips)} clips",
                clips_json=json.dumps(clips, ensure_ascii=False),
            )
            shutil.rmtree(job_dir / "source", ignore_errors=True)
        except asyncio.CancelledError:
            _update_job(job_id, status="queued", progress="Waiting to resume")
            raise
        except Exception as error:
            logger.exception("Video job %s failed", job_id)
            shutil.rmtree(job_dir / "source", ignore_errors=True)
            shutil.rmtree(job_dir / "clips", ignore_errors=True)
            _update_job(
                job_id,
                status="failed",
                progress="Processing stopped",
                error=str(error)[:1000],
            )


async def _run_pending_jobs() -> None:
    for job_id in _get_queued_jobs():
        await _process_job(job_id)


def _schedule_job(job_id: str) -> None:
    task = asyncio.create_task(_process_job(job_id))
    _job_tasks.add(task)
    task.add_done_callback(_job_tasks.discard)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _worker_task
    _initialize_storage()
    _worker_task = asyncio.create_task(_run_pending_jobs())
    yield
    for task in tuple(_job_tasks):
        task.cancel()
    if _job_tasks:
        await asyncio.gather(*_job_tasks, return_exceptions=True)
    if _worker_task and not _worker_task.done():
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Clipping Gods", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def homepage() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/login", dependencies=[Depends(_require_app_password)])
async def login(request: Request, response: Response) -> dict[str, bool]:
    if APP_PASSWORD:
        response.set_cookie(
            "cg_access",
            APP_PASSWORD,
            httponly=True,
            secure=(
                request.url.scheme == "https"
                or os.getenv("COOKIE_SECURE", "").strip().lower() == "true"
            ),
            samesite="lax",
            path="/",
            max_age=60 * 60 * 12,
        )
    return {"authenticated": True}


@app.post("/api/jobs", dependencies=[Depends(_require_app_password)])
async def create_job(request: CreateJobRequest) -> dict[str, Any]:
    url = request.url.strip()
    source = detect_source(url)
    if not source:
        raise HTTPException(
            status_code=422,
            detail="Use a public YouTube, TikTok, or Facebook video URL.",
        )
    if not os.getenv("ASSEMBLYAI_API_KEY", "").strip():
        raise HTTPException(status_code=503, detail="Video transcription is not configured.")
    if not os.getenv("GONKA_API_KEY", "").strip():
        raise HTTPException(status_code=503, detail="GonkaRouter inference is not configured.")
    job_id = uuid.uuid4().hex
    now = _now()
    with _database() as connection:
        connection.execute(
            """INSERT INTO jobs (id, url, source, status, progress, created_at, updated_at)
               VALUES (?, ?, ?, 'queued', 'Waiting to start', ?, ?)""",
            (job_id, url, source, now, now),
        )
    _schedule_job(job_id)
    record = _get_job(job_id)
    if record is None:
        raise RuntimeError("The job record could not be loaded after creation.")
    return record


@app.get("/api/jobs", dependencies=[Depends(_require_app_password)])
async def list_jobs() -> list[dict[str, Any]]:
    with _database() as connection:
        rows = connection.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT 25"
        ).fetchall()
    return [_job_record(row) for row in rows]


@app.get("/api/jobs/{job_id}", dependencies=[Depends(_require_app_password)])
async def job_status(job_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise HTTPException(status_code=404, detail="Job not found.")
    record = _get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return record


@app.get(
    "/api/jobs/{job_id}/clips/{filename}",
    dependencies=[Depends(_require_app_password)],
)
async def download_clip(job_id: str, filename: str) -> FileResponse:
    if not re.fullmatch(r"[a-f0-9]{32}", job_id) or not re.fullmatch(
        r"clip-\d{2}\.mp4", filename
    ):
        raise HTTPException(status_code=404, detail="Clip not found.")
    record = _get_job(job_id)
    if record is None or filename not in {clip["filename"] for clip in record["clips"]}:
        raise HTTPException(status_code=404, detail="Clip not found.")
    target = JOBS_DIR / job_id / "clips" / filename
    if not target.is_file() or not target.resolve().is_relative_to(JOBS_DIR.resolve()):
        raise HTTPException(status_code=404, detail="Clip not found.")
    return FileResponse(target, media_type="video/mp4", filename=filename)
