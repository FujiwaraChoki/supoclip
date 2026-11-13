"""Beats: words_with_timing, validate_llm_plan (§7.3), heuristic_beats (§7.5) and union_swears (G7)."""

import json

import pytest

from src.edit_fx.beats import (
    MAX_BEATS,
    heuristic_beats,
    normalize_word,
    timeline_hash,
    union_swears,
    validate_llm_plan,
    words_hash,
    words_with_timing,
)
from src.edit_fx.constants import LLM_ALLOWED_EMOJI, TONE_EMOJI
from src.edit_fx.contracts import Beat, BeatTagLLM, EditBeats, EditPlanLLM, ReactionLLM
from src.emoji_captions import EMOJI_KEYWORD_MAP

EMOJI_KEYS = frozenset(EMOJI_KEYWORD_MAP.values()) | frozenset(TONE_EMOJI.values()) | frozenset(LLM_ALLOWED_EMOJI)


def make_words(text, *, step=0.4, dur=0.3, src_offset=100.0, pauses=None):
    """Words at a fixed pace; `pauses` = {word index: extra seconds after that word}."""
    words = []
    t = 0.0
    for index, token in enumerate(text.split()):
        words.append({"text": token, "start": round(t, 3), "end": round(t + dur, 3),
                      "src_start": round(src_offset + t, 3), "src_end": round(src_offset + t + dur, 3)})
        t += step + (pauses or {}).get(index, 0.0)
    return words


def tag(kind, first, last=None, *, strength=2, tone="neutral", emoji="", label=""):
    return BeatTagLLM(kind=kind, first_word=first, last_word=first if last is None else last, strength=strength,
                      tone=tone, emoji=emoji, label=label)


def plan(*tags, mood="hype", sensitivity="normal", hook_last_word=-1, reactions=()):
    return EditPlanLLM(mood=mood, sensitivity=sensitivity, hook_last_word=hook_last_word, beats=list(tags),
                       reactions=list(reactions))


TALK = ("So I literally made $2,000,000 in one year. Nobody believed me. Then the bank called and said the money "
        "was gone. That is when I learned the real secret. You have to stay consistent every single day.")


# ----------------------------------------------------------------------------- hashes and timing


def test_words_hash_is_stable_and_rounds_to_centiseconds():
    words = make_words("hello there world")
    nudged = [dict(w, start=w["start"] + 0.001, end=w["end"] - 0.001) for w in words]
    assert words_hash(words) == words_hash(nudged)
    assert len(words_hash(words)) == 64
    expected_blob = json.dumps([[w["text"], round(w["start"], 2), round(w["end"], 2)] for w in words],
                               ensure_ascii=False, separators=(",", ":"))
    import hashlib
    assert words_hash(words) == hashlib.sha256(expected_blob.encode()).hexdigest()
    changed = list(words)
    changed[1] = dict(changed[1], text="their")
    assert words_hash(changed) != words_hash(words)


def test_timeline_hash_changes_with_ranges_offsets_and_words():
    words = make_words("a b c")
    base = timeline_hash([(1.0, 5.0)], [0.0], words)
    assert len(base) == 32
    assert base == timeline_hash([(1.0, 5.0)], [0.0], words)
    assert base != timeline_hash([(1.0, 5.5)], [0.0], words)
    assert base != timeline_hash([(1.0, 5.0)], [0.1], words)
    assert base != timeline_hash([(1.0, 5.0)], [0.0], make_words("a b d"))


def test_words_with_timing_word_mode_adds_ids_and_keeps_extra_keys():
    words = make_words("one two three four five")
    out, timing = words_with_timing(words)
    assert timing == "word"
    assert [w["id"] for w in out] == ["w0", "w1", "w2", "w3", "w4"]
    assert out[0]["src_start"] == 100.0
    assert "id" not in words[0]  # input not mutated


def test_words_with_timing_tolerates_a_few_degenerate_words():
    words = make_words("a b c d e f g h i j")
    words[3] = dict(words[3], end=words[3]["start"])          # 1 of 10 broken → still "word"
    out, timing = words_with_timing(words)
    assert timing == "word"
    assert out[3]["end"] >= out[3]["start"]


