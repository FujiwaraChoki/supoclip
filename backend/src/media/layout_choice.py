"""User-chosen vertical layout, read by the renderer instead of auto-detection.

The choice lives in ``tasks.source_metadata["layout"]``. ``process_task`` binds it
to a ContextVar for the duration of the run (``asyncio.to_thread`` copies the
context), so the planner does not need a new argument through every layer.
``auto`` is the default and leaves the existing behaviour byte-identical.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Optional

VALID_LAYOUTS = {
    "auto", "reaction_top", "reaction_bottom", "reaction_dynamic", "stack", "speaker",
}

_forced_layout: ContextVar[str] = ContextVar("forced_layout", default="auto")


def normalize_layout(value: Any) -> str:
    return value if isinstance(value, str) and value in VALID_LAYOUTS else "auto"


def current_layout() -> str:
    return _forced_layout.get()


def bind_layout(value: Optional[Any]) -> str:
    """Set the layout for the current task context; always overwrites, so a
    job never inherits the previous job's choice."""
    chosen = normalize_layout(value)
    _forced_layout.set(chosen)
    return chosen


@contextmanager
def forced_layout(value: Optional[Any]) -> Iterator[None]:
    token = _forced_layout.set(normalize_layout(value))
    try:
        yield
    finally:
        _forced_layout.reset(token)
