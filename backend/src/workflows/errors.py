"""Workflow failures: rejected at the API, or raised in the worker."""

from __future__ import annotations

from typing import Any, Optional


class WorkflowRequestError(ValueError):
    """Rejected at the API before anything is charged.

    ``status`` is 422, or 403 for WORKFLOW_NOT_AVAILABLE. ``details`` carries
    extra fields for the client (for example ``max_parts``) and is merged into
    the response body next to ``code`` and ``message``.
    """

    def __init__(
        self,
        code: str,
        message: str,
        status: int = 422,
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = dict(details or {})

    def to_detail(self) -> dict[str, Any]:
        """The HTTPException ``detail`` body."""
        return {**self.details, "code": self.code, "message": self.message}

    def __reduce__(self):
        return (type(self), (self.code, self.message, self.status, self.details))


class WorkflowError(ValueError):
    """Raised in the worker for a user-facing failure.

    ``message`` is English, shown as the task's progress message. ``code`` is
    stored as the task's ``error_code`` and in ``workflow_plan.error``, so the
    UI can localize it and offer a next step from ``details`` (for example
    suggested topics).
    """

    CODES = frozenset(
        {
            "source_too_short",
            "no_story",
            "topic_not_found",
            "recap_unsupported",
            "trailer_unbuildable",
            "analysis_incomplete",
            "plan_failed",
            "render_incomplete",
            "transcript_untimed",
        }
    )

    def __init__(self, code: str, message: str, details: Optional[dict[str, Any]] = None):
        if code not in self.CODES:
            raise ValueError(f"Unknown workflow error code: {code}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details) if details else None

    def __str__(self) -> str:
        return self.message

    def __reduce__(self):
        # ARQ pickles a failed job's exception into its result; the default
        # reduction would rebuild it from the message alone and fail.
        return (type(self), (self.code, self.message, self.details))
