"""Validate workflow requests at the API, and gate who may use them.

Everything here runs before a task is created or charged, so a rejected
request never costs minutes.
"""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any, Optional

from sqlalchemy import text

from ..config import get_config
from .contracts import WORKFLOWS, WorkflowOptions, WorkflowSpec
from .durations import (
    MIN_SOURCE_SECONDS,
    SERIES_MAX_PARTS,
    SERIES_MIN_PARTS,
    SERIES_SOURCE_HEADROOM,
    max_series_parts,
    part_budget,
    recap_lengths,
    series_lengths,
    trailer_lengths,
)
from .errors import WorkflowError, WorkflowRequestError

TOPIC_MIN_CHARS, TOPIC_MAX_CHARS = 2, 120
INSTRUCTIONS_MAX_CHARS = 500

# Bidi embeddings, overrides and isolates can make stored text display
# differently from what the model reads, and invisible characters (soft hyphen,
# zero-width space, word joiner, BOM) hide inside words the topic search must
# match. ZWJ in emoji and ZWNJ in Persian and Indic scripts carry meaning and stay.
_INVISIBLE = re.compile("[\u00ad\u180e\u200b\u2060\ufeff\u202a-\u202e\u2066-\u2069]")
# One Han, kana or Hangul character is already a word ("钱", "猫").
_ONE_CHARACTER_WORD = re.compile(
    "[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af\U00020000-\U0003134f]"
)
_LINE_BREAKS = re.compile("\r\n|[\r  \u0085\u000b\u000c]")
_INLINE_SPACE = re.compile(r"[^\S\n]+")
_SPACE_AROUND_NEWLINES = re.compile(r" ?\n ?")
_EXTRA_NEWLINES = re.compile(r"\n{3,}")

_NOT_AVAILABLE_MESSAGE = "This option isn't available on your account yet."


def normalize_text(value: Any, *, keep_newlines: bool) -> str:
    """str only; strip; remove control chars (keep \\n if keep_newlines); collapse runs of
    spaces/tabs to one space; collapse 3+ newlines to 2."""
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFC", value)
    value = _LINE_BREAKS.sub("\n", value)
    value = value.replace("\t", " ")
    if not keep_newlines:
        value = value.replace("\n", " ")
    # Control characters and lone surrogates (valid in JSON, not in UTF-8,
    # so the database and every hash would reject them) are dropped.
    value = "".join(
        ch for ch in value if ch == "\n" or unicodedata.category(ch) not in ("Cc", "Cs")
    )
    value = _INVISIBLE.sub("", value)
    value = _INLINE_SPACE.sub(" ", value)
    if keep_newlines:
        value = _SPACE_AROUND_NEWLINES.sub("\n", value)
        value = _EXTRA_NEWLINES.sub("\n\n", value)
    return value.strip()


def topic_is_searchable(topic: str) -> bool:
    """A normalized topic names something: a letter or digit, and two characters
    unless the one character is a Han, kana or Hangul word."""
    if not any(ch.isalnum() for ch in topic):
        return False
    return len(topic) >= TOPIC_MIN_CHARS or bool(_ONE_CHARACTER_WORD.fullmatch(topic))


def _parse_part_count(value: Any) -> Optional[int]:
    """"auto", null or missing -> None (auto). An int 2-10, also as a numeric string."""
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.casefold() == "auto":
            return None
        if not re.fullmatch(r"\d{1,2}", stripped):
            raise _invalid_part_count()
        value = int(stripped)
    elif isinstance(value, float) and math.isfinite(value) and value.is_integer():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int):
        raise _invalid_part_count()
    if not SERIES_MIN_PARTS <= value <= SERIES_MAX_PARTS:
        raise _invalid_part_count()
    return value


def _invalid_part_count() -> WorkflowRequestError:
    return WorkflowRequestError("INVALID_PART_COUNT", "Choose Auto or 2 to 10 parts.")


def parse_workflow_request(data: dict, clip_length: str) -> WorkflowSpec:
    """Read ``workflow``, ``workflow_options`` and ``instructions`` from a create body.

    Raises WorkflowRequestError (422). Options that do not apply to the chosen
    workflow are dropped, never validated.
    """
    workflow = data.get("workflow")
    if workflow is None:
        workflow = "highlights"
    if not isinstance(workflow, str) or workflow not in WORKFLOWS:
        raise WorkflowRequestError(
            "INVALID_WORKFLOW",
            "Unknown workflow. Use highlights, series, topic, recap or trailer.",
        )

    raw_options = data.get("workflow_options")
    if raw_options is None:
        raw_options = {}
    if not isinstance(raw_options, dict):
        raise WorkflowRequestError(
            "INVALID_WORKFLOW_OPTIONS", "workflow_options must be an object."
        )

    topic = ""
    if workflow == "topic":
        topic = normalize_text(raw_options.get("topic"), keep_newlines=False)
        if not topic_is_searchable(topic):
            raise WorkflowRequestError("TOPIC_REQUIRED", "Enter a topic to look for.")
        if len(topic) > TOPIC_MAX_CHARS:
            raise WorkflowRequestError("TOPIC_TOO_LONG", "Keep the topic under 120 characters.")

    part_count = _parse_part_count(raw_options.get("part_count")) if workflow == "series" else None

    # Top level wins; workflow_options.instructions is for MCP and older clients.
    instructions = normalize_text(data.get("instructions"), keep_newlines=True)
    if not instructions:
        instructions = normalize_text(raw_options.get("instructions"), keep_newlines=True)
    if len(instructions) > INSTRUCTIONS_MAX_CHARS:
        raise WorkflowRequestError(
            "INSTRUCTIONS_TOO_LONG", "Keep instructions under 500 characters."
        )

    return WorkflowSpec(
        workflow=workflow,
        options=WorkflowOptions(topic=topic, part_count=part_count, instructions=instructions),
        clip_length=clip_length,
    )


