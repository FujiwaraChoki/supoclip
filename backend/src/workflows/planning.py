"""What every planner shares: its inputs, the plan language and segment building."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from ..media.timeline import seconds_to_mmss
from .contracts import WORKFLOW_PLAN_VERSION, PlanContext, WorkflowSpec
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


# Settings the prompts must know about, said the way a creator would.
_SETTING_WISHES = (
    ("series", "endings", "natural", "End each part where a thought is complete. Don't chase cliffhangers or question endings."),
    ("trailer", "ending", "question", "The trailer must end on a question."),
    ("trailer", "spoilers", "relaxed", "You may use lines that give the ending away when they make the strongest trailer."),
)


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
        """The creator's words, then what their advanced settings ask of the editor."""
        parts = [self.ctx.spec.options.instructions]
        spec = self.ctx.spec
        for workflow, key, value, wish in _SETTING_WISHES:
            if spec.workflow == workflow and spec.setting(key) == value:
                parts.append(wish)
        return "\n".join(part for part in parts if part)

    def setting(self, key: str) -> Any:
        return self.ctx.spec.setting(key)

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


# What ``junction_style`` means when it isn't auto.
SOFT_JUNCTION_SECONDS = 0.12


def apply_output_settings(spec: WorkflowSpec, segments: list[dict[str, Any]]) -> None:
    """The settings every planned workflow shares, applied to its finished segments.

    A hidden title stays the clip's name in the app; only the burn-in goes.
    A cold open's junction stays a hard cut whatever the style: it is a jump in time.
    """
    if not spec.options.advanced:
        return
    style = spec.setting("junction_style")
    for segment in segments:
        meta = segment["workflow_meta"]
        if not spec.setting("hook_title"):
            meta["burn_hook_title"] = False
        if style == "auto":
            continue
        fade = SOFT_JUNCTION_SECONDS if style == "soft" else 0.0
        fades = [fade] * len(segment["junction_fades"])
        if meta.get("cold_open"):
            fades[int(meta["cold_open"]["range_count"]) - 1] = 0.0
        segment["junction_fades"] = fades
        meta["junction_fades"] = list(fades)


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
