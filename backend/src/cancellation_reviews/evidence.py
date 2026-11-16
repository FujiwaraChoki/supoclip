"""Evidence for one cancellation: who the customer is, what they made, and the clips themselves.

Everything here is measured or read from the database. Nothing is inferred:
an unavailable clip is recorded as unavailable with the reason, never judged.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..clip_duration import CLIP_DURATIONS, normalize_clip_length
from .schema import account_column, has_column, has_table

logger = logging.getLogger(__name__)

MAX_REVIEWED_CLIPS = 3
MAX_CLIP_BYTES = 400 * 1024 * 1024
FRAME_COUNT = 8
FFMPEG_TIMEOUT_SECONDS = 180
FULL_HD_SHORT_SIDE = 1080
CONJUNCTION_START = re.compile(r"^(and|but|so|or|because|then|which|that)\b", re.IGNORECASE)


def mask_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


async def resolve_user(db: AsyncSession, account_id: str, customer_id: str | None, source_account: str | None) -> str | None:
    """The SupoClip user behind a Stripe customer, without trusting payload metadata."""
    if not customer_id:
        return None
    account = await account_column(db)
    rows = (
        await db.execute(
            text(f"SELECT u.id, {account} AS stripe_account_id FROM users u WHERE u.stripe_customer_id = :customer"),
            {"customer": customer_id},
        )
    ).all()
    matches = [r.id for r in rows if (r.stripe_account_id or source_account) in (account_id, None, "")]
    if len(matches) == 1:
        return matches[0]
    if not await has_table(db, "stripe_account_migrations"):
        return None
    migration = (
        await db.execute(
            text(
                """
                SELECT user_id FROM stripe_account_migrations
                WHERE (source_account = :account AND source_customer = :customer)
                   OR (destination_account = :account AND destination_customer = :customer)
                LIMIT 1
                """
            ),
            {"account": account_id, "customer": customer_id},
        )
    ).first()
    return migration.user_id if migration else None


async def migration_for_subscription(db: AsyncSession, account_id: str, subscription_id: str) -> dict[str, Any] | None:
    if not await has_table(db, "stripe_account_migrations"):
        return None
    row = (
        await db.execute(
            text(
                """
                SELECT user_id, stage, cancellation_requested, review_reason,
                       (source_subscription = :subscription) AS is_source
                FROM stripe_account_migrations
                WHERE source_subscription = :subscription OR destination_subscription = :subscription
                LIMIT 1
                """
            ),
            {"subscription": subscription_id},
        )
    ).first()
    return dict(row._mapping) if row else None


async def user_context(db: AsyncSession, user_id: str) -> dict[str, Any] | None:
    acquisition = await has_table(db, "user_acquisition")
    affiliates = await has_table(db, "affiliates")
    legacy_hd = await has_column(db, "users", "legacy_hd")
    user = (
        await db.execute(
            text(
                f"""
                SELECT u.id, u.email, u."createdAt" AS created_at, u.plan, u.subscription_status, u.subscription_provider,
                       u.stripe_subscription_id, u.is_admin, u.subscription_cancel_at,
                       {"u.legacy_hd" if legacy_hd else "FALSE"} AS legacy_hd,
                       {"a.utm_source, a.ref, a.referrer_host, a.first_clip_exported_at" if acquisition else
                        "NULL AS utm_source, NULL AS ref, NULL AS referrer_host, NULL AS first_clip_exported_at"},
                       {"f.status" if affiliates else "NULL"} AS affiliate_status
                FROM users u
                {"LEFT JOIN user_acquisition a ON a.user_id = u.id" if acquisition else ""}
                {"LEFT JOIN affiliates f ON f.user_id = u.id" if affiliates else ""}
                WHERE u.id = :user_id
                """
            ),
            {"user_id": user_id},
        )
    ).first()
    if user is None:
        return None
    tasks = (
        await db.execute(
            text(
                """
                SELECT count(*) AS total,
                       count(*) FILTER (WHERE status = 'completed') AS completed,
                       count(*) FILTER (WHERE status = 'error') AS failed,
                       count(*) FILTER (WHERE status = 'cancelled') AS cancelled,
                       count(*) FILTER (WHERE created_at >= NOW() - INTERVAL '30 days') AS last_30_days,
                       min(created_at) AS first_at, max(created_at) AS last_at,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY source_duration_seconds) AS median_source_seconds,
                       count(*) FILTER (WHERE COALESCE(source_metadata->>'instructions', workflow_options->>'instructions', '') <> '')
                           AS with_instructions
                FROM tasks WHERE user_id = :user_id
                """
            ),
            {"user_id": user_id},
        )
    ).first()
    breakdowns: dict[str, dict[str, int]] = {}
    for name, expression in (
        ("errors", "t.error_code"),
        ("sources", "COALESCE(t.source_metadata->>'source_type', s.type, 'unknown')"),
        ("workflows", "t.workflow"),
        ("clip_lengths", "COALESCE(t.source_metadata->>'clip_length', 'auto')"),
        ("formats", "COALESCE(t.source_metadata->>'output_format', 'vertical')"),
        ("caption_templates", "t.caption_template"),
    ):
        where = "AND t.status = 'error'" if name == "errors" else ""
        rows = await db.execute(
            text(f"SELECT {expression} AS key, count(*) AS n FROM tasks t LEFT JOIN sources s ON s.id = t.source_id "
                 f"WHERE t.user_id = :user_id {where} GROUP BY 1"),
            {"user_id": user_id},
        )
        breakdowns[name] = {str(r.key): int(r.n) for r in rows if r.key is not None}
    activity = (
        await db.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM generated_clips g JOIN tasks t ON t.id = g.task_id WHERE t.user_id = :user_id) AS clips,
                  (SELECT round(avg(g.virality_score)) FROM generated_clips g JOIN tasks t ON t.id = g.task_id
                     WHERE t.user_id = :user_id) AS mean_virality,
                  (SELECT count(*) FROM editor_sessions e JOIN tasks t ON t.id = e.task_id WHERE t.user_id = :user_id)
                     AS editor_sessions,
                  (SELECT count(*) FROM clip_exports WHERE user_id = :user_id) AS exports,
                  (SELECT count(*) FROM social_posts WHERE user_id = :user_id) AS social_posts,
                  (SELECT count(*) FROM social_posts WHERE user_id = :user_id AND status = 'published') AS social_published
                """
            ),
            {"user_id": user_id},
        )
    ).first()
    return {
        "user": {
            "id": user.id,
            "email_masked": mask_email(user.email),
            "signed_up": user.created_at,
            "plan_now": user.plan,
            "subscription_status_now": user.subscription_status,
            "provider_now": user.subscription_provider,
            "current_subscription_id": user.stripe_subscription_id,
            "legacy_hd": bool(user.legacy_hd),
            "is_admin": bool(user.is_admin),
            "acquisition": {k: v for k, v in {
                "utm_source": user.utm_source, "ref": user.ref, "referrer": user.referrer_host,
            }.items() if v},
            "first_clip_exported_at": user.first_clip_exported_at,
            "affiliate_status": user.affiliate_status,
        },
        "tasks": {
            **{k: getattr(tasks, k) for k in ("total", "completed", "failed", "cancelled", "last_30_days", "with_instructions")},
            "first_at": tasks.first_at,
            "last_at": tasks.last_at,
            "median_source_minutes": round(tasks.median_source_seconds / 60, 1) if tasks.median_source_seconds else None,
            **breakdowns,
        },
        "activity": {k: (int(v) if v is not None else None) for k, v in activity._mapping.items()},
    }


