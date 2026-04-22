"""Tests for ``src/observability.py``."""

import json
import logging

import pytest

from src.observability import (
    JsonLogFormatter,
    TraceIdFilter,
    clear_trace_id,
    generate_trace_id,
    get_trace_id,
    set_trace_id,
)


class TestTraceIdContext:
    def test_default_is_dash(self):
        clear_trace_id()
        assert get_trace_id() == "-"

    def test_set_and_get(self):
        set_trace_id("abc123")
        assert get_trace_id() == "abc123"
        clear_trace_id()

    def test_clear_resets(self):
        set_trace_id("abc")
        clear_trace_id()
        assert get_trace_id() == "-"


class TestGenerateTraceId:
    def test_unique(self):
        ids = {generate_trace_id() for _ in range(50)}
        assert len(ids) == 50

    def test_hex_format(self):
        value = generate_trace_id()
        assert len(value) == 32
        assert all(c in "0123456789abcdef" for c in value)


class TestTraceIdFilter:
    def test_attaches_trace_id(self):
        set_trace_id("xyz")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hi",
            args=(),
            exc_info=None,
        )
        assert TraceIdFilter().filter(record) is True
        assert record.trace_id == "xyz"
        clear_trace_id()


class TestJsonLogFormatter:
    def _make_record(self, level=logging.INFO, exc_info=None):
        return logging.LogRecord(
            name="app",
            level=level,
            pathname=__file__,
            lineno=1,
            msg="hello %s",
            args=("world",),
            exc_info=exc_info,
        )

    def test_produces_valid_json(self):
        record = self._make_record()
        record.trace_id = "traceabc"
        output = JsonLogFormatter().format(record)
        payload = json.loads(output)
        assert payload["level"] == "INFO"
        assert payload["logger"] == "app"
        assert payload["message"] == "hello world"
        assert payload["trace_id"] == "traceabc"
        assert "timestamp" in payload

    def test_missing_trace_id_defaults_to_dash(self):
        record = self._make_record()
        output = JsonLogFormatter().format(record)
        payload = json.loads(output)
        assert payload["trace_id"] == "-"

    def test_includes_exception(self):
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            record = self._make_record(level=logging.ERROR, exc_info=sys.exc_info())
        output = JsonLogFormatter().format(record)
        payload = json.loads(output)
        assert "exception" in payload
        assert "ValueError" in payload["exception"]
