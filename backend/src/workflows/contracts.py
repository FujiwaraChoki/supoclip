"""Shared workflow contracts. Every workflow package codes against these.

Change them only together with the spec's contract section (docs/workflows.md).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, Optional, Protocol

from ..clip_duration import normalize_clip_length
from .durations import SERIES_MAX_PARTS, SERIES_MIN_PARTS

WORKFLOWS: tuple[str, ...] = ("highlights", "series", "topic", "recap", "trailer")
Workflow = Literal["highlights", "series", "topic", "recap", "trailer"]
WORKFLOW_PLAN_VERSION = "wf-v1"  # bump to invalidate cached plans (not transcripts)
OUTLINE_VERSION = "outline-v1"  # bump to invalidate cached outlines


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _part_count(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if SERIES_MIN_PARTS <= value <= SERIES_MAX_PARTS else None


@dataclass(frozen=True)
class WorkflowOptions:
    topic: str = ""  # topic workflow only; already normalized
    part_count: Optional[int] = None  # series only; None = auto
    instructions: str = ""  # any workflow; already normalized

    def to_json(self) -> dict[str, Any]:
        """Canonical, sorted, no empty values. Stored in tasks.workflow_options."""
        out: dict[str, Any] = {}
        if self.instructions:
            out["instructions"] = self.instructions
        if self.part_count is not None:
            out["part_count"] = self.part_count
        if self.topic:
            out["topic"] = self.topic
        return out

    @classmethod
    def from_json(cls, raw: Any) -> "WorkflowOptions":
        """Lenient: unknown keys ignored, bad types dropped. Never raises."""
        if isinstance(raw, (str, bytes)):
            try:
                raw = json.loads(raw)
            except (TypeError, ValueError):
                return cls()
        if not isinstance(raw, dict):
            return cls()
        topic = raw.get("topic")
        instructions = raw.get("instructions")
        return cls(
            topic=topic.strip() if isinstance(topic, str) else "",
            part_count=_part_count(raw.get("part_count")),
            instructions=instructions.strip() if isinstance(instructions, str) else "",
        )

    def for_workflow(self, workflow: str) -> "WorkflowOptions":
        """Drop the options that do not apply to ``workflow``."""
        return WorkflowOptions(
            topic=self.topic if workflow == "topic" else "",
            part_count=self.part_count if workflow == "series" else None,
            instructions=self.instructions,
        )


@dataclass(frozen=True)
class WorkflowSpec:
    workflow: str  # one of WORKFLOWS
    options: WorkflowOptions
    clip_length: str  # auto|short|medium|long

    @property
    def legacy(self) -> bool:
        """Highlights without instructions: the original pipeline, byte for byte."""
        return self.workflow == "highlights" and not self.options.instructions

    @property
    def uses_performance_context(self) -> bool:
        return self.workflow in ("highlights", "topic")

    @property
    def topic_key(self) -> str:
        """Casefolded topic with collapsed whitespace."""
        return " ".join(self.options.topic.casefold().split())

    def fingerprint(self) -> str:
        """sha256 of canonical JSON: workflow, WORKFLOW_PLAN_VERSION, clip_length,
        topic_key, part_count, sha256(instructions)[:16]."""
        instructions = self.options.instructions
        return _sha256(
            _canonical_json(
                {
                    "workflow": self.workflow,
                    "version": WORKFLOW_PLAN_VERSION,
                    "clip_length": self.clip_length,
                    "topic": self.topic_key,
                    "part_count": self.options.part_count,
                    "instructions": _sha256(instructions)[:16] if instructions else "",
                }
            )
        )


def highlights_spec(clip_length: str = "auto") -> WorkflowSpec:
    return WorkflowSpec("highlights", WorkflowOptions(), normalize_clip_length(clip_length))


def workflow_spec_from_task(task_row: Any, clip_length: str) -> WorkflowSpec:
    """Lenient read for the worker. Missing/unknown workflow (including a MagicMock
    row in tests or a pre-migration row) -> highlights with empty options."""
    clip_length = normalize_clip_length(clip_length)
    if not isinstance(task_row, dict):
        return highlights_spec(clip_length)
    workflow = task_row.get("workflow")
    if not isinstance(workflow, str) or workflow not in WORKFLOWS:
        return highlights_spec(clip_length)
    options = WorkflowOptions.from_json(task_row.get("workflow_options"))
    return WorkflowSpec(workflow, options.for_workflow(workflow), clip_length)


ProgressFn = Callable[[int, str], Awaitable[None]]  # (percent, message); raises on cancel


class PlanCache(Protocol):
    async def load(self, key: str) -> Optional[str]: ...

    async def store(self, key: str, payload: str) -> None: ...


@dataclass
class PlanContext:
    spec: WorkflowSpec
    task_id: Optional[str]
    transcript_text: str  # the existing analysis transcript ("[MM:SS - MM:SS] Speaker: ...")
    transcript_data: Optional[dict]  # word sidecar (ms), from load_cached_transcript_data
    source_seconds: float  # ffprobe duration of the analysed file
    cleanup_settings: dict  # normalize_clip_cleanup_settings(...) output
    performance_context: Optional[str]
    progress: ProgressFn
    plan_cache: PlanCache
    outline_cache_key: str


@dataclass
class WorkflowResult:
    segments: list[dict]  # PlannedSegment dicts, in render order
    plan: dict  # -> tasks.workflow_plan
    summary: Optional[str]
    key_topics: list[str]
    units_fingerprint: str

    def to_cache_json(self) -> str:
        return json.dumps(
            {
                "version": WORKFLOW_PLAN_VERSION,
                "workflow": self.plan["workflow"],
                "units_fingerprint": self.units_fingerprint,
                "plan": self.plan,
                "segments": self.segments,
                "summary": self.summary,
                "key_topics": self.key_topics,
            }
        )

    @classmethod
    def from_cache_json(
        cls, raw: Optional[str], workflow: str, units_fingerprint: str
    ) -> Optional["WorkflowResult"]:
        """None unless version, workflow and units_fingerprint all match."""
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        if (
            payload.get("version") != WORKFLOW_PLAN_VERSION
            or payload.get("workflow") != workflow
            or payload.get("units_fingerprint") != units_fingerprint
        ):
            return None
        plan = payload.get("plan")
        segments = payload.get("segments")
        summary = payload.get("summary")
        key_topics = payload.get("key_topics")
        if (
            not isinstance(plan, dict)
            or plan.get("workflow") != workflow
            or not isinstance(segments, list)
            or not segments
            or not all(isinstance(segment, dict) for segment in segments)
            or not (summary is None or isinstance(summary, str))
            or not isinstance(key_topics, list)
            or not all(isinstance(topic, str) for topic in key_topics)
        ):
            return None
        return cls(
            segments=segments,
            plan=plan,
            summary=summary,
            key_topics=key_topics,
            units_fingerprint=units_fingerprint,
        )
