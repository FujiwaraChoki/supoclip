"""run_in_thread keeps expected 4xx client errors out of ERROR logs (and so out of error tracking)."""

import logging

import pytest
from fastapi import HTTPException

from src.editor_document import NeedsPlan
from src.utils.async_helpers import run_in_thread

LOGGER = "src.utils.async_helpers"


def _raise(exc: BaseException):
    raise exc


def _errors(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOGGER and r.levelno >= logging.ERROR]


async def test_needs_plan_is_not_logged_as_error(caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    with pytest.raises(NeedsPlan):
        await run_in_thread(_raise, NeedsPlan("Plan edits for this clip first"))
    assert _errors(caplog) == []
    assert any(r.levelno == logging.INFO and r.exc_info is None for r in caplog.records if r.name == LOGGER)


async def test_http_409_is_not_logged_as_error(caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    with pytest.raises(HTTPException):
        await run_in_thread(_raise, HTTPException(409, {"code": "NEEDS_PLAN"}))
    assert _errors(caplog) == []


async def test_http_5xx_is_still_an_error(caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    with pytest.raises(HTTPException):
        await run_in_thread(_raise, HTTPException(503, "unavailable"))
    assert len(_errors(caplog)) == 1


async def test_unexpected_exception_is_still_an_error(caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    with pytest.raises(RuntimeError):
        await run_in_thread(_raise, RuntimeError("boom"))
    assert len(_errors(caplog)) == 1


async def test_plain_value_error_is_still_an_error(caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    with pytest.raises(ValueError):
        await run_in_thread(_raise, ValueError("bad"))
    assert len(_errors(caplog)) == 1
