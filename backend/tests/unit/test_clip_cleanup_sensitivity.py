from src.clip_cleanup import (
    normalize_clip_cleanup_settings,
    normalize_sensitivity,
    sensitivity_to_pause_threshold_ms,
    DEFAULT_PAUSE_THRESHOLD_MS,
)


def test_sensitivity_zero_disables_cleanup_entirely():
    settings = normalize_clip_cleanup_settings(sensitivity=0)
    assert settings["cut_long_pauses"] is False
    assert settings["remove_filler_words"] is False
    assert settings["sensitivity"] == 0


def test_sensitivity_enables_both_toggles_and_scales_threshold():
    low = normalize_clip_cleanup_settings(sensitivity=1)
    high = normalize_clip_cleanup_settings(sensitivity=100)

    assert low["cut_long_pauses"] is True
    assert low["remove_filler_words"] is True
    assert high["cut_long_pauses"] is True
    assert high["remove_filler_words"] is True

    # Higher sensitivity -> shorter pause threshold (cuts more eagerly).
    assert high["pause_threshold_ms"] < low["pause_threshold_ms"]


def test_sensitivity_high_adds_aggressive_filler_words():
    low = normalize_clip_cleanup_settings(sensitivity=10)
    high = normalize_clip_cleanup_settings(sensitivity=90)

    assert "basically" not in low["filtered_words"]
    assert "basically" in high["filtered_words"]


def test_sensitivity_none_falls_back_to_legacy_booleans():
    """Backward compatibility: omitting sensitivity keeps the old explicit
    cut_long_pauses/pause_threshold_ms/remove_filler_words behavior."""
    settings = normalize_clip_cleanup_settings(
        cut_long_pauses=True,
        pause_threshold_ms=700,
        remove_filler_words=False,
        filtered_words=["custom phrase"],
    )
    assert settings["cut_long_pauses"] is True
    assert settings["pause_threshold_ms"] == 700
    assert settings["remove_filler_words"] is False
    assert settings["filtered_words"] == ["custom phrase"]
    # A display-only sensitivity is still derived, but doesn't change behavior.
    assert settings["sensitivity"] > 0


def test_normalize_sensitivity_clamps_and_rejects_invalid():
    assert normalize_sensitivity(None) is None
    assert normalize_sensitivity("nonsense") is None
    assert normalize_sensitivity(-5) == 0
    assert normalize_sensitivity(150) == 100
    assert normalize_sensitivity(42) == 42


def test_sensitivity_to_pause_threshold_ms_monotonic_decreasing():
    thresholds = [sensitivity_to_pause_threshold_ms(s) for s in (1, 25, 50, 75, 100)]
    assert thresholds == sorted(thresholds, reverse=True)
    assert thresholds[0] <= DEFAULT_PAUSE_THRESHOLD_MS + 600
