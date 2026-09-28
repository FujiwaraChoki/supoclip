import asyncio
import traceback
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UnexpectedModelBehavior

from src import ai


@pytest.fixture
def retry_sleep(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(ai.asyncio, "sleep", sleep)
    monkeypatch.setattr(ai.random, "uniform", lambda *_: 0)
    return sleep


async def test_analysis_recovers_from_gemini_overload_without_changing_prompt(
    monkeypatch, retry_sleep
):
    transcript = "[00:00 - 00:30] A useful standalone explanation of a common mistake."
    analysis = ai.TranscriptAnalysis(
        most_relevant_segments=[ai.TranscriptSegment(
            start_time="00:00",
            end_time="00:30",
            text="A useful standalone explanation of a common mistake.",
        )],
        summary="A common mistake.",
        key_topics=["mistakes"],
    )
    agent = SimpleNamespace(run=AsyncMock(side_effect=[
        ModelHTTPError(503, "gemini-test", {"message": "High demand"}),
        SimpleNamespace(output=analysis),
    ]))
    monkeypatch.setattr(ai, "get_transcript_agent", lambda: agent)

    result = await ai.get_most_relevant_parts_by_transcript(transcript)

    assert len(result.most_relevant_segments) == 1
    assert agent.run.await_count == 2
    assert agent.run.await_args_list[0] == agent.run.await_args_list[1]
    retry_sleep.assert_awaited_once_with(5)


async def test_exhausted_overload_is_bounded_and_has_safe_error(monkeypatch, retry_sleep, caplog):
    error = ModelHTTPError(503, "gemini-test", {"message": "sensitive transcript"})
    agent = SimpleNamespace(run=AsyncMock(side_effect=error))
    monkeypatch.setattr(ai, "get_transcript_agent", lambda: agent)

    with pytest.raises(RuntimeError, match="temporarily unavailable") as caught:
        await ai.get_most_relevant_parts_by_transcript("transcript")

    assert agent.run.await_count == 3
    assert [call.args[0] for call in retry_sleep.await_args_list] == [5, 10]
    assert "sensitive transcript" not in str(caught.value)
    assert "sensitive transcript" not in caplog.text
    assert "sensitive transcript" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504, 529])
async def test_transient_http_statuses_recover(status, retry_sleep):
    agent = SimpleNamespace(run=AsyncMock(side_effect=[
        ModelHTTPError(status, "configured-model"), "result",
    ]))
    assert await ai._run_transcript_analysis(agent, "prompt") == "result"
    assert agent.run.await_count == 2


@pytest.mark.parametrize("error", [
    ModelHTTPError(400, "configured-model"),
    ModelHTTPError(401, "configured-model"),
    ModelHTTPError(403, "configured-model"),
    UnexpectedModelBehavior("Invalid output"),
    ValueError("Invalid configuration"),
    httpx.UnsupportedProtocol("Invalid endpoint scheme"),
    httpx.LocalProtocolError("Invalid request"),
])
async def test_permanent_or_output_failures_are_not_retried(error, retry_sleep):
    agent = SimpleNamespace(run=AsyncMock(side_effect=error))
    with pytest.raises(type(error)) as caught:
        await ai._run_transcript_analysis(agent, "prompt")
    assert caught.value is error
    agent.run.assert_awaited_once_with("prompt")
    retry_sleep.assert_not_awaited()


async def test_wrapped_transport_failure_recovers(retry_sleep):
    wrapped = ModelAPIError("configured-model", "Connection failed")
    wrapped.__cause__ = httpx.ReadTimeout("Read timed out")
    agent = SimpleNamespace(run=AsyncMock(side_effect=[wrapped, "result"]))
    assert await ai._run_transcript_analysis(agent, "prompt") == "result"
    assert agent.run.await_count == 2


async def test_cancellation_during_request_passes_through(retry_sleep):
    agent = SimpleNamespace(run=AsyncMock(side_effect=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        await ai._run_transcript_analysis(agent, "prompt")
    assert agent.run.await_count == 1
    retry_sleep.assert_not_awaited()


async def test_cancellation_during_backoff_passes_through(retry_sleep):
    agent = SimpleNamespace(run=AsyncMock(side_effect=ModelHTTPError(503, "model")))
    retry_sleep.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await ai._run_transcript_analysis(agent, "prompt")
    assert agent.run.await_count == 1


async def test_entire_run_has_deadline(monkeypatch):
    monkeypatch.setattr(ai, "TRANSCRIPT_ANALYSIS_TIMEOUT_SECONDS", 0.01)
    cancelled = asyncio.Event()

    async def never_finishes(_prompt):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    agent = SimpleNamespace(run=AsyncMock(side_effect=never_finishes))
    with pytest.raises(RuntimeError, match="took too long"):
        await ai._run_transcript_analysis(agent, "prompt")
    assert cancelled.is_set()
    assert agent.run.await_count == 1
