"""What every planner shares: its inputs, the plan language and segment building."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from ..media.timeline import seconds_to_mmss
from .contracts import WORKFLOW_PLAN_VERSION, PlanContext
from .language import LANGUAGE_NAMES, language_name, normalize_language_code
from .llm import PlanStats
from .outline import OutlineResult
from .units import UnitIndex

Range = tuple[float, float]


def plan_language(index: UnitIndex, transcript_data: Optional[dict], outline_language: str = "") -> Optional[str]:
    """The language titles are written in and part labels use.

    The transcription's own language code wins (it is measured, not guessed);
    then the outline's answer; then what the script alone tells.
    """
    measured = normalize_language_code((transcript_data or {}).get("language_code"))
    if measured:
        return measured
    stated = normalize_language_code(outline_language)
    if stated in LANGUAGE_NAMES:
        return stated
    return index.language


@dataclass
class PlanInput:
    """One planner's inputs: the task context, the sentence units and the outline."""

    ctx: PlanContext
    index: UnitIndex
    outline: OutlineResult
    language: Optional[str]
    stats: PlanStats
    notes: list[dict[str, Any]] = field(default_factory=list)

    @property
    def language_name(self) -> str:
        return language_name(self.language)

    @property
    def cleanup(self) -> dict:
        return self.ctx.cleanup_settings or {}

    @property
    def instructions(self) -> str:
        return self.ctx.spec.options.instructions

    def note(self, code: str, params: Optional[dict[str, Any]] = None) -> None:
        if not any(existing.get("code") == code for existing in self.notes):
            self.notes.append({"code": code, "params": dict(params or {})})


def base_plan(plan_input: PlanInput, *, title: str) -> dict[str, Any]:
    """The fields every ``workflow_plan`` carries (§2.4); planners add theirs."""
    outline = plan_input.outline.outline
    return {
        "version": WORKFLOW_PLAN_VERSION,
        "workflow": plan_input.ctx.spec.workflow,
        "language": plan_input.language,
        "coarse": plan_input.index.coarse,
        "mode": plan_input.outline.mode,
        "title": title,
        "summary": outline.one_line,
        "key_topics": list(outline.key_topics),
        "notes": plan_input.notes,
    }


def rounded_ranges(ranges: Sequence[Range]) -> list[list[float]]:
    return [[round(float(start), 3), round(float(end), 3)] for start, end in ranges]


def planned_segment(
    *,
    ranges: Sequence[Range],
    junction_fades: Sequence[float],
    max_seconds: float,
    required: bool,
    text: str,
    hook_title: Optional[str],
    reasoning: str,
    workflow_role: str,
    meta: dict[str, Any],
    part_number: Optional[int] = None,
    relevance_score: float = 1.0,
    scores: Optional[dict[str, int]] = None,
    hook_type: Optional[str] = None,
) -> dict[str, Any]:
    """A locked PlannedSegment (§2.3): the renderer plays exactly these ranges."""
    source_ranges = rounded_ranges(ranges)
    fades = [round(float(fade), 3) for fade in junction_fades]
    if len(fades) != max(0, len(source_ranges) - 1):
        raise ValueError("junction_fades must have one entry per junction")
    scores = scores or {}
    first = source_ranges[0]
    return {
        "start_time": seconds_to_mmss(first[0]),
        "end_time": seconds_to_mmss(first[1]),
        "source_ranges": source_ranges,
        "planned": True,
        "junction_fades": fades,
        "max_seconds": round(float(max_seconds), 1),
        "required": required,
        "text": text,
        "hook_title": hook_title,
        "reasoning": reasoning,
        "relevance_score": round(float(relevance_score), 3),
        "virality_score": int(scores.get("total", 0)),
        "hook_score": int(scores.get("hook", 0)),
        "engagement_score": int(scores.get("engagement", 0)),
        "value_score": int(scores.get("value", 0)),
        "shareability_score": int(scores.get("shareability", 0)),
        "hook_type": hook_type,
        "workflow_role": workflow_role,
        "part_number": part_number,
        "workflow_meta": {
            "planned": True,
            "ranges": [list(item) for item in source_ranges],
            "junction_fades": list(fades),
            "max_seconds": round(float(max_seconds), 1),
            **meta,
        },
    }