async def candidate_clips(db: AsyncSession, user_id: str) -> list[dict[str, Any]]:
    rows = await db.execute(
        text(
            """
            SELECT g.id, g.task_id, g.file_path, g.filename, g.duration, g.text, g.hook_title, g.virality_score,
                   g.hook_score, g.engagement_score, g.value_score, g.shareability_score, g.clip_order, g.created_at,
                   g.start_time, g.end_time, g.workflow_role,
                   t.created_at AS task_created_at, t.workflow, t.caption_template, t.processing_mode, t.include_broll,
                   t.source_duration_seconds, t.source_metadata->>'clip_length' AS clip_length,
                   t.source_metadata->>'output_format' AS output_format,
                   t.source_metadata->>'source_type' AS source_type,
                   s.title AS source_title, s.type AS source_kind
            FROM generated_clips g
            JOIN tasks t ON t.id = g.task_id
            LEFT JOIN sources s ON s.id = t.source_id
            WHERE t.user_id = :user_id
            ORDER BY t.created_at DESC, g.virality_score DESC NULLS LAST, g.clip_order
            LIMIT 400
            """
        ),
        {"user_id": user_id},
    )
    return [dict(r._mapping) for r in rows]


def select_representative(clips: list[dict[str, Any]], limit: int = MAX_REVIEWED_CLIPS) -> list[tuple[str, dict[str, Any]]]:
    """Up to ``limit`` clips that show what the customer actually got.

    The best clip of their latest task (what they most likely watched), the
    weakest clip of their three latest tasks (the floor), and the best clip of an
    earlier task (consistency). Input is ordered by task recency, then score.
    """
    if not clips:
        return []
    tasks: list[str] = []
    for clip in clips:
        if clip["task_id"] not in tasks:
            tasks.append(clip["task_id"])
    chosen: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()

    def take(role: str, clip: dict[str, Any] | None) -> None:
        if clip is not None and clip["id"] not in seen and len(chosen) < limit:
            chosen.append((role, clip))
            seen.add(clip["id"])

    latest = [c for c in clips if c["task_id"] == tasks[0]]
    take("best clip of the latest task", latest[0])
    recent = [c for c in clips if c["task_id"] in tasks[:3]]
    take("weakest clip of the latest tasks", min(recent, key=lambda c: c.get("virality_score") or 0))
    if len(tasks) > 1:
        take("best clip of an earlier task", next(c for c in clips if c["task_id"] == tasks[1]))
    for clip in clips:  # fill from the latest task if the user only ever ran one
        take("another clip of the latest task", clip)
    return chosen


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_SECONDS, check=False)