def test_words_with_timing_falls_back_to_lines_by_character_count():
    broken = [{"text": t, "start": 0.0, "end": 0.0} for t in "hi there everybody".split()]
    lines = [{"start": 10.0, "end": 12.0, "text": "hi there everybody"},
             {"start": 13.0, "end": 14.0, "text": "go"}]
    out, timing = words_with_timing(broken, lines)
    assert timing == "approx"
    assert [w["text"] for w in out] == ["hi", "there", "everybody", "go"]
    # 2 + 5 + 9 = 16 chars over 2 s
    assert out[0]["start"] == 10.0 and out[0]["end"] == pytest.approx(10.25)
    assert out[1]["end"] == pytest.approx(10.875)
    assert out[2]["end"] == pytest.approx(12.0)
    assert out[3]["start"] == 13.0 and out[3]["end"] == 14.0
    assert [w["id"] for w in out] == ["w0", "w1", "w2", "w3"]


def test_words_with_timing_approx_spreads_source_times_too():
    out, timing = words_with_timing([], [{"start": 0.0, "end": 1.0, "src_start": 50.0, "src_end": 51.0,
                                         "text": "ab cd"}])
    assert timing == "approx"
    assert out[1]["src_start"] == 50.5 and out[1]["src_end"] == 51.0


def test_words_with_timing_none_without_words_or_lines():
    assert words_with_timing([]) == ([], "none")
    assert words_with_timing([{"text": "x", "start": 1, "end": 1}] * 5) == ([], "none")
    assert words_with_timing([], [{"start": 1, "end": 1, "text": "zero length"}]) == ([], "none")


# ----------------------------------------------------------------------------- validate_llm_plan


def test_validate_clamps_ids_swaps_and_cuts_spans():
    words = make_words(TALK)
    n = len(words)
    out = validate_llm_plan(plan(
        tag("emphasis", 999, 999),           # past the end → last word
        tag("emotional_turn", 12, 9),        # swapped
        tag("build", 20, 35),                # 16 words → 8
        tag("punchline", -5, 1, strength=9), # clamped to 0, strength clamped to 3
    ), words, model="google-gla:x", emoji_keys=EMOJI_KEYS)
    by_kind = {b.kind: b for b in out.beats}
    assert by_kind["emphasis"].start == words[n - 1]["start"]
    assert by_kind["emotional_turn"].start == words[9]["start"]
    assert by_kind["emotional_turn"].end == words[12]["end"]
    assert by_kind["build"].start == words[20]["start"] and by_kind["build"].end == words[27]["end"]
    assert len(by_kind["build"].words) == 8
    assert by_kind["punchline"].strength == 3 and by_kind["punchline"].start == 0.0
    assert out.model == "google-gla:x" and out.source == "llm"
    assert out.words_hash == words_hash(words)
    assert out.timeline_hash == ""


def test_validate_ids_are_in_time_order_and_times_come_from_words():
    words = make_words(TALK)
    out = validate_llm_plan(plan(tag("payoff", 30), tag("emphasis", 2), tag("number", 4)), words,
                            model="m", emoji_keys=EMOJI_KEYS)
    assert [b.id for b in out.beats] == ["b0", "b1", "b2"]
    assert [b.kind for b in out.beats] == ["emphasis", "number", "payoff"]
    starts = [b.start for b in out.beats]
    assert starts == sorted(starts)
    assert out.beats[0].start == words[2]["start"] and out.beats[0].end == words[2]["end"]


def test_validate_beats_carry_source_times_and_identity_words():
    words = make_words(TALK)
    out = validate_llm_plan(plan(tag("emphasis", 1, 3)), words, model="m", emoji_keys=EMOJI_KEYS)
    beat = out.beats[0]
    assert beat.src_start == words[1]["src_start"] and beat.src_end == words[3]["src_end"]
    assert beat.words == ("i", "literally", "made")
    assert beat.text == "I literally made"


def test_validate_keeps_one_hook_in_the_first_two_sentences():
    words = make_words(TALK)
    # sentence 0 = words 0..7, sentence 1 = "Nobody believed me." (8..10), sentence 2 starts at word 11
    out = validate_llm_plan(plan(tag("hook", 14, 16, strength=3), tag("hook", 0, 5, strength=2),
                                 tag("hook", 9, 10, strength=2)), words, model="m", emoji_keys=EMOJI_KEYS)
    hooks = [b for b in out.beats if b.kind == "hook"]
    assert len(hooks) == 1 and hooks[0].start == 0.0


