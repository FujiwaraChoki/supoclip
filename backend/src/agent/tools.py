"""What the agent can do, as Claude tools.

Each tool calls SupoClip's own API as the person (``AgentAPI``), so a tool can
never do more than the person could in the app. A handler returns two things:
compact JSON for the model, and a card for the page (clip thumbnails, a post
on the calendar, an edit in progress). Card shapes are mirrored in
``frontend/src/lib/agent.ts``.

Times are local to the person (``timeutil``). Actions that publish right away
or delete something wait for the person's approval (``APPROVAL_TOOLS``).
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import text

from ..database import AsyncSessionLocal
from . import store
from .api_client import AgentAPI, AgentAPIError, _detail_text
from .timeutil import local_text, parse_any, parse_local

logger = logging.getLogger(__name__)

PLATFORMS = ("youtube", "tiktok", "instagram")
WORKFLOWS = ("highlights", "series", "topic", "recap", "trailer")
CLIP_LENGTHS = ("auto", "short", "medium", "long")
OUTPUT_FORMATS = ("vertical", "vertical_fit", "vertical_pan", "vertical_split", "original")
MAX_RESULT_CHARS = 14000
# Studio edits and the re-renders behind trims and restyles take longer than a lookup.
SLOW_TIMEOUT_SECONDS = 600.0

# Typical engagement peaks (local time) when the creator has no measured posts yet.
DEFAULT_SLOTS = {
    "tiktok": ["12:00", "19:00", "21:00"],
    "youtube": ["15:00", "18:00", "11:00"],
    "instagram": ["11:00", "13:00", "19:00"],
    None: ["12:00", "18:00", "20:00"],
}


class ToolError(Exception):
    """A problem the model should read and work around (wrong id, missing account...)."""


@dataclass
class ToolContext:
    app: Any
    api: AgentAPI
    user_id: str
    team_id: Optional[str]
    conversation_id: str
    run_id: str
    tz: ZoneInfo
    # Starts a follow-up turn for background work (set by the runtime).
    on_job_created: Optional[Callable[[dict], Awaitable[None]]] = None


@dataclass
class ToolOutcome:
    content: Any
    card: Optional[dict] = None
    ok: bool = True

    def model_text(self) -> str:
        body = self.content if isinstance(self.content, str) else json.dumps(self.content, default=str)
        if len(body) > MAX_RESULT_CHARS:
            body = body[:MAX_RESULT_CHARS] + "… (truncated: ask for fewer items or one id)"
        return body


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    handler: Callable[[ToolContext, dict], Awaitable[ToolOutcome]]
    needs_approval: Callable[[dict], bool] = field(default=lambda _input: False)

    def definition(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.schema}


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


# ----------------------------------------------------------------------
# Input helpers
# ----------------------------------------------------------------------
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _ID.match(value.strip()):
        raise ToolError(f"{name} must be an id from an earlier result.")
    return value.strip()


def _str(value: Any, name: str, *, max_len: int, required: bool = False) -> Optional[str]:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ToolError(f"{name} is required.")
        return None
    if not isinstance(value, str):
        raise ToolError(f"{name} must be text.")
    cleaned = value.strip()
    if len(cleaned) > max_len:
        raise ToolError(f"{name} must be at most {max_len} characters.")
    return cleaned


def _int(value: Any, name: str, *, low: int, high: int, default: Optional[int] = None) -> Optional[int]:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolError(f"{name} must be a number.")
    return max(low, min(high, int(value)))


def _choice(value: Any, name: str, choices: tuple, default: Any = None) -> Any:
    if value is None:
        return default
    if value not in choices:
        raise ToolError(f"{name} must be one of: {', '.join(str(c) for c in choices if c is not None)}.")
    return value


def _excerpt(value: Optional[str], limit: int = 180) -> str:
    text_value = " ".join((value or "").split())
    return text_value if len(text_value) <= limit else text_value[: limit - 1].rstrip() + "…"


async def _call(coro) -> Any:
    try:
        return await coro
    except AgentAPIError as exc:
        raise ToolError(_detail_text(exc.detail) + f" (HTTP {exc.status})") from None


# ----------------------------------------------------------------------
# Shapes shared by several tools
# ----------------------------------------------------------------------
def _project_summary(task: dict, tz: ZoneInfo) -> dict:
    return {
        "id": task.get("id"),
        "title": task.get("source_title") or "Untitled video",
        "status": task.get("status"),
        "workflow": task.get("workflow") or "highlights",
        "clips": task.get("clips_count"),
        "top_score": task.get("top_virality_score"),
        "created": local_text(task.get("created_at"), tz),
        **({"error": _excerpt(task.get("progress_message"), 200)} if task.get("status") == "error" else {}),
    }


def _project_card_item(task: dict) -> dict:
    return {
        "id": task.get("id"),
        "title": task.get("source_title") or "Untitled video",
        "status": task.get("status"),
        "clips_count": task.get("clips_count") or 0,
        "cover_clip_id": task.get("cover_clip_id"),
        "created_at": task.get("created_at"),
        "workflow": task.get("workflow") or "highlights",
    }


def _post_summary(post: dict, tz: ZoneInfo) -> dict:
    when = post.get("scheduled_for") or post.get("published_at") or post.get("created_at")
    account = post.get("account") or {}
    summary = {
        "post_id": post.get("id"),
        "platform": post.get("provider"),
        "account": account.get("display_name") or account.get("username"),
        "status": post.get("status"),
        "when": local_text(when, tz),
        "title": post.get("title"),
        "project_id": post.get("task_id"),
        "clip_id": post.get("clip_id"),
    }
    if post.get("external_url"):
        summary["url"] = post["external_url"]
    if post.get("error_message"):
        summary["error"] = _excerpt(post["error_message"], 200)
    metrics = post.get("metrics") or {}
    if metrics.get("views") is not None:
        summary["views"] = metrics.get("views")
        summary["likes"] = metrics.get("likes")
    return summary


def _post_card_item(post: dict) -> dict:
    account = post.get("account") or {}
    return {
        "id": post.get("id"),
        "provider": post.get("provider"),
        "status": post.get("status"),
        "scheduled_for": post.get("scheduled_for"),
        "published_at": post.get("published_at"),
        "created_at": post.get("created_at"),
        "title": post.get("title"),
        "caption": post.get("caption"),
        "task_id": post.get("task_id"),
        "clip_id": post.get("clip_id"),
        "external_url": post.get("external_url"),
        "error_message": post.get("error_message"),
        "account": {"display_name": account.get("display_name"), "username": account.get("username")},
        "metrics": post.get("metrics"),
    }


async def _connections(ctx: ToolContext) -> list[dict]:
    try:
        data = await ctx.api.get("/social/connections")
    except AgentAPIError as exc:
        if exc.status == 403:
            return []
        raise ToolError(_detail_text(exc.detail)) from None
    return data.get("connections") or []


async def _resolve_account(ctx: ToolContext, account_id: Optional[str], platform: Optional[str]) -> dict:
    connections = [c for c in await _connections(ctx) if c.get("status") != "revoked"]
    if not connections:
        raise ToolError(
            "No social accounts are connected. The person can connect YouTube, TikTok or Instagram in "
            "Settings → Social (/settings/social)."
        )
    if account_id:
        for connection in connections:
            if connection.get("id") == account_id:
                return connection
        raise ToolError("That account isn't connected. Call get_overview for the connected accounts.")
    if not platform:
        if len(connections) == 1:
            return connections[0]
        raise ToolError("Several accounts are connected: pass account_id or platform.")
    matching = [c for c in connections if c.get("provider") == platform]
    if not matching:
        raise ToolError(f"No {platform} account is connected. Connected: {', '.join(sorted({c['provider'] for c in connections}))}.")
    if len(matching) > 1:
        names = ", ".join(f"{c.get('display_name') or c.get('username')} ({c['id']})" for c in matching)
        raise ToolError(f"Several {platform} accounts are connected; pass account_id. Accounts: {names}.")
    return matching[0]


def _account_summary(connection: dict) -> dict:
    return {
        "account_id": connection.get("id"),
        "platform": connection.get("provider"),
        "name": connection.get("display_name") or connection.get("username"),
        "status": connection.get("status"),
    }


# ----------------------------------------------------------------------
# Account & library
# ----------------------------------------------------------------------
async def get_overview(ctx: ToolContext, _input: dict) -> ToolOutcome:
    billing = await _call(ctx.api.get("/tasks/billing/summary"))
    try:
        providers = await ctx.api.get("/social/providers")
    except AgentAPIError:
        providers = {"enabled": False, "providers": []}
    connections = await _connections(ctx) if providers.get("enabled") else []
    try:
        workflows = await ctx.api.get("/tasks/workflows")
    except AgentAPIError:
        workflows = {"workflows": []}
    memories = await store.list_memories(ctx.user_id)
    overview = {
        "plan": billing.get("plan"),
        "workspace": billing.get("team_name") or ("team" if billing.get("workspace_type") == "team" else "personal"),
        "minutes_used_this_month": billing.get("usage_minutes"),
        "minutes_left_this_month": billing.get("remaining_minutes"),
        "max_video_minutes": (
            round(billing["max_youtube_duration_seconds"] / 60) if billing.get("max_youtube_duration_seconds") else None
        ),
        "can_create_project": billing.get("can_create_task"),
        "clip_resolution": f"{billing['output_short_side']}p" if billing.get("output_short_side") else "full",
        "social_publishing": bool(providers.get("enabled")),
        "can_schedule": bool(providers.get("can_schedule")),
        "platforms": [
            {"platform": p.get("provider"), "available": bool(p.get("configured")), **({"paused": True} if p.get("paused") else {})}
            for p in providers.get("providers") or []
        ],
        "connected_accounts": [_account_summary(c) for c in connections],
        "workflows": [w["id"] for w in workflows.get("workflows", []) if w.get("available")],
        "remembered": [m["note"] for m in memories],
    }
    return ToolOutcome(overview)


async def list_projects(ctx: ToolContext, data: dict) -> ToolOutcome:
    status = _choice(data.get("status"), "status", ("queued", "processing", "completed", "error", "cancelled"))
    query = (_str(data.get("query"), "query", max_len=120) or "").lower()
    limit = _int(data.get("limit"), "limit", low=1, high=50, default=12)
    response = await _call(ctx.api.get("/tasks/", limit=100))
    tasks = response.get("tasks") or []
    if status:
        tasks = [t for t in tasks if t.get("status") == status]
    if query:
        tasks = [t for t in tasks if query in (t.get("source_title") or "").lower()]
    tasks = tasks[:limit]
    return ToolOutcome(
        {"projects": [_project_summary(t, ctx.tz) for t in tasks], "shown": len(tasks)},
        card={"type": "projects", "items": [_project_card_item(t) for t in tasks]} if tasks else None,
    )


async def get_project(ctx: ToolContext, data: dict) -> ToolOutcome:
    project_id = _id(data.get("project_id"), "project_id")
    task = await _call(ctx.api.get(f"/tasks/{project_id}"))
    clips = sorted(task.get("clips") or [], key=lambda c: (c.get("clip_order") or 0))
    posts_by_clip: dict[str, list] = defaultdict(list)
    try:
        posts = (await ctx.api.get("/social/posts", task_id=project_id, limit=200)).get("posts") or []
        for post in posts:
            if post.get("status") != "cancelled":
                posts_by_clip[post.get("clip_id")].append(
                    {"platform": post.get("provider"), "status": post.get("status"),
                     "when": local_text(post.get("scheduled_for") or post.get("published_at"), ctx.tz),
                     "post_id": post.get("id")}
                )
    except AgentAPIError:
        pass
    clip_rows = []
    for clip in clips:
        clip_rows.append(
            {
                "clip_id": clip.get("id"),
                "n": clip.get("clip_order"),
                "title": clip.get("hook_title"),
                "score": clip.get("virality_score"),
                "hook_type": clip.get("hook_type"),
                "seconds": round(float(clip.get("duration") or 0), 1),
                **({"part": clip.get("part_number")} if clip.get("part_number") else {}),
                "says": _excerpt(clip.get("text"), 220),
                **({"posts": posts_by_clip[clip["id"]]} if posts_by_clip.get(clip.get("id")) else {}),
            }
        )
    result = {
        **_project_summary({**task, "clips_count": len(clips)}, ctx.tz),
        "progress": task.get("progress"),
        "progress_message": task.get("progress_message") if task.get("status") in {"queued", "processing"} else None,
        "caption_template": task.get("caption_template"),
        # The video it came from: pass it to create_project to make something else from the same video.
        "source_url": task.get("source_url"),
        "clips": clip_rows,
    }
    card = {
        "type": "project",
        "project": {
            "id": task.get("id"),
            "title": task.get("source_title") or "Untitled video",
            "status": task.get("status"),
            "progress": task.get("progress"),
            "clips": [
                {
                    "id": c.get("id"),
                    "title": c.get("hook_title"),
                    "score": c.get("virality_score"),
                    "duration": c.get("duration"),
                    "order": c.get("clip_order"),
                }
                for c in clips
            ],
        },
    }
    return ToolOutcome(result, card=card)


async def find_clips(ctx: ToolContext, data: dict) -> ToolOutcome:
    """Clips across the whole library, straight from SQL (scoped like the task list)."""
    query = _str(data.get("query"), "query", max_len=120)
    min_score = _int(data.get("min_score"), "min_score", low=0, high=100)
    unposted = data.get("unposted_only") is True
    sort = _choice(data.get("sort"), "sort", ("score", "recent"), "score")
    limit = _int(data.get("limit"), "limit", low=1, high=40, default=12)
    scope = (
        # Still a member: a turn started before someone left the team must not keep reading its clips.
        "t.team_id = :team_id AND EXISTS (SELECT 1 FROM team_members tm WHERE tm.team_id = :team_id AND tm.user_id = :user_id)"
        if ctx.team_id
        else "t.user_id = :user_id AND t.team_id IS NULL"
    )
    clauses = [scope, "t.status = 'completed'"]
    params: dict[str, Any] = {"user_id": ctx.user_id, "team_id": ctx.team_id, "limit": limit}
    if query:
        clauses.append("(c.text ILIKE :q OR c.hook_title ILIKE :q OR s.title ILIKE :q)")
        params["q"] = f"%{query}%"
    if min_score is not None:
        clauses.append("COALESCE(c.virality_score, 0) >= :min_score")
        params["min_score"] = min_score
    if unposted:
        clauses.append(
            "NOT EXISTS (SELECT 1 FROM social_posts p WHERE p.clip_id = c.id "
            "AND p.status IN ('scheduled', 'queued', 'publishing', 'published'))"
        )
    order = "c.virality_score DESC NULLS LAST, c.created_at DESC" if sort == "score" else "c.created_at DESC"
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                text(
                    f"""
                    SELECT c.id, c.task_id, c.hook_title, c.virality_score, c.duration, c.text, c.clip_order,
                           c.created_at, s.title AS project_title,
                           (SELECT COUNT(*) FROM social_posts p WHERE p.clip_id = c.id
                              AND p.status IN ('scheduled', 'queued', 'publishing', 'published')) AS posts
                    FROM generated_clips c
                    JOIN tasks t ON t.id = c.task_id
                    LEFT JOIN sources s ON s.id = t.source_id
                    WHERE {' AND '.join(clauses)}
                    ORDER BY {order}
                    LIMIT :limit
                    """
                ),
                params,
            )
        ).fetchall()
    items = [
        {
            "project_id": row.task_id,
            "clip_id": row.id,
            "n": row.clip_order,
            "title": row.hook_title,
            "score": row.virality_score,
            "seconds": round(float(row.duration or 0), 1),
            "project": row.project_title,
            "posted_or_scheduled": int(row.posts or 0),
            "says": _excerpt(row.text, 160),
        }
        for row in rows
    ]
    card_items = [
        {
            "project_id": row.task_id,
            "clip_id": row.id,
            "title": row.hook_title,
            "score": row.virality_score,
            "duration": row.duration,
            "project_title": row.project_title,
        }
        for row in rows
    ]
    return ToolOutcome({"clips": items, "shown": len(items)}, card={"type": "clips", "items": card_items} if items else None)


async def get_clip_transcript(ctx: ToolContext, data: dict) -> ToolOutcome:
    project_id = _id(data.get("project_id"), "project_id")
    clip_id = _id(data.get("clip_id"), "clip_id")
    task = await _call(ctx.api.get(f"/tasks/{project_id}"))
    for clip in task.get("clips") or []:
        if clip.get("id") == clip_id:
            return ToolOutcome(
                {
                    "clip_id": clip_id,
                    "title": clip.get("hook_title"),
                    "seconds": clip.get("duration"),
                    "transcript": (clip.get("text") or "")[:8000],
                    "why_chosen": _excerpt(clip.get("reasoning"), 600),
                }
            )
    raise ToolError("That clip isn't in this project.")


# ----------------------------------------------------------------------
# Making clips
# ----------------------------------------------------------------------
async def _create_defaults(user_id: str) -> dict:
    """What the manual create screen would send for this person (their saved choices)."""
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                text(
                    "SELECT default_font_family, default_font_size, default_font_color, default_clip_length, "
                    "create_settings FROM users WHERE id = :id"
                ),
                {"id": user_id},
            )
        ).fetchone()
    if not row:
        return {}
    settings = row.create_settings if isinstance(row.create_settings, dict) else {}
    if isinstance(row.create_settings, str):
        try:
            settings = json.loads(row.create_settings)
        except ValueError:
            settings = {}
    defaults: dict[str, Any] = {
        "clip_length": row.default_clip_length or "auto",
        "font_options": {
            k: v
            for k, v in {
                "font_family": row.default_font_family,
                "font_size": row.default_font_size,
                "font_color": row.default_font_color,
            }.items()
            if v
        },
    }
    mapping = {
        "captionTemplate": "caption_template",
        "addSubtitles": "add_subtitles",
        "outputFormat": "output_format",
        "includeBroll": "include_broll",
        "cutLongPauses": "cut_long_pauses",
        "pauseThresholdMs": "pause_threshold_ms",
        "removeFillerWords": "remove_filler_words",
    }
    for key, field_name in mapping.items():
        if key in settings:
            defaults[field_name] = settings[key]
    if isinstance(settings.get("filteredWords"), str):
        defaults["filtered_words"] = [w.strip().lower() for w in settings["filteredWords"].split(",") if w.strip()]
    return defaults


async def create_project(ctx: ToolContext, data: dict) -> ToolOutcome:
    url = _str(data.get("url"), "url", max_len=2000, required=True)
    if not (url.startswith("http://") or url.startswith("https://") or url.startswith("upload://")):
        raise ToolError("url must be a video link (https://...) or an attached upload (upload://...).")
    workflow = _choice(data.get("workflow"), "workflow", WORKFLOWS, "highlights")
    topic = _str(data.get("topic"), "topic", max_len=120)
    instructions = _str(data.get("instructions"), "instructions", max_len=500)
    part_count = _int(data.get("part_count"), "part_count", low=2, high=10)
    defaults = await _create_defaults(ctx.user_id)
    body: dict[str, Any] = {
        "source": {"url": url},
        "processing_mode": "fast",
        "caption_template": defaults.get("caption_template", "default"),
        "output_format": defaults.get("output_format", "vertical"),
        "add_subtitles": defaults.get("add_subtitles", True),
        "include_broll": defaults.get("include_broll", False),
        "clip_length": defaults.get("clip_length", "auto"),
    }
    for key in ("cut_long_pauses", "pause_threshold_ms", "remove_filler_words", "filtered_words", "font_options"):
        if key in defaults and defaults[key] not in (None, {}):
            body[key] = defaults[key]
    title = _str(data.get("title"), "title", max_len=300)
    if title:
        body["source"]["title"] = title
    if data.get("start_seconds") is not None:
        body["source"]["start_seconds"] = _int(data["start_seconds"], "start_seconds", low=0, high=86400)
    if data.get("end_seconds") is not None:
        body["source"]["end_seconds"] = _int(data["end_seconds"], "end_seconds", low=1, high=86400)
    if data.get("clip_length") is not None:
        body["clip_length"] = _choice(data["clip_length"], "clip_length", CLIP_LENGTHS)
    if data.get("caption_style") is not None:
        body["caption_template"] = _str(data["caption_style"], "caption_style", max_len=50)
    if data.get("output_format") is not None:
        body["output_format"] = _choice(data["output_format"], "output_format", OUTPUT_FORMATS)
    if data.get("captions") is not None:
        body["add_subtitles"] = bool(data["captions"])
    if data.get("broll") is not None:
        body["include_broll"] = bool(data["broll"])
    options: dict[str, Any] = {}
    if workflow != "highlights":
        body["workflow"] = workflow
    if workflow == "topic":
        if not topic:
            raise ToolError("topic is required for the topic workflow.")
        options["topic"] = topic
    elif topic:
        raise ToolError("topic only applies to the topic workflow.")
    if part_count is not None:
        if workflow != "series":
            raise ToolError("part_count only applies to the series workflow.")
        options["part_count"] = part_count
    if options:
        body["workflow_options"] = options
    if instructions:
        body["instructions"] = instructions
    created = await _call(ctx.api.post("/tasks/", body))
    project_id = created.get("task_id")
    project_title = title
    if project_id and not project_title:
        try:
            project_title = (await ctx.api.get(f"/tasks/{project_id}")).get("source_title")
        except AgentAPIError:
            project_title = None
    duplicate = "already" in str(created.get("message", "")).lower()
    return ToolOutcome(
        {
            "project_id": project_id,
            "title": project_title,
            "status": "queued",
            **({"note": "This video was already being clipped; this is that project."} if duplicate else {}),
            "next": "Processing takes a few minutes. Use when_project_finishes to act on the clips once they're ready.",
        },
        card={"type": "project_started", "project": {"id": project_id, "title": project_title, "workflow": workflow}},
    )


async def project_action(ctx: ToolContext, data: dict) -> ToolOutcome:
    project_id = _id(data.get("project_id"), "project_id")
    action = _choice(data.get("action"), "action", ("cancel", "resume"))
    if not action:
        raise ToolError("action is required: cancel or resume.")
    result = await _call(ctx.api.post(f"/tasks/{project_id}/{action}"))
    return ToolOutcome({"project_id": project_id, "done": action, "result": result.get("message") or result.get("status")})


async def delete_project(ctx: ToolContext, data: dict) -> ToolOutcome:
    project_id = _id(data.get("project_id"), "project_id")
    await _call(ctx.api.delete(f"/tasks/{project_id}"))
    return ToolOutcome({"project_id": project_id, "deleted": True})


async def list_caption_styles(ctx: ToolContext, _data: dict) -> ToolOutcome:
    styles = await _call(ctx.api.get("/caption-templates"))
    templates = styles.get("templates") or []
    return ToolOutcome(
        {"styles": [{"id": t.get("id"), "name": t.get("name"), "description": t.get("description")} for t in templates]}
    )


async def when_project_finishes(ctx: ToolContext, data: dict) -> ToolOutcome:
    project_id = _id(data.get("project_id"), "project_id")
    instruction = _str(data.get("then"), "then", max_len=1000, required=True)
    task = await _call(ctx.api.get(f"/tasks/{project_id}"))
    if task.get("status") in {"completed", "error", "cancelled"}:
        raise ToolError(f"That project is already {task['status']}; act on it now instead.")
    job = await store.create_job(
        conversation_id=ctx.conversation_id,
        user_id=ctx.user_id,
        team_id=ctx.team_id,
        kind="followup",
        task_id=project_id,
        clip_id=None,
        payload={"then": instruction, "project_title": task.get("source_title"), "timezone": str(ctx.tz)},
        lifetime=timedelta(hours=12),
    )
    if ctx.on_job_created:
        await ctx.on_job_created(job)
    return ToolOutcome(
        {"followup_id": job["id"], "project_id": project_id, "will_do": instruction},
        card={"type": "followup", "job_id": job["id"], "project_id": project_id,
              "project_title": task.get("source_title"), "instruction": instruction},
    )


# ----------------------------------------------------------------------
# Editing
# ----------------------------------------------------------------------
async def _clip_title(ctx: ToolContext, project_id: str, clip_id: str) -> tuple[Optional[str], dict]:
    task = await _call(ctx.api.get(f"/tasks/{project_id}"))
    for clip in task.get("clips") or []:
        if clip.get("id") == clip_id:
            return clip.get("hook_title"), clip
    raise ToolError("That clip isn't in this project.")


async def edit_clip(ctx: ToolContext, data: dict) -> ToolOutcome:
    project_id = _id(data.get("project_id"), "project_id")
    clip_id = _id(data.get("clip_id"), "clip_id")
    instruction = _str(data.get("instruction"), "instruction", max_len=500, required=True)
    render = data.get("render") is not False
    then = _str(data.get("then"), "then", max_len=1000)
    title, _clip = await _clip_title(ctx, project_id, clip_id)
    job = await store.create_job(
        conversation_id=ctx.conversation_id,
        user_id=ctx.user_id,
        team_id=ctx.team_id,
        kind="clip_edit",
        task_id=project_id,
        clip_id=clip_id,
        payload={"instruction": instruction, "render": render, "then": then, "clip_title": title,
                 "timezone": str(ctx.tz)},
        lifetime=timedelta(hours=2),
    )
    if ctx.on_job_created:
        await ctx.on_job_created(job)
    return ToolOutcome(
        {
            "edit_id": job["id"],
            "status": "started",
            "note": "Runs in the background (about a minute, longer if the studio has to prepare the clip first). "
            "The result is a studio draft plus a rendered download; it does not replace the generated clip that posts use.",
        },
        card={"type": "edit", "job": _job_card(job)},
    )


def _job_card(job: dict) -> dict:
    payload = job.get("payload") or {}
    result = job.get("result") or {}
    return {
        "id": job.get("id"),
        "kind": job.get("kind"),
        "status": job.get("status"),
        "step": job.get("step"),
        "project_id": job.get("task_id"),
        "clip_id": job.get("clip_id"),
        "clip_title": payload.get("clip_title"),
        "instruction": payload.get("instruction") or payload.get("then"),
        "summary": result.get("summary"),
        "export_url": result.get("export_url"),
        "error": job.get("error"),
    }


async def check_background_work(ctx: ToolContext, data: dict) -> ToolOutcome:
    ids = data.get("ids") if isinstance(data.get("ids"), list) else None
    jobs = await store.list_jobs(ctx.conversation_id, [str(i) for i in ids][:20] if ids else None)
    return ToolOutcome(
        {
            "work": [
                {
                    "id": j["id"],
                    "kind": j["kind"],
                    "status": j["status"],
                    "step": j["step"],
                    "project_id": j["task_id"],
                    "clip_id": j["clip_id"],
                    **({"summary": (j.get("result") or {}).get("summary")} if j.get("result") else {}),
                    **({"error": j["error"]} if j.get("error") else {}),
                }
                for j in jobs
            ]
        }
    )


async def trim_clip(ctx: ToolContext, data: dict) -> ToolOutcome:
    project_id = _id(data.get("project_id"), "project_id")
    clip_id = _id(data.get("clip_id"), "clip_id")
    start = float(data.get("cut_from_start") or 0)
    end = float(data.get("cut_from_end") or 0)
    if start < 0 or end < 0 or (start == 0 and end == 0):
        raise ToolError("Give a positive number of seconds to cut from the start and/or the end.")
    title, clip = await _clip_title(ctx, project_id, clip_id)
    if start + end >= float(clip.get("duration") or 0) - 3:
        raise ToolError("That would leave less than 3 seconds of the clip.")
    result = await _call(
        _slow(ctx, "PATCH", f"/tasks/{project_id}/clips/{clip_id}", {"start_offset": start, "end_offset": end})
    )
    clip_after = result.get("clip") or {}
    queued = bool(result.get("job_id") or result.get("status") == "queued")
    return ToolOutcome(
        {
            "clip_id": clip_after.get("id") or clip_id,
            "seconds": clip_after.get("duration"),
            "status": "re-rendering in the background" if queued else "done",
        },
        card={"type": "clip", "project_id": project_id, "clip_id": clip_after.get("id") or clip_id, "title": title,
              "note": "trimmed"},
    )


async def _slow(ctx: ToolContext, method: str, path: str, body: dict) -> Any:
    """A call that re-renders video in the request (trim, restyle)."""
    return await ctx.api.request(method, path, json_body=body, timeout=SLOW_TIMEOUT_SECONDS)


async def restyle_project(ctx: ToolContext, data: dict) -> ToolOutcome:
    project_id = _id(data.get("project_id"), "project_id")
    body: dict[str, Any] = {"apply_to_existing": True}
    style = _str(data.get("caption_style"), "caption_style", max_len=50)
    if style:
        body["caption_template"] = style
    font_color = _str(data.get("font_color"), "font_color", max_len=7)
    if font_color:
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", font_color):
            raise ToolError("font_color must be a hex color like #FFD700.")
        body["font_color"] = font_color
    if data.get("font_size") is not None:
        body["font_size"] = _int(data["font_size"], "font_size", low=12, high=48)
    font_family = _str(data.get("font_family"), "font_family", max_len=100)
    if font_family:
        body["font_family"] = font_family
    if len(body) == 1:
        raise ToolError("Say what to change: caption_style, font_color, font_size or font_family.")
    task = await _call(ctx.api.get(f"/tasks/{project_id}"))
    # Unchanged values are sent as they are, so only what was asked changes.
    body.setdefault("caption_template", task.get("caption_template") or "default")
    body.setdefault("font_family", task.get("font_family"))
    body.setdefault("font_size", task.get("font_size"))
    body.setdefault("font_color", task.get("font_color"))
    result = await _call(_slow(ctx, "POST", f"/tasks/{project_id}/settings", body))
    return ToolOutcome(
        {"project_id": project_id, "applied": {k: v for k, v in body.items() if k != "apply_to_existing"},
         "status": "re-rendering every clip" if result.get("job_id") else "done"},
        card={"type": "project_restyled", "project_id": project_id, "title": task.get("source_title")},
    )


# ----------------------------------------------------------------------
# Publishing & the calendar
# ----------------------------------------------------------------------
async def get_calendar(ctx: ToolContext, data: dict) -> ToolOutcome:
    start_text = _str(data.get("start"), "start", max_len=40)
    days = _int(data.get("days"), "days", low=1, high=62, default=14)
    status = _choice(data.get("status"), "status", ("scheduled", "queued", "publishing", "published", "failed", "cancelled"))
    if start_text:
        start = parse_local(start_text if "T" in start_text else f"{start_text}T00:00", ctx.tz)
    else:
        local_now = datetime.now(timezone.utc).astimezone(ctx.tz)
        start = local_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    end = start + timedelta(days=days)
    response = await _call(
        ctx.api.get("/social/posts", starts_at=start.isoformat(), ends_at=end.isoformat(), status=status, limit=300)
    )
    posts = [p for p in response.get("posts") or [] if status or p.get("status") != "cancelled"]
    return ToolOutcome(
        {
            "from": local_text(start, ctx.tz),
            "to": local_text(end, ctx.tz),
            "posts": [_post_summary(p, ctx.tz) for p in posts],
        },
        card={"type": "posts", "items": [_post_card_item(p) for p in posts], "range": {"start": start.isoformat(), "end": end.isoformat()}},
    )


async def draft_caption(ctx: ToolContext, data: dict) -> ToolOutcome:
    project_id = _id(data.get("project_id"), "project_id")
    clip_id = _id(data.get("clip_id"), "clip_id")
    platform = _choice(data.get("platform"), "platform", PLATFORMS, "tiktok")
    result = await _call(ctx.api.post("/social/captions", {"task_id": project_id, "clip_id": clip_id, "provider": platform}))
    return ToolOutcome({"platform": platform, "caption": result.get("caption")})


def _post_body(ctx: ToolContext, data: dict, project_id: str, clip_id: str, account_id: str) -> dict:
    body: dict[str, Any] = {"task_id": project_id, "clip_id": clip_id, "social_account_id": account_id}
    title = _str(data.get("title"), "title", max_len=255)
    caption = _str(data.get("caption"), "caption", max_len=2200)
    if title:
        body["title"] = title
    if caption is not None:
        body["caption"] = caption
    hashtags = data.get("hashtags")
    if isinstance(hashtags, list):
        body["hashtags"] = [str(h)[:60] for h in hashtags][:30]
    privacy = _choice(data.get("privacy"), "privacy", ("public", "unlisted", "private"))
    if privacy:
        body["privacy_level"] = privacy
    return body


async def schedule_post(ctx: ToolContext, data: dict) -> ToolOutcome:
    project_id = _id(data.get("project_id"), "project_id")
    clip_id = _id(data.get("clip_id"), "clip_id")
    when_text = _str(data.get("when"), "when", max_len=40, required=True)
    try:
        when = parse_local(when_text, ctx.tz)
    except ValueError as exc:
        raise ToolError(str(exc)) from None
    if when <= datetime.now(timezone.utc) + timedelta(minutes=1):
        raise ToolError("That time has passed. Pick a later time, or use publish_now to post right away.")
    account = await _resolve_account(ctx, data.get("account_id"), _choice(data.get("platform"), "platform", PLATFORMS))
    body = _post_body(ctx, data, project_id, clip_id, account["id"])
    body["scheduled_for"] = when.isoformat()
    result = await _call(ctx.api.post("/social/posts", body))
    post = result.get("post") or {}
    return ToolOutcome(
        {"scheduled": _post_summary(post, ctx.tz)},
        card={"type": "post", "post": _post_card_item(post), "action": "scheduled"},
    )


async def publish_now(ctx: ToolContext, data: dict) -> ToolOutcome:
    project_id = _id(data.get("project_id"), "project_id")
    clip_id = _id(data.get("clip_id"), "clip_id")
    account = await _resolve_account(ctx, data.get("account_id"), _choice(data.get("platform"), "platform", PLATFORMS))
    body = _post_body(ctx, data, project_id, clip_id, account["id"])
    result = await _call(ctx.api.post("/social/posts", body))
    post = result.get("post") or {}
    return ToolOutcome(
        {"publishing": _post_summary(post, ctx.tz), "note": "Uploading now; it shows up on the platform within minutes."},
        card={"type": "post", "post": _post_card_item(post), "action": "publishing"},
    )


async def update_post(ctx: ToolContext, data: dict) -> ToolOutcome:
    post_id = _id(data.get("post_id"), "post_id")
    body: dict[str, Any] = {}
    if data.get("when"):
        try:
            body["scheduled_for"] = parse_local(str(data["when"]), ctx.tz).isoformat()
        except ValueError as exc:
            raise ToolError(str(exc)) from None
    title = _str(data.get("title"), "title", max_len=255)
    if title:
        body["title"] = title
    caption = data.get("caption")
    if isinstance(caption, str):
        body["caption"] = caption[:2200]
    if isinstance(data.get("hashtags"), list):
        body["hashtags"] = [str(h)[:60] for h in data["hashtags"]][:30]
    if not body:
        raise ToolError("Say what to change: when, title, caption or hashtags.")
    result = await _call(ctx.api.patch(f"/social/posts/{post_id}", body))
    post = result.get("post") or {}
    return ToolOutcome({"updated": _post_summary(post, ctx.tz)}, card={"type": "post", "post": _post_card_item(post), "action": "updated"})


async def post_action(ctx: ToolContext, data: dict) -> ToolOutcome:
    post_id = _id(data.get("post_id"), "post_id")
    action = _choice(data.get("action"), "action", ("cancel", "retry", "delete"))
    if not action:
        raise ToolError("action is required: cancel, retry or delete.")
    if action == "delete":
        await _call(ctx.api.delete(f"/social/posts/{post_id}"))
        return ToolOutcome({"post_id": post_id, "deleted": True})
    result = await _call(ctx.api.post(f"/social/posts/{post_id}/{action}"))
    post = result.get("post") or {}
    return ToolOutcome(
        {action + ("led" if action == "cancel" else "ed"): _post_summary(post, ctx.tz)},
        card={"type": "post", "post": _post_card_item(post), "action": "cancelled" if action == "cancel" else "retried"},
    )


async def suggest_posting_times(ctx: ToolContext, data: dict) -> ToolOutcome:
    """Open slots that fit the creator's audience and don't crowd the calendar."""
    count = _int(data.get("count"), "count", low=1, high=30, default=5)
    platform = _choice(data.get("platform"), "platform", PLATFORMS)
    per_day = _int(data.get("per_day"), "per_day", low=1, high=4, default=1)
    start_text = _str(data.get("start_date"), "start_date", max_len=10)
    local_now = datetime.now(timezone.utc).astimezone(ctx.tz)
    if start_text:
        try:
            first_day = datetime.fromisoformat(start_text).date()
        except ValueError:
            raise ToolError("start_date must look like 2026-11-24.") from None
    else:
        first_day = local_now.date()
    horizon = max(7, (count // per_day) + 7)
    start = datetime.combine(first_day, datetime.min.time(), tzinfo=ctx.tz).astimezone(timezone.utc)
    end = start + timedelta(days=horizon + 1)
    try:
        upcoming = (await ctx.api.get("/social/posts", starts_at=start.isoformat(), ends_at=end.isoformat(), limit=500)).get("posts") or []
        published = (await ctx.api.get("/social/posts", status="published", limit=300)).get("posts") or []
    except AgentAPIError as exc:
        raise ToolError(_detail_text(exc.detail)) from None
    busy = [
        parse_any(p.get("scheduled_for") or p.get("published_at"))
        for p in upcoming
        if p.get("status") in {"scheduled", "queued", "publishing", "published"}
        and (platform is None or p.get("provider") == platform)
    ]
    busy = [b for b in busy if b]
    # Learn the creator's best hours once enough posts have views.
    views_by_hour: dict[int, list[float]] = defaultdict(list)
    for post in published:
        if platform and post.get("provider") != platform:
            continue
        when = parse_any(post.get("published_at"))
        views = (post.get("metrics") or {}).get("views")
        if when and isinstance(views, (int, float)):
            views_by_hour[when.astimezone(ctx.tz).hour].append(float(views))
    measured = sum(len(v) for v in views_by_hour.values())
    if measured >= 6 and len(views_by_hour) >= 2:
        ranked = sorted(views_by_hour, key=lambda h: -sum(views_by_hour[h]) / len(views_by_hour[h]))
        hours = [f"{h:02d}:00" for h in ranked[:3]]
        basis = f"your best hours from {measured} measured posts"
    else:
        hours = DEFAULT_SLOTS.get(platform, DEFAULT_SLOTS[None])
        basis = "typical engagement peaks (not enough of your own posts measured yet)"
    slots: list[str] = []
    day = first_day
    while len(slots) < count and day <= first_day + timedelta(days=horizon):
        taken_today = sum(1 for b in busy if b.astimezone(ctx.tz).date() == day)
        for hour_text in hours:
            if taken_today >= per_day or len(slots) >= count:
                break
            hour, minute = (int(x) for x in hour_text.split(":"))
            candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=ctx.tz)
            if candidate <= local_now + timedelta(minutes=30):
                continue
            if any(abs((candidate - b).total_seconds()) < 3 * 3600 for b in busy):
                continue
            slots.append(candidate.strftime("%Y-%m-%dT%H:%M"))
            busy.append(candidate)
            taken_today += 1
        day += timedelta(days=1)
    return ToolOutcome(
        {"slots": [{"when": s, "day": datetime.fromisoformat(s).strftime("%a")} for s in slots], "based_on": basis},
        card={"type": "slots", "items": slots, "platform": platform, "basis": basis},
    )


async def get_performance(ctx: ToolContext, _data: dict) -> ToolOutcome:
    summary = await _call(ctx.api.get("/social/performance"))
    try:
        series = await ctx.api.get("/social/performance/timeseries", days=30)
    except AgentAPIError:
        series = {}
    points = series.get("days") or []
    last_30 = sum(float(p.get("views") or 0) for p in points if isinstance(p, dict))
    result = {
        "published_posts": summary.get("total_published"),
        "measured_posts": summary.get("measured_posts"),
        "views_last_30_days": int(last_30) if points else None,
        "by_platform": summary.get("by_provider"),
        "by_hook_type": summary.get("by_hook_type"),
        "by_length": summary.get("by_duration"),
        "top_posts": [
            {k: p.get(k) for k in ("id", "provider", "title", "hook_type", "views", "likes", "external_url")}
            for p in (summary.get("top_posts") or [])
        ],
    }
    return ToolOutcome(result, card={"type": "performance", "summary": result})


# ----------------------------------------------------------------------
# Preferences & memory
# ----------------------------------------------------------------------
async def update_create_defaults(ctx: ToolContext, data: dict) -> ToolOutcome:
    """Change what new projects use by default (the manual create screen reads the same settings)."""
    settings_updates: dict[str, Any] = {}
    if data.get("caption_style") is not None:
        settings_updates["captionTemplate"] = _str(data["caption_style"], "caption_style", max_len=64)
    if data.get("captions") is not None:
        settings_updates["addSubtitles"] = bool(data["captions"])
    if data.get("broll") is not None:
        settings_updates["includeBroll"] = bool(data["broll"])
    if data.get("output_format") is not None:
        settings_updates["outputFormat"] = _choice(data["output_format"], "output_format", OUTPUT_FORMATS)
    clip_length = _choice(data.get("clip_length"), "clip_length", CLIP_LENGTHS)
    if not settings_updates and clip_length is None:
        raise ToolError("Say which default to change.")
    async with AsyncSessionLocal() as db:
        if settings_updates:
            await db.execute(
                text(
                    "UPDATE users SET create_settings = COALESCE(create_settings, '{}'::jsonb) || CAST(:patch AS JSONB) "
                    "WHERE id = :id"
                ),
                {"id": ctx.user_id, "patch": json.dumps(settings_updates)},
            )
        if clip_length is not None:
            await db.execute(
                text("UPDATE users SET default_clip_length = :value WHERE id = :id"),
                {"id": ctx.user_id, "value": clip_length},
            )
        await db.commit()
    changed = {**settings_updates, **({"clipLength": clip_length} if clip_length else {})}
    return ToolOutcome({"saved_defaults": changed}, card={"type": "defaults", "changed": changed})


async def remember(ctx: ToolContext, data: dict) -> ToolOutcome:
    note = _str(data.get("note"), "note", max_len=300, required=True)
    saved = await store.add_memory(ctx.user_id, note)
    if saved is None:
        raise ToolError("Memory is full (40 notes). Ask the person which note to forget first.")
    return ToolOutcome({"remembered": note, "memory_id": saved["id"]}, card={"type": "memory", "note": note})


async def forget(ctx: ToolContext, data: dict) -> ToolOutcome:
    query = (_str(data.get("note_contains"), "note_contains", max_len=200, required=True) or "").lower()
    memories = await store.list_memories(ctx.user_id)
    matches = [m for m in memories if query in m["note"].lower()]
    if not matches:
        raise ToolError("No remembered note contains that text.")
    for memory in matches:
        await store.delete_memory(ctx.user_id, memory["id"])
    return ToolOutcome({"forgot": [m["note"] for m in matches]})


# ----------------------------------------------------------------------
# Interactive UI in the chat
# ----------------------------------------------------------------------
OPTION_STYLES = ("chips", "cards", "clips", "slots")
OPTION_ICONS = (
    "sparkles", "list", "target", "recap", "trailer", "calendar", "send", "scissors", "wand", "captions",
    "chart", "youtube", "tiktok", "instagram", "clock", "check", "x", "upload", "link", "film", "heart",
)


async def show_options(ctx: ToolContext, data: dict) -> ToolOutcome:
    """Buttons, choice cards, a clip picker or time slots the person taps to answer."""
    style = _choice(data.get("style"), "style", OPTION_STYLES, "chips")
    question = _str(data.get("question"), "question", max_len=200)
    multiple = data.get("multiple") is True
    raw = data.get("options")
    if not isinstance(raw, list) or not 1 <= len(raw) <= 12:
        raise ToolError("options must list 1 to 12 choices.")
    options = []
    for index, option in enumerate(raw):
        if not isinstance(option, dict):
            raise ToolError("Each option must be an object.")
        label = _str(option.get("label"), "label", max_len=80, required=True)
        item: dict[str, Any] = {"id": f"o{index}", "label": label}
        reply = _str(option.get("reply"), "reply", max_len=500)
        item["reply"] = reply or label
        description = _str(option.get("description"), "description", max_len=200)
        if description:
            item["description"] = description
        icon = option.get("icon")
        if icon in OPTION_ICONS:
            item["icon"] = icon
        if option.get("recommended") is True:
            item["recommended"] = True
        if style == "clips":
            item["project_id"] = _id(option.get("project_id"), "project_id")
            item["clip_id"] = _id(option.get("clip_id"), "clip_id")
            if option.get("score") is not None:
                item["score"] = _int(option.get("score"), "score", low=0, high=100)
        if style == "slots":
            when = _str(option.get("when"), "when", max_len=40, required=True)
            try:
                item["when"] = parse_local(when, ctx.tz).isoformat()
            except ValueError as exc:
                raise ToolError(str(exc)) from None
        options.append(item)
    card = {"type": "options", "style": style, "question": question, "multiple": multiple, "options": options}
    return ToolOutcome(
        "Shown to the person under your reply. Now finish your turn with your reply text and no more tool "
        "calls: say what you did or found, briefly, without listing the options. Their tap arrives as their next message.",
        card=card,
    )


# ----------------------------------------------------------------------
# Registry
# ----------------------------------------------------------------------
_PROJECT_CLIP = {
    "project_id": {"type": "string", "description": "Project id (from list_projects, find_clips or get_project)."},
    "clip_id": {"type": "string", "description": "Clip id within that project."},
}
_POST_FIELDS = {
    "account_id": {"type": "string", "description": "Connected account id from get_overview. Optional when platform identifies one account."},
    "platform": {"type": "string", "enum": list(PLATFORMS)},
    "title": {"type": "string", "description": "Post title (YouTube uses it; defaults to the clip's hook title)."},
    "caption": {"type": "string", "description": "Caption/description. Write one with draft_caption or yourself; defaults to the clip transcript."},
    "hashtags": {"type": "array", "items": {"type": "string"}, "description": "Without or with #, max 30."},
    "privacy": {"type": "string", "enum": ["public", "unlisted", "private"], "description": "Leave out for the platform default."},
}

TOOLS: list[Tool] = [
    Tool(
        "show_options",
        "Show tappable UI in the chat instead of asking in plain text: quick-reply chips, rich choice cards "
        "(icon, label, one-line description, one marked recommended, like the app's workflow chooser), a clip "
        "picker with real thumbnails (style clips: give project_id and clip_id per option), or time slots "
        "(style slots: give when per option). Each option's `reply` is sent as the person's message when tapped, "
        "written in their voice (e.g. 'Make it a 3-part series'). With multiple=true they pick several and "
        "confirm. Use it whenever there's a real choice to make, to offer next steps after finishing something, "
        "or to confirm a plan. Call it last, then write your reply; the options appear under it.",
        _schema(
            {
                "question": {"type": "string", "description": "Short heading above the options; can be empty for chips."},
                "style": {"type": "string", "enum": list(OPTION_STYLES)},
                "multiple": {"type": "boolean"},
                "options": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "reply": {"type": "string"},
                            "description": {"type": "string"},
                            "icon": {"type": "string", "enum": list(OPTION_ICONS)},
                            "recommended": {"type": "boolean"},
                            "project_id": {"type": "string"},
                            "clip_id": {"type": "string"},
                            "score": {"type": "integer"},
                            "when": {"type": "string", "description": "Local time for style slots."},
                        },
                        "required": ["label"],
                        "additionalProperties": False,
                    },
                },
            },
            ["style", "options"],
        ),
        show_options,
    ),
    Tool(
        "get_overview",
        "The person's plan, minutes left this month, connected social accounts (with account ids), whether "
        "scheduling is allowed, available workflows, and the notes you remembered about them. Call this first "
        "when you need account ids or limits.",
        _schema({}),
        get_overview,
    ),
    Tool(
        "list_projects",
        "Recent projects (one per source video), newest first, with status and clip counts.",
        _schema(
            {
                "status": {"type": "string", "enum": ["queued", "processing", "completed", "error", "cancelled"]},
                "query": {"type": "string", "description": "Words from the video title."},
                "limit": {"type": "integer", "description": "1-50, default 12."},
            }
        ),
        list_projects,
    ),
    Tool(
        "get_project",
        "One project with every clip: clip ids, hook titles, virality scores (0-100), length, what is said, and "
        "where each clip is already posted or scheduled. Also shows progress while it's processing.",
        _schema({"project_id": _PROJECT_CLIP["project_id"]}, ["project_id"]),
        get_project,
    ),
    Tool(
        "find_clips",
        "Search clips across the whole library by words said, hook title or video title; filter by score or to "
        "clips never posted or scheduled. Best for 'my best unposted clips' or 'clips about pricing'.",
        _schema(
            {
                "query": {"type": "string"},
                "min_score": {"type": "integer", "description": "Minimum virality score, 0-100."},
                "unposted_only": {"type": "boolean"},
                "sort": {"type": "string", "enum": ["score", "recent"]},
                "limit": {"type": "integer", "description": "1-40, default 12."},
            }
        ),
        find_clips,
    ),
    Tool(
        "get_clip_transcript",
        "The full transcript of one clip and why it was picked. Use before writing captions or planning edits.",
        _schema(dict(_PROJECT_CLIP), ["project_id", "clip_id"]),
        get_clip_transcript,
    ),
    Tool(
        "create_project",
        "Start clipping a video: a YouTube/Twitch/Kick or direct video link, or an attached upload (upload://...). "
        "Uses the person's saved defaults unless you override them. Charges minutes of source video; processing "
        "takes several minutes. Workflows: highlights (best standalone clips, default), series (Part 1..N of one "
        "story), topic (every strong moment about `topic`), recap (one clip of the main points), trailer (one "
        "teaser that hides the payoff). clip_length: highlights/topic per clip auto 15-60s, short 15-30s, medium "
        "30-60s, long 60-120s; series per part; recap/trailer total length.",
        _schema(
            {
                "url": {"type": "string"},
                "workflow": {"type": "string", "enum": list(WORKFLOWS)},
                "topic": {"type": "string", "description": "Required for workflow=topic."},
                "part_count": {"type": "integer", "description": "Series only, 2-10; leave out for automatic."},
                "instructions": {"type": "string", "description": "Editorial guidance, max 500 chars, e.g. 'skip the sponsor read'."},
                "clip_length": {"type": "string", "enum": list(CLIP_LENGTHS)},
                "caption_style": {"type": "string", "description": "Caption template id from list_caption_styles."},
                "captions": {"type": "boolean", "description": "Burn in captions (default from saved settings)."},
                "broll": {"type": "boolean", "description": "Add stock B-roll."},
                "output_format": {"type": "string", "enum": list(OUTPUT_FORMATS)},
                "title": {"type": "string"},
                "start_seconds": {"type": "integer", "description": "Clip only part of a long video (highlights only)."},
                "end_seconds": {"type": "integer"},
            },
            ["url"],
        ),
        create_project,
    ),
    Tool(
        "when_project_finishes",
        "Queue something for you to do as soon as a processing project finishes, e.g. 'schedule the 3 best clips "
        "on TikTok over the next 3 days at good times'. You'll be woken up with the result in this chat. Use this "
        "instead of waiting.",
        _schema(
            {"project_id": _PROJECT_CLIP["project_id"], "then": {"type": "string", "description": "What to do, in full."}},
            ["project_id", "then"],
        ),
        when_project_finishes,
    ),
    Tool(
        "project_action",
        "Cancel a project that is processing, or resume one that was cancelled or failed.",
        _schema(
            {"project_id": _PROJECT_CLIP["project_id"], "action": {"type": "string", "enum": ["cancel", "resume"]}},
            ["project_id", "action"],
        ),
        project_action,
    ),
    Tool(
        "delete_project",
        "Delete a project and all its clips. Cannot be undone; the person approves it in the chat first.",
        _schema({"project_id": _PROJECT_CLIP["project_id"]}, ["project_id"]),
        delete_project,
        needs_approval=lambda _input: True,
    ),
    Tool(
        "list_caption_styles",
        "Caption styles (templates) you can use in create_project, restyle_project and update_create_defaults.",
        _schema({}),
        list_caption_styles,
    ),
    Tool(
        "edit_clip",
        "Edit a clip in the studio with the AI editor, in the background: tighten pacing, cut words or a section, "
        "remove pauses or filler, change caption look, add B-roll, highlight words, and so on. Write the "
        "instruction like a precise brief to an editor. With render (default true) a finished video is rendered "
        "for download. The studio draft changes; the generated clip that posts use stays as it was. Optional "
        "`then`: what to do once the edit is done.",
        _schema(
            {
                **_PROJECT_CLIP,
                "instruction": {"type": "string", "description": "Max 500 characters."},
                "render": {"type": "boolean"},
                "then": {"type": "string"},
            },
            ["project_id", "clip_id", "instruction"],
        ),
        edit_clip,
    ),
    Tool(
        "check_background_work",
        "Status of edits and follow-ups you started in this chat (all of them, or the given ids).",
        _schema({"ids": {"type": "array", "items": {"type": "string"}}}),
        check_background_work,
    ),
    Tool(
        "trim_clip",
        "Cut seconds off the start and/or end of the generated clip itself (re-renders it; this is what gets "
        "posted). Use for 'start it 2 seconds later' or 'end before the outro'.",
        _schema(
            {
                **_PROJECT_CLIP,
                "cut_from_start": {"type": "number", "description": "Seconds to remove from the start."},
                "cut_from_end": {"type": "number", "description": "Seconds to remove from the end."},
            },
            ["project_id", "clip_id"],
        ),
        trim_clip,
    ),
    Tool(
        "restyle_project",
        "Change the captions of every clip in a project (style, color, size, font) and re-render them. These are "
        "the clips that get posted.",
        _schema(
            {
                "project_id": _PROJECT_CLIP["project_id"],
                "caption_style": {"type": "string"},
                "font_color": {"type": "string", "description": "Hex, e.g. #FFD700."},
                "font_size": {"type": "integer", "description": "12-48."},
                "font_family": {"type": "string"},
            },
            ["project_id"],
        ),
        restyle_project,
    ),
    Tool(
        "get_calendar",
        "Posts on the posting calendar between two dates: scheduled, publishing, published and failed, with post "
        "ids, platform, account, local time and views.",
        _schema(
            {
                "start": {"type": "string", "description": "Local date like 2026-11-24; default today."},
                "days": {"type": "integer", "description": "1-62, default 14."},
                "status": {"type": "string", "enum": ["scheduled", "queued", "publishing", "published", "failed", "cancelled"]},
            }
        ),
        get_calendar,
    ),
    Tool(
        "suggest_posting_times",
        "Open posting slots (local times) that fit the person's audience (their best hours once measured) and keep "
        "posts at least 3 hours apart. Use before scheduling several clips.",
        _schema(
            {
                "count": {"type": "integer"},
                "platform": {"type": "string", "enum": list(PLATFORMS)},
                "per_day": {"type": "integer", "description": "Posts per day, default 1."},
                "start_date": {"type": "string", "description": "First day, like 2026-11-24; default today."},
            }
        ),
        suggest_posting_times,
    ),
    Tool(
        "draft_caption",
        "Write a platform-ready caption with hashtags for a clip.",
        _schema({**_PROJECT_CLIP, "platform": {"type": "string", "enum": list(PLATFORMS)}}, ["project_id", "clip_id"]),
        draft_caption,
    ),
    Tool(
        "schedule_post",
        "Schedule a clip to post on a connected account at a local time (`when` like 2026-11-24T18:00). "
        "Scheduling needs the Creator plan. Can be moved or cancelled until it goes out.",
        _schema({**_PROJECT_CLIP, "when": {"type": "string"}, **_POST_FIELDS}, ["project_id", "clip_id", "when"]),
        schedule_post,
    ),
    Tool(
        "publish_now",
        "Publish a clip to a connected account right away. Public and immediate: the person approves it in the "
        "chat before it runs.",
        _schema({**_PROJECT_CLIP, **_POST_FIELDS}, ["project_id", "clip_id"]),
        publish_now,
        needs_approval=lambda _input: True,
    ),
    Tool(
        "update_post",
        "Move a scheduled post to another local time and/or change its title, caption or hashtags.",
        _schema(
            {
                "post_id": {"type": "string"},
                "when": {"type": "string", "description": "New local time like 2026-11-25T19:30."},
                "title": {"type": "string"},
                "caption": {"type": "string"},
                "hashtags": {"type": "array", "items": {"type": "string"}},
            },
            ["post_id"],
        ),
        update_post,
    ),
    Tool(
        "post_action",
        "Cancel a scheduled post, retry a failed or cancelled one, or delete a post from the calendar.",
        _schema(
            {"post_id": {"type": "string"}, "action": {"type": "string", "enum": ["cancel", "retry", "delete"]}},
            ["post_id", "action"],
        ),
        post_action,
    ),
    Tool(
        "get_performance",
        "How published clips are doing: views by platform, hook type and length, the top posts, and views in the "
        "last 30 days.",
        _schema({}),
        get_performance,
    ),
    Tool(
        "update_create_defaults",
        "Change what new projects use by default (also the manual create screen): caption style, captions on/off, "
        "B-roll, framing, clip length. Only when the person asks to change their defaults.",
        _schema(
            {
                "caption_style": {"type": "string"},
                "captions": {"type": "boolean"},
                "broll": {"type": "boolean"},
                "output_format": {"type": "string", "enum": list(OUTPUT_FORMATS)},
                "clip_length": {"type": "string", "enum": list(CLIP_LENGTHS)},
            }
        ),
        update_create_defaults,
    ),
    Tool(
        "remember",
        "Remember a lasting fact or preference about the person for future chats (brand voice, niche, platforms, "
        "posting rhythm, words to avoid). Only when they ask you to remember something or clearly state a lasting "
        "preference.",
        _schema({"note": {"type": "string", "description": "One short sentence."}}, ["note"]),
        remember,
    ),
    Tool(
        "forget",
        "Forget remembered notes that contain the given text.",
        _schema({"note_contains": {"type": "string"}}, ["note_contains"]),
        forget,
    ),
]

TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}
TOOL_DEFINITIONS = [tool.definition() for tool in TOOLS]


async def run_tool(ctx: ToolContext, name: str, data: Any) -> ToolOutcome:
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        return ToolOutcome(f"Unknown tool {name}.", ok=False)
    if not isinstance(data, dict):
        return ToolOutcome("The tool input must be an object.", ok=False)
    try:
        return await tool.handler(ctx, data)
    except ToolError as exc:
        return ToolOutcome(str(exc), ok=False)
    except AgentAPIError as exc:
        return ToolOutcome(_detail_text(exc.detail) + f" (HTTP {exc.status})", ok=False)
    except Exception:  # noqa: BLE001
        logger.exception("Agent tool %s failed", name)
        return ToolOutcome("That didn't work because of an error on SupoClip's side. Try again or try another way.", ok=False)


def approval_summary(name: str, data: dict) -> dict:
    """What the approval card shows, before anything happens."""
    return {"tool": name, "input": {k: v for k, v in data.items() if k in {
        "project_id", "clip_id", "account_id", "platform", "title", "caption", "hashtags", "privacy"}}}
