"""The fallback engine: which model answers when the first one is busy."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from pydantic_ai.exceptions import ModelHTTPError

from src.llm_fallback import (
    ErrorKind,
    ModelUnavailableError,
    classify_error,
    key_fingerprint,
    model_provider,
    run_with_fallback,
)


def http_error(status_code: int) -> ModelHTTPError:
    return ModelHTTPError(
        status_code=status_code,
        model_name="gemini-2.5-flash",
        body={"error": {"message": "boom"}},
    )


class FakeAgent:
    """Stands in for a pydantic_ai agent."""

    def __init__(self, model: str, key: str | None):
        self.model = model
        self.key = key


def build_calls() -> list[tuple[str, str | None]]:
    return []


def test_the_provider_of_a_model_name_is_read_from_its_prefix():
    assert model_provider("google-gla:gemini-2.5-flash") == "google-gla"
    assert model_provider("openrouter:nvidia/nemotron") == "openrouter"
    assert model_provider("gemini-2.5-flash") == ""


def test_a_key_is_marked_without_revealing_it():
    fingerprint = key_fingerprint("super-secret-key-1234")
    assert fingerprint is not None
    assert "super" not in fingerprint
    assert "1234" in fingerprint
    assert key_fingerprint(None) is None


@pytest.mark.parametrize(
    "error, expected",
    [
        (http_error(429), ErrorKind.QUOTA),
        (http_error(401), ErrorKind.AUTH),
        (http_error(403), ErrorKind.AUTH),
        (http_error(503), ErrorKind.TRANSIENT),
        (http_error(529), ErrorKind.TRANSIENT),
        (http_error(404), ErrorKind.PERMANENT),
        (http_error(400), ErrorKind.PERMANENT),
        (httpx.ConnectTimeout("late"), ErrorKind.TRANSIENT),
        (httpx.ReadError("gone"), ErrorKind.TRANSIENT),
        (asyncio.TimeoutError(), ErrorKind.TRANSIENT),
    ],
)
def test_a_failure_is_told_apart_from_another(error, expected):
    assert classify_error(error) is expected


async def test_a_busy_model_makes_the_next_one_answer():
    calls: list[tuple[str, str | None]] = []

    def build(model: str, key: str | None) -> FakeAgent:
        calls.append((model, key))
        return FakeAgent(model, key)

    async def run(agent: FakeAgent):
        if agent.model == "google-gla:gemini-3-flash-preview":
            raise http_error(503)
        return "answer from " + agent.model

    result = await run_with_fallback(
        build,
        run,
        chain=["google-gla:gemini-3-flash-preview", "google-gla:gemini-2.5-flash"],
        keys_for=lambda model: ["key-1"],
    )

    assert result.value == "answer from google-gla:gemini-2.5-flash"
    assert result.model == "google-gla:gemini-2.5-flash"
    assert [call[0] for call in calls] == [
        "google-gla:gemini-3-flash-preview",
        "google-gla:gemini-2.5-flash",
    ]
    assert len(result.attempts) == 2
    assert result.attempts[0].ok is False
    assert result.attempts[0].kind is ErrorKind.TRANSIENT
    assert result.attempts[1].ok is True


async def test_a_refused_key_moves_to_the_next_key_of_the_same_model():
    calls: list[tuple[str, str | None]] = []

    def build(model: str, key: str | None) -> FakeAgent:
        calls.append((model, key))
        return FakeAgent(model, key)

    async def run(agent: FakeAgent):
        if agent.key == "bad-key":
            raise http_error(401)
        return "answer"

    result = await run_with_fallback(
        build,
        run,
        chain=["google-gla:gemini-2.5-flash"],
        keys_for=lambda model: ["bad-key", "good-key"],
    )

    assert result.value == "answer"
    assert calls == [
        ("google-gla:gemini-2.5-flash", "bad-key"),
        ("google-gla:gemini-2.5-flash", "good-key"),
    ]
    assert result.attempts[0].kind is ErrorKind.AUTH


async def test_a_refused_key_does_not_stop_the_next_model():
    """When every key of a model is refused, the next model of the chain answers."""

    async def run(agent: FakeAgent):
        if agent.model.endswith("gemini-3-flash-preview"):
            raise http_error(403)
        return "answer"

    result = await run_with_fallback(
        lambda model, key: FakeAgent(model, key),
        run,
        chain=["google-gla:gemini-3-flash-preview", "google-gla:gemini-2.5-flash"],
        keys_for=lambda model: ["bad-key"] if model.endswith("gemini-3-flash-preview") else ["good-key"],
    )

    assert result.value == "answer"
    assert result.model == "google-gla:gemini-2.5-flash"
    assert [attempt.key_fingerprint for attempt in result.attempts] == [
        key_fingerprint("bad-key"),
        key_fingerprint("good-key"),
    ]


async def test_a_model_that_does_not_exist_moves_on_to_the_next_model():
    async def run(agent: FakeAgent):
        if agent.model.endswith("2.0-flash"):
            raise http_error(404)
        return "answer"

    result = await run_with_fallback(
        lambda model, key: FakeAgent(model, key),
        run,
        chain=["google-gla:gemini-2.0-flash", "google-gla:gemini-2.5-flash"],
        keys_for=lambda model: [],
    )

    assert result.model == "google-gla:gemini-2.5-flash"
    assert result.attempts[0].kind is ErrorKind.PERMANENT


async def test_a_model_that_needs_too_long_is_treated_as_busy():
    async def run(agent: FakeAgent):
        await asyncio.sleep(5)
        return "never"

    with pytest.raises(TimeoutError) as error:
        await run_with_fallback(
            lambda model, key: FakeAgent(model, key),
            run,
            chain=["google-gla:gemini-3-flash-preview", "google-gla:gemini-2.5-flash"],
            keys_for=lambda model: [],
            attempt_timeout=0.05,
            total_timeout=0.2,
        )

    attempts = error.value.attempts
    assert len(attempts) == 2
    assert all(attempt.kind is ErrorKind.TRANSIENT for attempt in attempts)


async def test_an_exhausted_chain_reports_the_last_failure():
    async def run(agent: FakeAgent):
        raise http_error(503)

    with pytest.raises(ModelHTTPError) as error:
        await run_with_fallback(
            lambda model, key: FakeAgent(model, key),
            run,
            chain=["google-gla:gemini-3-flash-preview", "google-gla:gemini-2.5-flash"],
            keys_for=lambda model: ["key-1"],
        )

    attempts = error.value.attempts
    assert len(attempts) == 2
    assert all(attempt.ok is False for attempt in attempts)


async def test_an_unexpected_error_is_not_hidden_behind_the_chain():
    async def run(agent: FakeAgent):
        raise TypeError("a bug in the caller")

    with pytest.raises(TypeError):
        await run_with_fallback(
            lambda model, key: FakeAgent(model, key),
            run,
            chain=["google-gla:gemini-3-flash-preview", "google-gla:gemini-2.5-flash"],
            keys_for=lambda model: [],
        )
