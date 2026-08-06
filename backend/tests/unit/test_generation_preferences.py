from src.generation_preferences import (
    generation_preferences_prompt,
    normalize_generation_preferences,
)


def test_generation_preferences_are_bounded_and_deduplicated():
    preferences = normalize_generation_preferences(
        {
            "prompt": "  Find pricing lessons  ",
            "keywords": "pricing, Churn, pricing",
            "clip_count": 99,
            "clip_min_seconds": 5,
            "clip_max_seconds": 120,
            "timeframe_start": "01:30",
            "timeframe_end": "05:00",
            "analysis_mode": "multimodal",
        }
    )

    assert preferences == {
        "prompt": "Find pricing lessons",
        "keywords": ["pricing", "Churn"],
        "clip_count": 10,
        "clip_min_seconds": 15,
        "clip_max_seconds": 60,
        "timeframe_start_seconds": 90,
        "timeframe_end_seconds": 300,
        "analysis_mode": "multimodal",
    }


def test_invalid_timeframe_end_is_removed():
    preferences = normalize_generation_preferences(
        {"timeframe_start": "02:00", "timeframe_end": "02:10"}
    )

    assert preferences["timeframe_start_seconds"] == 120
    assert preferences["timeframe_end_seconds"] is None


def test_generation_preferences_prompt_contains_user_controls():
    prompt = generation_preferences_prompt(
        {
            "prompt": "Find counterintuitive advice",
            "keywords": ["retention"],
            "clip_count": 3,
            "clip_min_seconds": 20,
            "clip_max_seconds": 40,
            "timeframe_start": "01:00",
            "timeframe_end": "10:00",
        }
    )

    assert "Return up to 3 strong clips" in prompt
    assert "between 20 and 40 seconds" in prompt
    assert "Find counterintuitive advice" in prompt
    assert "retention" in prompt
    assert "before source second 60" in prompt
    assert "after source second 600" in prompt