def _last_float(pattern: str, text_value: str) -> float | None:
    matches = re.findall(pattern, text_value)
    return float(matches[-1]) if matches else None


def measure_clip(path: Path) -> dict[str, Any]:
    """Objective measurements of a rendered clip (ffprobe + one ffmpeg analysis pass)."""
    probe = _run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
    if probe.returncode != 0:
        return {"error": "ffprobe could not read the file"}
    info = json.loads(probe.stdout or "{}")
    streams = info.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = info.get("format") or {}
    result: dict[str, Any] = {
        "duration_seconds": round(float(fmt.get("duration") or 0), 2),
        "size_mb": round(int(fmt.get("size") or 0) / 1_000_000, 1),
        "bitrate_kbps": round(int(fmt.get("bit_rate") or 0) / 1000),
        "has_audio": audio is not None,
    }
    if video:
        rate = str(video.get("avg_frame_rate") or "0/1")
        num, _, den = rate.partition("/")
        result.update({
            "width": video.get("width"),
            "height": video.get("height"),
            "fps": round(float(num) / float(den), 2) if den and float(den) else None,
            "video_codec": video.get("codec_name"),
        })
    filters = ["-vf", "blackdetect=d=0.5:pix_th=0.10,freezedetect=n=0.003:d=2"]
    if audio:
        filters += ["-af", "ebur128=peak=true,silencedetect=noise=-45dB:d=0.8,volumedetect"]
    analysis = _run(["ffmpeg", "-nostats", "-hide_banner", "-i", str(path), *filters, "-f", "null", "-"])
    log = analysis.stderr or ""
    result["black_seconds"] = round(sum(float(x) for x in re.findall(r"black_duration:([\d.]+)", log)), 2)
    result["frozen_seconds"] = round(sum(float(x) for x in re.findall(r"freeze_duration: ([\d.]+)", log)), 2)
    if audio:
        summary = log[log.rfind("Summary:"):] if "Summary:" in log else log
        result["loudness_lufs"] = _last_float(r"I:\s+(-?[\d.]+) LUFS", summary)
        result["true_peak_dbfs"] = _last_float(r"Peak:\s+(-?[\d.]+) dBFS", summary)
        result["max_volume_db"] = _last_float(r"max_volume: (-?[\d.]+) dB", log)
        silence = sum(float(x) for x in re.findall(r"silence_duration: ([\d.]+)", log))
        duration = result["duration_seconds"] or 0
        result["silence_seconds"] = round(silence, 2)
        result["silence_percent"] = round(100 * silence / duration, 1) if duration else None
    return result


def extract_frames(path: Path, duration: float, out_dir: Path, count: int = FRAME_COUNT) -> list[tuple[float, Path]]:
    """Evenly spaced frames, including the opening (the hook) and the ending."""
    duration = max(duration, 0.6)
    points = [0.3] + [duration * k / (count - 1) for k in range(1, count - 1)] + [max(0.0, duration - 0.4)]
    frames = []
    for index, point in enumerate(points):
        target = out_dir / f"frame_{index:02d}.jpg"
        done = _run(["ffmpeg", "-v", "error", "-y", "-ss", f"{point:.2f}", "-i", str(path), "-frames:v", "1",
                     "-vf", "scale=540:-2", "-q:v", "4", str(target)])
        if done.returncode == 0 and target.exists() and target.stat().st_size > 0:
            frames.append((round(point, 1), target))
    return frames


