from src.services.clip_service import split_half_breakdown


def test_none_for_non_dict():
    assert split_half_breakdown(None) is None
    assert split_half_breakdown("x") is None


def test_text_keys_nulled_scores_kept_input_untouched():
    src = {"hook": 20, "pacing": 9, "hook_quote": "q", "why_it_works": "w", "editor_fix": "e"}
    out = split_half_breakdown(src)
    assert out == {"hook": 20, "pacing": 9, "hook_quote": None, "why_it_works": None, "editor_fix": None}
    assert src["hook_quote"] == "q" and src["editor_fix"] == "e"
