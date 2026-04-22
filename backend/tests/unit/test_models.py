"""Tests for SQLAlchemy models helpers in ``src/models.py``."""

import uuid

from src.models import Source, generate_uuid_string


class TestGenerateUuidString:
    def test_returns_string(self):
        value = generate_uuid_string()
        assert isinstance(value, str)

    def test_is_valid_uuid(self):
        value = generate_uuid_string()
        # Will raise if not a valid UUID
        parsed = uuid.UUID(value)
        assert str(parsed) == value

    def test_unique_per_call(self):
        ids = {generate_uuid_string() for _ in range(50)}
        assert len(ids) == 50


class TestSourceDecideSourceType:
    def test_youtube_url(self):
        # Only checks for the substring "youtube" — youtu.be is not matched.
        source = Source.__new__(Source)
        assert source.decide_source_type("https://www.youtube.com/watch?v=abc") == "youtube"
        assert source.decide_source_type("youtube.com/shorts/xyz") == "youtube"

    def test_non_youtube_url(self):
        source = Source.__new__(Source)
        assert source.decide_source_type("https://example.com/video.mp4") == "video_url"
        # youtu.be short links are classified as video_url — this is the
        # actual (simple substring) behavior, captured here so a future
        # rename cannot change it silently.
        assert source.decide_source_type("https://youtu.be/abc") == "video_url"

    def test_empty_url_is_video(self):
        source = Source.__new__(Source)
        assert source.decide_source_type("") == "video_url"