def test_validate_drops_a_hook_after_the_second_sentence():
    words = make_words(TALK)
    out = validate_llm_plan(plan(tag("hook", 14, 16, strength=3)), words, model="m", emoji_keys=EMOJI_KEYS)
    assert not [b for b in out.beats if b.kind == "hook"]


def test_validate_keeps_the_two_strongest_payoffs():
    words = make_words(TALK)
    out = validate_llm_plan(plan(tag("payoff", 20, strength=1), tag("payoff", 25, strength=3),
                                 tag("payoff", 30, strength=2)), words, model="m", emoji_keys=EMOJI_KEYS)
    payoffs = sorted(b.start for b in out.beats if b.kind == "payoff")
    assert payoffs == [words[25]["start"], words[30]["start"]]


def test_validate_merges_overlapping_same_kind_beats():
    words = make_words(TALK)
    out = validate_llm_plan(plan(tag("emphasis", 3, 5, strength=1, tone="hype"),
                                 tag("emphasis", 5, 6, strength=3, tone="shocked"),
                                 tag("number", 4, 5)), words, model="m", emoji_keys=EMOJI_KEYS)
    emphasis = [b for b in out.beats if b.kind == "emphasis"]
    assert len(emphasis) == 1
    assert emphasis[0].strength == 3 and emphasis[0].tone == "shocked"
    assert emphasis[0].start == words[3]["start"] and emphasis[0].end == words[6]["end"]
    assert [b for b in out.beats if b.kind == "number"]   # other kinds don't merge


def test_validate_at_most_one_beat_per_0_6_s_per_kind():
    words = make_words("a b c d e f g h", step=0.25, dur=0.2)
    out = validate_llm_plan(plan(tag("emphasis", 0, strength=1), tag("emphasis", 1, strength=3),
                                 tag("emphasis", 2, strength=2), tag("emphasis", 4, strength=1),
                                 tag("punchline", 1)), words, model="m", emoji_keys=EMOJI_KEYS)
    emphasis = sorted(b.start for b in out.beats if b.kind == "emphasis")
    # word 1 (strongest, 0.25 s) claims ±0.6 s; word 4 (1.0 s) is far enough
    assert emphasis == [0.25, 1.0]
    assert any(b.kind == "punchline" for b in out.beats)


def test_validate_never_spaces_out_swears():
    words = make_words("fucking shit fucking hell", step=0.2, dur=0.15)
    out = validate_llm_plan(plan(tag("swear", 0), tag("swear", 1), tag("swear", 2)), words, model="m",
                            emoji_keys=EMOJI_KEYS)
    assert len([b for b in out.beats if b.kind == "swear"]) == 3


def test_validate_caps_beats_at_40_by_priority():
    words = make_words(" ".join(f"w{i}" for i in range(200)), step=0.7, dur=0.5)
    tags = [tag("emphasis", i, strength=1) for i in range(0, 120)]
    tags += [tag("payoff", 150), tag("number", 160, strength=1), tag("punchline", 170)]
    out = validate_llm_plan(plan(*tags), words, model="m", emoji_keys=EMOJI_KEYS)
    assert len(out.beats) == MAX_BEATS
    kinds = {b.kind for b in out.beats}
    assert {"payoff", "number", "punchline"} <= kinds
    emphasis = [b for b in out.beats if b.kind == "emphasis"]
    assert len(emphasis) == MAX_BEATS - 3
    assert emphasis[-1].start < words[40]["start"]   # earliest emphasis kept on ties


def test_validate_filters_emoji_by_atlas_keys():
    words = make_words("I love money and pizza here")
    out = validate_llm_plan(plan(
        tag("emphasis", 2, emoji="💰"),         # in the atlas
        tag("number", 4, emoji="🦄"),           # not in the atlas, word "pizza" has no map entry → ""
        tag("emotional_turn", 1, emoji="🫠"),   # not in the atlas, "love" maps to ❤️
        tag("build", 5, emoji=""),              # model said none → none
    ), words, model="m", emoji_keys=EMOJI_KEYS)
    by_kind = {b.kind: b for b in out.beats}
    assert by_kind["emphasis"].emoji == "💰"
    assert by_kind["number"].emoji == ""
    assert by_kind["emotional_turn"].emoji == "❤️"
    assert by_kind["build"].emoji == ""


