"""Tests for ``src/usage_context.py``."""

from src.usage_context import (
    UsageEvent,
    UsageRecorder,
    current_usage,
    record_event,
)


class TestUsageEvent:
    def test_defaults(self):
        event = UsageEvent(service="llm")
        assert event.service == "llm"
        assert event.model is None
        assert event.input_tokens == 0
        assert event.output_tokens == 0
        assert event.audio_seconds == 0.0
        assert event.cost_usd == 0.0
        assert event.metadata == {}

    def test_metadata_is_per_instance(self):
        a = UsageEvent(service="llm")
        b = UsageEvent(service="llm")
        a.metadata["task"] = "1"
        assert "task" not in b.metadata


class TestUsageRecorder:
    def test_starts_empty(self):
        assert UsageRecorder().events == []

    def test_records_appends(self):
        recorder = UsageRecorder()
        recorder.record(UsageEvent(service="llm"))
        recorder.record(UsageEvent(service="assemblyai"))
        assert len(recorder.events) == 2
        assert recorder.events[0].service == "llm"


class TestRecordEvent:
    def test_noop_when_no_recorder(self):
        # Default context has no recorder — must not raise.
        current_usage.set(None)
        record_event(UsageEvent(service="llm"))  # should silently drop

    def test_records_to_current(self):
        recorder = UsageRecorder()
        token = current_usage.set(recorder)
        try:
            record_event(UsageEvent(service="llm", cost_usd=0.05))
            assert len(recorder.events) == 1
            assert recorder.events[0].cost_usd == 0.05
        finally:
            current_usage.reset(token)
