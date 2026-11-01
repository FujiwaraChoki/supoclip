import pytest

from src.config import Config, set_config_override
from src.media.source_limits import (
    exceeds_output_limits,
    fit_within_1080p,
    output_long_edge,
    output_short_edge,
)
from src.services.task_service import TaskService
from src.youtube_proxy import capped_height


@pytest.fixture
def cap():
    def apply(height):
        config = Config()
        config.max_output_height = height
        set_config_override(config)
        return config

    yield apply
    set_config_override(None)


@pytest.mark.parametrize(
    "value,expected",
    [(None, 1080), ("1080", 1080), ("2160", 2160), ("1440", 1080), ("garbage", 1080)],
)
def test_max_output_height_parsing(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("MAX_OUTPUT_HEIGHT", raising=False)
    else:
        monkeypatch.setenv("MAX_OUTPUT_HEIGHT", value)
    monkeypatch.delenv("YOUTUBE_MAX_HEIGHT", raising=False)
    config = Config()
    assert config.max_output_height == expected
    assert config.youtube_max_height == expected


def test_edge_caps(cap):
    cap(1080)
    assert (output_long_edge(), output_short_edge()) == (1920, 1080)
    cap(2160)
    assert (output_long_edge(), output_short_edge()) == (3840, 2160)


def test_fit_within_cap_for_uhd_source(cap):
    cap(1080)
    assert fit_within_1080p(3840, 2160) == (1920, 1080)
    assert exceeds_output_limits(3840, 2160, 30)
    cap(2160)
    assert fit_within_1080p(3840, 2160) == (3840, 2160)
    assert not exceeds_output_limits(3840, 2160, 30)
    assert fit_within_1080p(7680, 4320) == (3840, 2160)








def test_cache_key_gets_4k_suffix_only_when_opted_in(cap):
    cap(1080)
    base = TaskService._build_cache_key("u", "youtube", "fast")
    assert TaskService._build_cache_key("u", "youtube", "fast", "720") != base
    cap(2160)
    assert TaskService._build_cache_key("u", "youtube", "fast") != base
    assert TaskService._build_cache_key("u", "youtube", "fast") == TaskService._build_cache_key(
        "u", "youtube", "fast"
    )


def test_youtube_height_clamp(cap):
    cap(1080)
    assert capped_height(2160) == 1080
    cap(2160)
    assert capped_height(2160) == 2160
    assert capped_height(4320) == 2160
    assert capped_height(720) == 720
