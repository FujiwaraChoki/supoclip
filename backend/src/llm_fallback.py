"""Try one model after another when a provider is busy.

A busy model is not a broken deployment: the next model of the chain often
answers right away, on the same key. The rules live here so that every LLM
call of the project uses the same ones.

The engine never hides the real error: an attempt that fails is written to the
log with the model, the key, the duration and the kind of the failure, and the
error of the last attempt is what the caller sees.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Sequence

import httpx
from pydantic_ai import Agent
from pydantic_ai.exceptions import (
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
    UserError,
)

logger = logging.getLogger(__name__)

AUTH_STATUS_CODES = frozenset({401, 403})
QUOTA_STATUS_CODES = frozenset({429})
TRANSIENT_STATUS_CODES = frozenset({408, 500, 502, 503, 504, 529})
MODEL_MISSING_STATUS_CODES = frozenset({404})


class ErrorKind(str, Enum):
    """What the engine does with a failure."""

    TRANSIENT = "transient"       # the model is busy: try the next model
    QUOTA = "quota"               # the key ran out of its quota: try the next key
    AUTH = "auth"                 # the key is refused: try the next key
    PERMANENT = "permanent"       # the request itself is wrong: try the next model
    FATAL = "fatal"               # nothing else will help: stop right away


class TransientModelError(RuntimeError):
    """
    A failure that another model of the chain may still answer.

    The project retries a busy model a few times. When those retries are used
    up the failure keeps this marker so the chain moves on to the next model
    instead of hiding the problem.
    """


class ModelUnavailableError(Exception):
    """Every model of the chain refused to answer."""

    def __init__(
        self,
        message: str,
        code: str = "model_unavailable",
        attempts: tuple["Attempt", ...] = (),
    ):
        super().__init__(message)
        self.code = code
        self.attempts = attempts


@dataclass(frozen=True)
class Attempt:
    """One try of one model with one key."""

    model: str
    provider: str
    key_fingerprint: str | None
    seconds: float
    ok: bool
    kind: ErrorKind | None = None
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "provider": self.provider,
            "key_fingerprint": self.key_fingerprint,
            "seconds": round(self.seconds, 3),
            "ok": self.ok,
            "kind": self.kind.value if self.kind else None,
            "error": self.error,
        }


@dataclass(frozen=True)
class FallbackResult:
    """The answer and the way it was reached."""

    value: Any
    model: str
    attempts: tuple[Attempt, ...]


def model_provider(model_name: str) -> str:
    """The provider of a model name such as "google-gla:gemini-2.5-flash"."""
    name = (model_name or "").strip()
    if ":" not in name:
        return ""
    return name.split(":", 1)[0].strip().lower()


def key_fingerprint(key: str | None) -> str | None:
    """A short, non reversible mark of a key: the last four characters and a hash."""
    if not key:
        return None
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:6]
    return f"…{key[-4:]}·{digest}"


def classify_error(error: BaseException) -> ErrorKind:
    """What to do with this failure."""
    if isinstance(error, TransientModelError):
        # The retries of one model are used up; the next model may answer.
        return ErrorKind.TRANSIENT
    if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
        return ErrorKind.TRANSIENT
    if isinstance(error, (httpx.TimeoutException, httpx.NetworkError)):
        return ErrorKind.TRANSIENT
    if isinstance(error, UserError):
        # A key that is missing or refused is not a broken model.
        return ErrorKind.AUTH
    if isinstance(error, ModelHTTPError):
        status = error.status_code
        if status in AUTH_STATUS_CODES:
            return ErrorKind.AUTH
        if status in QUOTA_STATUS_CODES:
            return ErrorKind.QUOTA
        if status in TRANSIENT_STATUS_CODES:
            return ErrorKind.TRANSIENT
        if status in MODEL_MISSING_STATUS_CODES:
            return ErrorKind.PERMANENT
        return ErrorKind.PERMANENT
    if isinstance(error, (UsageLimitExceeded, UnexpectedModelBehavior)):
        return ErrorKind.PERMANENT
    # An error nobody expected is reported instead of being retried away.
    return ErrorKind.FATAL


def _describe(error: BaseException) -> str:
    text = str(error).strip().replace("\n", " ")
    return text[:300] if text else type(error).__name__


def split_budget(total_timeout: float, models: int) -> float:
    """
    The time every model of the chain may use.

    The first model must not be able to spend the whole budget: a busy model
    has to leave room for the next one.
    """
    if models <= 1:
        return max(1.0, total_timeout)
    return max(30.0, total_timeout / models)


async def run_with_fallback(
    build_agent: Callable[[str, str | None], Agent],
    run: Callable[[Agent], Awaitable[Any]],
    *,
    chain: Sequence[str],
    keys_for: Callable[[str], list[str]],
    attempt_timeout: float = 60.0,
    total_timeout: float = 180.0,
) -> FallbackResult:
    """
    Run one request over the chain of models until one of them answers.

    ``build_agent`` builds the agent of a model with a key, ``run`` performs the
    request with that agent. A key that is refused or out of quota moves on to
    the next key of the same provider; a busy model moves on to the next model
    of the chain.
    """
    started = time.monotonic()
    deadline = started + total_timeout
    attempts: list[Attempt] = []
    last_error: BaseException | None = None
    last_kind: ErrorKind | None = None

    for model in chain:
        provider = model_provider(model)
        keys = [key for key in keys_for(model) if key] or [None]
        for key in keys:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            # The attempt never runs past the budget of the whole chain.
            timeout = max(0.01, min(attempt_timeout, remaining))
            attempt_started = time.monotonic()
            try:
                agent = build_agent(model, key)
            except Exception as exc:  # the model name itself is wrong
                kind = classify_error(exc)
                attempts.append(
                    Attempt(
                        model=model,
                        provider=provider,
                        key_fingerprint=key_fingerprint(key),
                        seconds=time.monotonic() - attempt_started,
                        ok=False,
                        kind=kind,
                        error=_describe(exc),
                    )
                )
                logger.warning(
                    "Cannot build the model %s: %s", model, _describe(exc)
                )
                last_error, last_kind = exc, kind
                if kind is ErrorKind.FATAL:
                    raise
                break  # the next model may be a valid one

            try:
                value = await asyncio.wait_for(run(agent), timeout=timeout)
            except asyncio.TimeoutError as exc:
                attempts.append(
                    Attempt(
                        model=model,
                        provider=provider,
                        key_fingerprint=key_fingerprint(key),
                        seconds=time.monotonic() - attempt_started,
                        ok=False,
                        kind=ErrorKind.TRANSIENT,
                        error=f"no answer in {timeout:.1f}s",
                    )
                )
                logger.warning(
                    "The model %s did not answer in %.1fs, trying the next one",
                    model,
                    timeout,
                )
                last_error, last_kind = exc, ErrorKind.TRANSIENT
                break  # the next model, the key is fine
            except Exception as exc:
                kind = classify_error(exc)
                attempts.append(
                    Attempt(
                        model=model,
                        provider=provider,
                        key_fingerprint=key_fingerprint(key),
                        seconds=time.monotonic() - attempt_started,
                        ok=False,
                        kind=kind,
                        error=_describe(exc),
                    )
                )
                last_error, last_kind = exc, kind
                logger.warning(
                    "The model %s failed (%s): %s",
                    model,
                    kind.value,
                    _describe(exc),
                )
                if kind is ErrorKind.FATAL:
                    raise
                if kind in (ErrorKind.AUTH, ErrorKind.QUOTA):
                    continue  # the next key of the same provider
                break  # the next model of the chain
            else:
                attempts.append(
                    Attempt(
                        model=model,
                        provider=provider,
                        key_fingerprint=key_fingerprint(key),
                        seconds=time.monotonic() - attempt_started,
                        ok=True,
                    )
                )
                logger.info(
                    "The model %s answered in %.1fs",
                    model,
                    time.monotonic() - attempt_started,
                )
                return FallbackResult(
                    value=value, model=model, attempts=tuple(attempts)
                )

    message = (
        f"No model of the chain answered: {last_error}"
        if last_error
        else "No model of the chain answered"
    )
    if last_error is not None:
        # The engine never hides the real error: the failure of the last
        # attempt is what the caller sees. The attempts travel with it, so a
        # caller can still tell a busy model from a broken one.
        try:
            last_error.attempts = tuple(attempts)  # type: ignore[attr-defined]
        except Exception:  # an exception that does not take attributes
            pass
        raise last_error
    raise ModelUnavailableError(
        message,
        code="model_unavailable",
        attempts=tuple(attempts),
    )
