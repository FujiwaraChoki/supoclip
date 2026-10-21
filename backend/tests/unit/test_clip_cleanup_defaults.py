from src.clip_cleanup import (
    DEFAULT_FILTERED_WORDS,
    default_filtered_words,
    DEFAULT_PAUSE_THRESHOLD_MS,
    new_task_cleanup_settings,
    normalize_clip_cleanup_settings,
    updated_cleanup_settings,
)


def test_new_task_defaults_cleanup_on_when_fields_are_omitted():
    settings = new_task_cleanup_settings({"source": {"url": "https://example.com"}})

    assert settings == {
        "cut_long_pauses": True,
        "pause_threshold_ms": DEFAULT_PAUSE_THRESHOLD_MS,
        "remove_filler_words": True,
        "filtered_words": [],
    }
    assert DEFAULT_PAUSE_THRESHOLD_MS == 1200


def test_new_task_explicit_false_turns_cleanup_off():
    settings = new_task_cleanup_settings(
        {"cut_long_pauses": False, "remove_filler_words": False}
    )

    assert settings["cut_long_pauses"] is False
    assert settings["remove_filler_words"] is False


def test_stored_settings_without_values_stay_off():
    # Existing tasks (resume, regenerate, expired metadata) never pick up the
    # new-task defaults.
    settings = normalize_clip_cleanup_settings()

    assert settings["cut_long_pauses"] is False
    assert settings["remove_filler_words"] is False


def test_task_update_keeps_stored_cleanup_for_omitted_fields():
    stored = {
        "cut_long_pauses": True,
        "pause_threshold_ms": 800,
        "remove_filler_words": True,
        "filtered_words": ["basically"],
    }

    assert updated_cleanup_settings({"font_size": 30}, stored) == stored
    assert updated_cleanup_settings({"cut_long_pauses": False}, stored)[
        "cut_long_pauses"
    ] is False
    assert updated_cleanup_settings({}, None)["cut_long_pauses"] is False


def test_default_fillers_are_pure_disfluencies():
    for phrase in ("you know", "i mean", "sort of", "kind of", "like"):
        assert phrase not in DEFAULT_FILTERED_WORDS
    assert {"um", "uh"} <= set(DEFAULT_FILTERED_WORDS)


def test_default_fillers_depend_on_transcript_language():
    assert "um" in default_filtered_words("en_us")
    assert "um" in default_filtered_words("en")
    for language in ("de", "fr", "es", None, ""):
        words = default_filtered_words(language)
        assert "um" not in words
        assert "er" not in words
        assert "uh" in words
    assert "ähm" in default_filtered_words("de")
    assert "euh" in default_filtered_words("fr")