def objective_checks(clip: dict[str, Any], metrics: dict[str, Any], expected_short_side: int | None) -> list[str]:
    """Plain comparisons of the rendered file against what the task asked for."""
    checks: list[str] = []
    width, height = metrics.get("width"), metrics.get("height")
    if width and height:
        short_side = min(width, height)
        target = expected_short_side or FULL_HD_SHORT_SIDE
        output_format = clip.get("output_format") or "vertical"
        if output_format != "original" and width > height:
            checks.append(f"landscape output {width}×{height} although the task asked for '{output_format}'")
        if short_side < target:
            checks.append(f"short side {short_side}px is below the plan's {target}px")
        else:
            checks.append(f"resolution {width}×{height} meets the {target}px plan cap")
    preset = CLIP_DURATIONS[normalize_clip_length(clip.get("clip_length"))]
    duration = metrics.get("duration_seconds") or 0
    length_name = normalize_clip_length(clip.get("clip_length"))
    if duration and not (preset.minimum - 1 <= duration <= preset.maximum + 5):
        checks.append(f"{duration:.0f}s is outside the '{length_name}' range {preset.minimum}–{preset.maximum}s")
    elif duration:
        checks.append(f"{duration:.0f}s fits the '{length_name}' range {preset.minimum}–{preset.maximum}s")
    if not metrics.get("has_audio"):
        checks.append("no audio stream")
    else:
        lufs = metrics.get("loudness_lufs")
        if lufs is not None and not (-20 <= lufs <= -9):
            checks.append(f"loudness {lufs} LUFS is outside the usual -20…-9 LUFS for short-form")
        peak = metrics.get("true_peak_dbfs")
        if peak is not None and peak > -0.5:
            checks.append(f"true peak {peak} dBFS: likely clipping")
        silence = metrics.get("silence_percent")
        if silence is not None and silence >= 20:
            checks.append(f"{silence}% of the clip is near-silent (<-45 dB for ≥0.8s)")
    if (metrics.get("black_seconds") or 0) >= 0.5:
        checks.append(f"{metrics['black_seconds']}s of black frames")
    if (metrics.get("frozen_seconds") or 0) >= 2:
        checks.append(f"{metrics['frozen_seconds']}s of frozen picture")
    words = (clip.get("text") or "").strip()
    if words:
        if words[:1].islower() or CONJUNCTION_START.match(words):
            checks.append(f"transcript opens mid-thought (“{' '.join(words.split()[:4])}…”) [heuristic]")
        if words[-1:] not in ".!?…\"'”":
            checks.append(f"transcript ends without sentence punctuation (“…{' '.join(words.split()[-4:])}”) [heuristic]")
    return checks


@dataclass
class ClipEvidence:
    role: str
    clip: dict[str, Any]
    available: bool
    unavailable_reason: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    checks: list[str] = field(default_factory=list)
    frames: list[tuple[float, Path]] = field(default_factory=list)


def fetch_and_measure(storage, role: str, clip: dict[str, Any], work_dir: Path,
                      expected_short_side: int | None) -> ClipEvidence:
    """Download one clip and measure it. Any failure leaves it marked unavailable, with why."""
    reference = clip.get("file_path") or ""
    target_dir = work_dir / clip["id"]
    target_dir.mkdir(parents=True, exist_ok=True)
    local = target_dir / "clip.mp4"
    try:
        if reference.startswith("s3://"):
            size = storage.object_size(reference)
            if size is None:
                return ClipEvidence(role, clip, False, "the file is no longer in storage (deleted or expired)")
            if size > MAX_CLIP_BYTES:
                return ClipEvidence(role, clip, False, f"the file is {size // 1_000_000} MB, over the review limit")
            if not storage.fetch(reference, local):
                return ClipEvidence(role, clip, False, "storage refused the download")
        else:
            source = Path(reference)
            if not source.is_file():
                return ClipEvidence(role, clip, False, "the file is not on this machine's disk (local storage path)")
            local = source
    except Exception as exc:  # storage outage, permissions
        logger.warning("Cancellation review could not fetch clip %s: %s", clip.get("id"), type(exc).__name__)
        return ClipEvidence(role, clip, False, f"storage error ({type(exc).__name__})")
    metrics = measure_clip(local)
    if metrics.get("error"):
        return ClipEvidence(role, clip, False, metrics["error"])
    frames = extract_frames(local, metrics.get("duration_seconds") or float(clip.get("duration") or 0), target_dir)
    if not frames:
        return ClipEvidence(role, clip, False, "no frame could be decoded", metrics=metrics)
    return ClipEvidence(role, clip, True, metrics=metrics, checks=objective_checks(clip, metrics, expected_short_side),
                        frames=frames)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
