from src.content_policy import (
    DEFAULT_WORD_LISTS,
    build_word_boundary_pattern,
    rewrite_flagged,
    scan_text,
)


def test_word_boundary_pattern_avoids_scunthorpe_problem():
    pattern = build_word_boundary_pattern(["cunt"])
    assert pattern.search("Scunthorpe") is None
    assert pattern.search("what a cunt") is not None


def test_word_boundary_pattern_is_case_insensitive():
    pattern = build_word_boundary_pattern(["cocaine"])
    assert pattern.search("He does COCAINE sometimes") is not None


def test_scan_text_off_sensitivity_returns_nothing():
    flags = scan_text(
        "he was doing cocaine",
        DEFAULT_WORD_LISTS,
        "off",
        {"drugs": True},
    )
    assert flags == []


def test_scan_text_low_sensitivity_catches_severe_only():
    flags = scan_text(
        "he was stoned and did cocaine",
        DEFAULT_WORD_LISTS,
        "low",
        {"drugs": True},
    )
    words = {f["word"].lower() for f in flags}
    assert "cocaine" in words
    assert "stoned" not in words  # borderline tier, excluded below HIGH


def test_scan_text_high_sensitivity_catches_borderline_too():
    flags = scan_text(
        "he was stoned and did cocaine",
        DEFAULT_WORD_LISTS,
        "high",
        {"drugs": True},
    )
    words = {f["word"].lower() for f in flags}
    assert "cocaine" in words
    assert "stoned" in words


def test_scan_text_respects_disabled_categories():
    flags = scan_text(
        "he was stabbing people while doing cocaine",
        DEFAULT_WORD_LISTS,
        "high",
        {"drugs": True, "violence": False},
    )
    categories = {f["category"] for f in flags}
    assert "drugs" in categories
    assert "violence" not in categories


def test_scan_text_default_categories_enabled_when_key_missing():
    # violence/profanity default off, sex/drugs default on, per spec —
    # scan_text should fall back to DEFAULT_CATEGORIES_ENABLED for any
    # category not explicitly present in the passed-in dict.
    flags = scan_text("he was stabbing people", DEFAULT_WORD_LISTS, "low", {})
    assert flags == []  # violence defaults off


def test_rewrite_flagged_preserves_first_letter():
    text = "he was doing cocaine yesterday"
    flags = scan_text(text, DEFAULT_WORD_LISTS, "low", {"drugs": True})
    rewritten = rewrite_flagged(text, flags)
    assert "c*****e" in rewritten
    assert "cocaine" not in rewritten


def test_rewrite_flagged_handles_multiple_spans_without_offset_drift():
    text = "cocaine and heroin were both there"
    flags = scan_text(text, DEFAULT_WORD_LISTS, "low", {"drugs": True})
    rewritten = rewrite_flagged(text, flags)
    assert "cocaine" not in rewritten
    assert "heroin" not in rewritten
    assert rewritten.startswith("c")
    assert " and " in rewritten


def test_rewrite_flagged_no_flags_returns_original():
    text = "this is a totally clean sentence"
    assert rewrite_flagged(text, []) == text