def _source_minimum_message(workflow: str) -> str:
    return {
        "series": "This video is too short to split into a series. Try Highlights instead.",
        "recap": "This video is already short. Recaps work on videos over 3 minutes.",
        "trailer": "Trailers work on videos over 3 minutes.",
        "topic": "This video is too short to search by topic. Try Highlights instead.",
    }[workflow]


def _below_minimum(spec: WorkflowSpec, source_seconds: float) -> bool:
    return source_seconds < MIN_SOURCE_SECONDS.get(spec.workflow, 0)


def check_source_duration(spec: WorkflowSpec, duration_seconds: Optional[float]) -> None:
    """Reject a source too short for the workflow (422). No-op without a duration."""
    if duration_seconds is None or spec.workflow == "highlights":
        return
    if _below_minimum(spec, duration_seconds):
        raise WorkflowRequestError(
            f"SOURCE_TOO_SHORT_FOR_{spec.workflow.upper()}",
            _source_minimum_message(spec.workflow),
            details={"min_source_seconds": MIN_SOURCE_SECONDS[spec.workflow]},
        )
    requested = spec.options.part_count
    if spec.workflow != "series" or requested is None:
        return
    most = max_series_parts(duration_seconds, spec.clip_length)
    if requested <= most:
        return
    if most < SERIES_MIN_PARTS:
        # Long parts need more than the series minimum: report what two parts
        # of this length actually need, so the hint matches the video.
        two_parts = math.ceil(
            SERIES_MIN_PARTS * part_budget(spec.clip_length).min * SERIES_SOURCE_HEADROOM
        )
        raise WorkflowRequestError(
            "SOURCE_TOO_SHORT_FOR_SERIES",
            _source_minimum_message("series"),
            details={"min_source_seconds": max(MIN_SOURCE_SECONDS["series"], two_parts)},
        )
    raise WorkflowRequestError(
        "TOO_MANY_PARTS",
        f"This video fits at most {most} parts. Choose fewer parts or Auto.",
        details={"max_parts": most},
    )


def workflow_runtime_minimum_error(
    spec: WorkflowSpec, source_seconds: float
) -> Optional[WorkflowError]:
    """The worker's source-length check when the API had no duration.

    Only the per-workflow minimum applies here. An explicit part count that
    no longer fits is reduced by the partitioner (note PART_COUNT_REDUCED)
    instead of failing a task that is already running.
    """
    if spec.workflow == "highlights" or not _below_minimum(spec, source_seconds):
        return None
    return WorkflowError(
        "source_too_short",
        _source_minimum_message(spec.workflow),
        details={"min_source_seconds": MIN_SOURCE_SECONDS[spec.workflow]},
    )


def workflow_not_available() -> WorkflowRequestError:
    return WorkflowRequestError("WORKFLOW_NOT_AVAILABLE", _NOT_AVAILABLE_MESSAGE, status=403)


async def workflows_enabled_for(db, user_id: str) -> bool:
    """Same logic as SocialService.has_access: all -> True, off -> False,
    admins -> users.is_admin or email in WORKFLOWS_ALLOWED_EMAILS."""
    config = get_config()
    access = config.workflows_access
    if access == "all":
        return True
    if access == "off" or db is None or not user_id:
        return False
    row = (
        await db.execute(
            text("SELECT is_admin, email FROM users WHERE id = :user_id"),
            {"user_id": user_id},
        )
    ).fetchone()
    if not row:
        return False
    email = (row.email or "").strip().lower()
    return bool(row.is_admin) or email in config.workflows_allowed_emails


def workflows_info(enabled: bool) -> dict[str, Any]:
    """``GET /tasks/workflows``. ``enabled`` false: highlights without instructions only."""

    def entry(workflow: str, **extra: Any) -> dict[str, Any]:
        return {
            "id": workflow,
            "available": enabled or workflow == "highlights",
            "min_source_seconds": MIN_SOURCE_SECONDS[workflow],
            **extra,
        }

    return {
        "enabled": enabled,
        "workflows": [
            entry("highlights"),
            entry(
                "series",
                part_count={"min": SERIES_MIN_PARTS, "max": SERIES_MAX_PARTS},
                lengths=series_lengths(),
            ),
            entry("topic"),
            entry("recap", lengths=recap_lengths()),
            entry("trailer", lengths=trailer_lengths()),
        ],
        "topic_max_chars": TOPIC_MAX_CHARS,
        "instructions_max_chars": INSTRUCTIONS_MAX_CHARS,
    }