def test_validate_emoji_respects_a_narrow_key_set():
    words = make_words("I love money")
    out = validate_llm_plan(plan(tag("emphasis", 2, emoji="💰"), tag("emotional_turn", 1, emoji="🫠")), words,
                            model="m", emoji_keys=frozenset({"🔥"}))
    assert all(b.emoji == "" for b in out.beats)


def test_validate_emoji_matches_with_or_without_variation_selector():
    words = make_words("I love you")
    out = validate_llm_plan(plan(tag("emphasis", 1, emoji="❤")), words, model="m", emoji_keys=EMOJI_KEYS)
    assert out.beats[0].emoji == "❤️"


def test_validate_cleans_labels():
    words = make_words("wait what is happening")
    long_label = "x" * 60
    out = validate_llm_plan(plan(tag("punchline", 0, 1, label="WAIT 😱 WHAT\n\u0007"),
                                 tag("emphasis", 3, label=long_label)), words, model="m", emoji_keys=EMOJI_KEYS)
    by_kind = {b.kind: b for b in out.beats}
    assert by_kind["punchline"].label == "WAIT WHAT"
    assert by_kind["emphasis"].label == "x" * 40


def test_validate_parses_numbers():
    words = make_words("I made $2.5M and two million people forty percent hello")
    out = validate_llm_plan(plan(tag("number", 2), tag("number", 4, 5), tag("number", 7, 8),
                                 tag("number", 9)), words, model="m", emoji_keys=EMOJI_KEYS)
    numbers = [(b.value, b.prefix, b.suffix) for b in out.beats if b.kind == "number"]
    assert numbers == [(2.5, "$", "M"), (2.0, "", "M"), (40.0, "", "%"), (None, "", "")]


def test_validate_hook_end_and_reactions():
    words = make_words(TALK)
    out = validate_llm_plan(plan(tag("hook", 0, 8), hook_last_word=8,
                                 reactions=[ReactionLLM(word=11, query="  shocked   cat 😱 "),
                                            ReactionLLM(word=500, query="money gone"),
                                            ReactionLLM(word=11, query="Shocked cat"),
                                            ReactionLLM(word=3, query=""),
                                            ReactionLLM(word=4, query="rich"),
                                            ReactionLLM(word=5, query="too many")]),
                            words, model="m", emoji_keys=EMOJI_KEYS)
    assert out.hook_end == words[8]["end"]
    assert [(r.at, r.query) for r in out.reactions] == [
        (words[11]["start"], "shocked cat"), (words[-1]["start"], "money gone"), (words[4]["start"], "rich"),
    ]


def test_validate_hook_end_none_without_hook():
    words = make_words(TALK)
    assert validate_llm_plan(plan(tag("emphasis", 3)), words, model="m", emoji_keys=EMOJI_KEYS).hook_end is None


def test_validate_copies_mood_and_sensitivity():
    words = make_words(TALK)
    out = validate_llm_plan(plan(mood="dark", sensitivity="serious"), words, model="m", emoji_keys=EMOJI_KEYS)
    assert out.mood == "dark" and out.sensitivity == "serious" and out.beats == ()


def test_validate_without_words():
    out = validate_llm_plan(plan(tag("hook", 0, 3), hook_last_word=3), [], model="m", emoji_keys=EMOJI_KEYS)
    assert out.beats == () and out.timing == "none" and out.hook_end is None


def test_validate_applies_the_swear_union():
    words = make_words("this is fucking insane and the merda too")
    out = validate_llm_plan(plan(tag("emphasis", 3)), words, model="m", emoji_keys=EMOJI_KEYS)
    swears = [b for b in out.beats if b.kind == "swear"]
    assert [b.text for b in swears] == ["fucking", "merda"]
    assert all(b.strength == 2 and b.source == "heuristic" for b in swears)
    assert [b.id for b in out.beats] == [f"b{i}" for i in range(len(out.beats))]


