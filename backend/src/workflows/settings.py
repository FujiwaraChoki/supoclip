"""Advanced workflow settings: one declarative registry.

Every setting a creator can change under "Advanced" lives here: its type,
default, bounds and the workflows it applies to. ``GET /tasks/workflows``
publishes this as a schema so clients render the menu without knowing the list.

A task stores only what differs from the defaults, under
``workflow_options.advanced``. So a request with nothing changed, its stored
options and its plan fingerprint are exactly what they were before this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Union

from .errors import WorkflowRequestError

SettingValue = Union[bool, int, str]
PLANNED_WORKFLOWS = ("series", "topic", "recap", "trailer")


@dataclass(frozen=True)
class Setting:
    key: str
    kind: str  # bool | int | enum
    default: Optional[SettingValue]  # None: automatic (ints only)
    choices: tuple[str, ...] = ()
    minimum: Optional[int] = None
    maximum: Optional[int] = None

    def schema(self) -> dict[str, Any]:
        out: dict[str, Any] = {"key": self.key, "type": self.kind, "default": self.default}
        if self.choices:
            out["choices"] = list(self.choices)
        if self.kind == "int":
            out["min"], out["max"] = self.minimum, self.maximum
        return out

    def valid(self, value: Any) -> bool:
        if self.kind == "bool":
            return isinstance(value, bool)
        if self.kind == "enum":
            return isinstance(value, str) and value in self.choices
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and int(self.minimum or 0) <= value <= int(self.maximum or 0)
        )


_HOOK_TITLE = Setting("hook_title", "bool", True)
# auto: each workflow's own junctions (recap 0.12 s fade, trailer hard cuts).
_JUNCTIONS = Setting("junction_style", "enum", "auto", choices=("auto", "soft", "hard"))
_SHARED = (_HOOK_TITLE, _JUNCTIONS)

SETTINGS: dict[str, tuple[Setting, ...]] = {
    "highlights": (Setting("max_clips", "int", None, minimum=1, maximum=10),),
    "series": (
        Setting("cold_open", "bool", False),
        Setting("part_labels", "bool", True),
        Setting("endings", "enum", "cliffhanger", choices=("cliffhanger", "natural")),
        Setting("coverage", "enum", "best_chapters", choices=("best_chapters", "start_to_finish")),
        *_SHARED,
    ),
    "topic": (
        Setting("max_clips", "int", None, minimum=1, maximum=15),
        Setting("match", "enum", "broad", choices=("broad", "strict")),
        *_SHARED,
    ),
    "recap": (
        Setting("on_screen_points", "bool", True),
        Setting("target_seconds", "int", None, minimum=30, maximum=150),
        Setting("max_points", "int", None, minimum=2, maximum=12),
        Setting("coverage", "enum", "whole_video", choices=("whole_video", "best_moments")),
        *_SHARED,
    ),
    "trailer": (
        # Six beats of at most 10 s: a longer trailer could never fill its window.
        Setting("target_seconds", "int", None, minimum=20, maximum=60),
        Setting("spoilers", "enum", "strict", choices=("strict", "relaxed")),
        Setting("ending", "enum", "auto", choices=("auto", "question")),
        *_SHARED,
    ),
}

_BY_KEY: dict[str, dict[str, Setting]] = {
    workflow: {setting.key: setting for setting in settings} for workflow, settings in SETTINGS.items()
}
_ALL_KEYS = {key for settings in _BY_KEY.values() for key in settings}


def settings_schema(workflow: str) -> list[dict[str, Any]]:
    return [setting.schema() for setting in SETTINGS.get(workflow, ())]


def default_of(workflow: str, key: str) -> Optional[SettingValue]:
    return _BY_KEY[workflow][key].default


def _invalid(key: str) -> WorkflowRequestError:
    return WorkflowRequestError(
        "INVALID_WORKFLOW_SETTING", f"That value isn't supported for {key}.", details={"setting": key}
    )


def parse_settings(workflow: str, raw: Any) -> dict[str, SettingValue]:
    """Strict read at the API (422 before any charge).

    A key no workflow has is rejected. A key another workflow has is dropped,
    like every other option that doesn't apply to the chosen workflow. Null
    means the default. Defaults are not stored.
    """
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise WorkflowRequestError("INVALID_WORKFLOW_OPTIONS", "workflow_options.advanced must be an object.")
    out: dict[str, SettingValue] = {}
    known = _BY_KEY.get(workflow, {})
    for key, value in raw.items():
        if key not in _ALL_KEYS:
            raise _invalid(str(key))
        setting = known.get(key)
        if setting is None or value is None:
            continue
        if isinstance(value, float) and setting.kind == "int" and value.is_integer():
            value = int(value)
        if not setting.valid(value):
            raise _invalid(key)
        if value != setting.default:
            out[key] = value
    return dict(sorted(out.items()))


def read_settings(workflow: Optional[str], raw: Any) -> dict[str, SettingValue]:
    """Lenient read in the worker: anything unknown, invalid or default is dropped. Never raises."""
    if not isinstance(raw, Mapping):
        return {}
    known = _BY_KEY.get(workflow or "", {})
    if workflow is None:
        # Before the workflow is known: keep what some workflow could use.
        known = {setting.key: setting for settings in SETTINGS.values() for setting in settings}
        return dict(sorted((key, value) for key, value in raw.items() if key in known and value is not None))
    return dict(
        sorted(
            (key, value)
            for key, value in raw.items()
            if key in known and known[key].valid(value) and value != known[key].default
        )
    )