def test_validate_output_round_trips_through_the_contract():
    words = make_words(TALK)
    out = validate_llm_plan(plan(tag("hook", 0, 8, strength=3), tag("number", 4), tag("payoff", 30)), words,
                            model="m", emoji_keys=EMOJI_KEYS)
    assert EditBeats.model_validate(out.model_dump(mode="json")) == out


# ----------------------------------------------------------------------------- union_swears


def _beats(*beats, timing="word"):
    return EditBeats(words_hash="x", beats=tuple(beats), timing=timing)


def test_union_swears_unicode_and_accents():
    words = make_words("Das ist Scheiße! Qué coño pasa? Putain, c'est enculé. Vaffanculo amico")
    out = union_swears(_beats(), words)
    assert [b.text for b in out.beats] == ["Scheiße!", "coño", "Putain,", "enculé.", "Vaffanculo"]
    assert out.beats[0].words == ("scheiße",)
    assert out.beats[1].words == ("coño",)


def test_union_swears_stems_phrases_and_masked_words():
    words = make_words("motherfucker said f*** and sh*t then figlio di puttana fuckin hell")
    out = union_swears(_beats(), words)
    texts = [b.text for b in out.beats]
    assert texts == ["motherfucker", "f***", "sh*t", "figlio di puttana", "fuckin"]


def test_union_swears_leaves_innocent_words_alone():
    words = make_words("con el perro anjing suka hello damn hell crap concha shiitake")
    assert union_swears(_beats(), words).beats == ()


def test_union_swears_skips_covered_words_and_keeps_existing_ids():
    words = make_words("hey you fucking idiot")
    llm_swear = Beat(id="b0", kind="swear", start=words[2]["start"], end=words[2]["end"], source="llm")
    emphasis = Beat(id="b1", kind="emphasis", start=words[3]["start"], end=words[3]["end"])
    out = union_swears(_beats(llm_swear, emphasis), words)
    assert out.beats == (llm_swear, emphasis)


def test_union_swears_adds_next_free_ids_and_is_idempotent():
    words = make_words("shit happens and then emphasis fuck")
    emphasis = Beat(id="b4", kind="emphasis", start=words[4]["start"], end=words[4]["end"])
    once = union_swears(_beats(emphasis), words)
    assert [(b.id, b.kind) for b in once.beats] == [("b5", "swear"), ("b4", "emphasis"), ("b6", "swear")]
    assert union_swears(once, words) == once


def test_union_swears_non_swear_beat_does_not_cover():
    words = make_words("this shit is good")
    emphasis = Beat(id="b0", kind="emphasis", start=0.0, end=words[-1]["end"])
    out = union_swears(_beats(emphasis), words)
    assert [b.kind for b in out.beats].count("swear") == 1


def test_union_swears_without_words_is_a_no_op():
    beats = _beats()
    assert union_swears(beats, []) is beats


def test_normalize_word_keeps_accents_and_drops_punctuation():
    assert normalize_word("Scheiße!") == "scheiße"
    assert normalize_word("«Coño»") == "coño"
    assert normalize_word("foda-se") == "fodase"
    assert normalize_word("...") == ""


# ----------------------------------------------------------------------------- heuristic_beats


def test_heuristic_beats_cover_every_rule():
    words = make_words(
        "So I literally made $2,000,000 in one year! This is insane. Then the fucking bank called and said no. "
        "Never give up, because the secret is consistency and that is the truth.",
        pauses={7: 0.5},
    )
    out = heuristic_beats(words, timing="word")
    assert out.source == "heuristic" and out.mood == "hype" and out.sensitivity == "normal" and out.model == ""
    assert out.timing == "word"
    by_kind = {}
    for beat in out.beats:
        by_kind.setdefault(beat.kind, []).append(beat)

    hook = by_kind["hook"][0]
    assert hook.start == 0.0 and hook.end == words[7]["end"]          # first sentence
    assert out.hook_end == words[7]["end"]

    number = by_kind["number"][0]
    assert (number.value, number.prefix, number.suffix) == (2_000_000.0, "$", "")
    assert number.text == "$2,000,000"

    punchline = by_kind["punchline"][0]
    assert punchline.end == words[7]["end"] and punchline.text == "$2,000,000 in one year!"

    swear = by_kind["swear"][0]
    assert swear.text == "fucking" and swear.strength == 2

    payoff = by_kind["payoff"][0]
    assert payoff.text == "truth." and payoff.strength >= 2
    build = by_kind["build"][0]
    assert build.end <= payoff.start and build.text == "the"

    emphasis = {b.text: b.strength for b in by_kind["emphasis"]}
    assert emphasis["literally"] == 2       # power word
    assert emphasis["insane."] == 2
    assert emphasis["bank"] == 1            # emoji word, not a power word
    assert "truth." not in emphasis          # promoted to payoff
    assert "$2,000,000" not in emphasis      # numbers are number beats

    assert [b.id for b in out.beats] == [f"b{i}" for i in range(len(out.beats))]
    assert all(b.src_start is not None and b.words for b in out.beats)


def test_heuristic_punchline_needs_a_pause():
    words = make_words("That was amazing! Then we left.")
    assert not [b for b in heuristic_beats(words, timing="word").beats if b.kind == "punchline"]
    paused = make_words("That was amazing! Then we left.", pauses={2: 0.4})
    assert [b.text for b in heuristic_beats(paused, timing="word").beats if b.kind == "punchline"] == [
        "That was amazing!"]


def test_heuristic_no_payoff_on_short_clips():
    words = make_words("This is the secret to success. Always remember the truth.")
    assert words[-1]["end"] < 12
    assert not [b for b in heuristic_beats(words, timing="word").beats if b.kind in ("payoff", "build")]


def test_heuristic_hook_is_capped_at_12_words():
    words = make_words(" ".join(["word"] * 20) + ".")
    hook = [b for b in heuristic_beats(words, timing="word").beats if b.kind == "hook"][0]
    assert hook.end == words[11]["end"]
    assert len(hook.words) == 8


def test_heuristic_timing_none_and_no_words():
    words = make_words("hello world")
    out = heuristic_beats(words, timing="none")
    assert out.beats == () and out.timing == "none"
    empty = heuristic_beats([], timing="word")
    assert empty.beats == () and empty.timing == "none" and empty.words_hash == words_hash([])


def test_heuristic_blank_tokens_make_no_hook():
    blank = [{"text": " ", "start": 0.0, "end": 0.4}, {"text": "", "start": 0.5, "end": 0.9}]
    assert heuristic_beats(blank, timing="word").beats == ()


def test_heuristic_approx_timing_still_plans_beats():
    lines = [{"start": 0.0, "end": 3.0, "text": "I made $500 this week!"},
             {"start": 4.0, "end": 6.0, "text": "That is insane"}]
    words, timing = words_with_timing([], lines)
    out = heuristic_beats(words, timing=timing)
    assert out.timing == "approx"
    assert any(b.kind == "number" and b.value == 500 for b in out.beats)


def test_heuristic_is_deterministic():
    words = make_words(TALK)
    assert heuristic_beats(words, timing="word") == heuristic_beats(words, timing="word")


def test_validate_empty_model_emoji_falls_back_to_the_keyword_map():
    """§7.3: an emoji not in the atlas (including "") uses EMOJI_KEYWORD_MAP on the beat's words."""
    words = make_words("I love money and pizza here")
    out = validate_llm_plan(plan(tag("emphasis", 2, emoji=""), tag("build", 4, emoji="")), words, model="m",
                            emoji_keys=EMOJI_KEYS)
    by_kind = {b.kind: b for b in out.beats}
    assert by_kind["emphasis"].emoji == EMOJI_KEYWORD_MAP["money"]
    assert by_kind["build"].emoji == ""


def test_validate_label_drops_variation_selectors_and_keycaps():
    words = make_words("wait what is happening")
    out = validate_llm_plan(plan(tag("punchline", 0, 1, label="LOVE ❤️ IT 1️⃣")), words,
                            model="m", emoji_keys=EMOJI_KEYS)
    assert out.beats[0].label == "LOVE IT 1"


def test_validate_never_exceeds_the_contract_beat_limit():
    """Swears are exempt from the 40 cap, but the EditBeats contract allows at most 200 beats."""
    words = make_words(" ".join(["fuck"] * 260))
    out = validate_llm_plan(plan(*[tag("swear", i) for i in range(260)], tag("hook", 0)), words, model="m",
                            emoji_keys=EMOJI_KEYS)
    assert len(out.beats) <= 200
    EditBeats.model_validate(out.model_dump())
