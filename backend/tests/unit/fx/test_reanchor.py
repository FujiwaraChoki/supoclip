"""reanchor_beats: beats follow their source words when cleanup changes the clip timeline (§13.1)."""

from src.edit_fx.beats import reanchor_beats, union_swears, validate_llm_plan, words_hash
from src.edit_fx.contracts import BeatTagLLM, EditBeats, EditPlanLLM, ReactionLLM

SOURCE = ("So um I literally made two million dollars last year . "
          "And then uh the fucking bank froze everything . "
          "That is when I learned the secret : stay consistent every single day .")


def source_words():
    """Source transcript (A seconds): 0.4 s per word, a 1.5 s pause after each '.'"""
    words = []
    t = 30.0
    for token in SOURCE.split():
        if token in (".", ":"):
            if words:
                words[-1]["text"] += token
            t += 1.5 if token == "." else 0.0
            continue
        words.append({"text": token, "a0": round(t, 3), "a1": round(t + 0.3, 3)})
        t += 0.4
    return words


def project(ranges, *, drop=()):
    """Clip words for keep ranges (A) stitched back to back: S = cursor + (A - range start)."""
    out = []
    cursor = 0.0
    for a_start, a_end in ranges:
        for word in source_words():
            if word["text"].strip(".,:").lower() in drop:
                continue
            if word["a0"] >= a_start and word["a1"] <= a_end:
                out.append({"text": word["text"], "start": round(cursor + word["a0"] - a_start, 3),
                            "end": round(cursor + word["a1"] - a_start, 3),
                            "src_start": word["a0"], "src_end": word["a1"]})
        cursor += a_end - a_start
    return out


def index_of(words, text):
    return next(i for i, w in enumerate(words) if w["text"].strip(".,:").lower() == text)


def planned(words):
    tags = [
        BeatTagLLM(kind="hook", first_word=0, last_word=index_of(words, "year"), strength=3, tone="hype",
                   emoji="", label=""),
        BeatTagLLM(kind="number", first_word=index_of(words, "two"), last_word=index_of(words, "dollars"),
                   strength=3, tone="hype", emoji="💰", label=""),
        BeatTagLLM(kind="emotional_turn", first_word=index_of(words, "bank"), last_word=index_of(words, "everything"),
                   strength=2, tone="shocked", emoji="", label="NOPE"),
        BeatTagLLM(kind="payoff", first_word=index_of(words, "stay"), last_word=index_of(words, "day"),
                   strength=3, tone="wholesome", emoji="", label=""),
    ]
    out = EditPlanLLM(mood="inspiring", sensitivity="normal", hook_last_word=index_of(words, "year"), beats=tags,
                      reactions=[ReactionLLM(word=index_of(words, "froze"), query="shocked face")])
    return validate_llm_plan(out, words, model="m", emoji_keys=frozenset({"💰"}))


FULL = [(29.9, 60.0)]


def test_follows_words_through_a_pause_cut():
    before = project(FULL)
    beats = planned(before).model_copy(update={"timeline_hash": "old"})
    # cleanup cuts 1.5 s of the pause after the first sentence (A 33.95 .. 35.45)
    after_words = project([(29.9, 33.95), (35.45, 60.0)])
    after = reanchor_beats(beats, after_words)
    assert after is not None
    old = {b.id: b for b in beats.beats}
    new = {b.id: b for b in after.beats}
    assert set(old) <= set(new)
    # the first sentence did not move; everything after the cut moved earlier by the removed 1.5 s
    assert new[_id(beats, "hook")].start == old[_id(beats, "hook")].start
    turn_id = _id(beats, "emotional_turn")
    assert round(old[turn_id].start - new[turn_id].start, 3) == 1.5
    payoff_id = _id(beats, "payoff")
    assert round(old[payoff_id].start - new[payoff_id].start, 3) == 1.5
    # attributes survive; A times do not change
    assert new[turn_id].label == "NOPE" and new[turn_id].tone == "shocked"
    assert new[turn_id].src_start == old[turn_id].src_start
    assert after.words_hash == words_hash(after_words) != beats.words_hash
    assert after.timeline_hash == ""
    assert after.hook_end == new[_id(beats, "hook")].end
    assert after.reactions == ()


def test_follows_words_when_a_filler_is_removed():
    before = project(FULL)
    beats = planned(before)
    after_words = project([(29.9, 30.35), (30.75, 60.0)], drop=("uh",))   # "um" cut out, "uh" dropped
    after = reanchor_beats(beats, after_words)
    assert after is not None
    hook = next(b for b in after.beats if b.kind == "hook")
    assert "um" not in hook.words and "um" not in hook.text.lower().split()
    assert hook.words[:3] == ("so", "i", "literally")
    number = next(b for b in after.beats if b.kind == "number")
    assert (number.value, number.prefix, number.suffix) == (2.0, "$", "M")
    assert number.start == after_words[index_of(after_words, "two")]["start"]


def test_swears_land_on_the_new_times():
    before = project(FULL)
    beats = planned(before)
    swear_before = next(b for b in beats.beats if b.kind == "swear")
    after_words = project([(29.9, 33.95), (35.45, 60.0)])
    after = reanchor_beats(beats, after_words)
    swears = [b for b in after.beats if b.kind == "swear"]
    assert len(swears) == 1
    target = after_words[index_of(after_words, "fucking")]
    assert (swears[0].start, swears[0].end) == (target["start"], target["end"])
    assert swears[0].start == round(swear_before.start - 1.5, 3)


def test_swear_list_is_rederived_for_beats_without_source_times():
    words = project(FULL)
    beats = EditBeats(words_hash="x", beats=())
    after = reanchor_beats(beats, words)
    assert [b.text for b in after.beats] == ["fucking"]
    assert union_swears(after, words) == after


def test_returns_none_below_half_survival():
    before = project(FULL)
    beats = planned(before)
    # keep only the first sentence: hook and number survive, the swear, turn and payoff do not → 2 of 5
    after = reanchor_beats(beats, project([(29.9, 33.95)]))
    assert len(beats.beats) == 5
    assert after is None


def test_exactly_half_survives():
    before = project(FULL)
    beats = planned(before)
    kept = beats.model_copy(update={"beats": beats.beats[:4]})   # hook, number, swear, turn
    after = reanchor_beats(kept, project([(29.9, 33.95)]))      # hook + number survive = 2 / 4
    assert after is not None
    assert {b.kind for b in after.beats} == {"hook", "number"}


def test_drops_a_beat_whose_words_were_all_cut():
    before = project(FULL)
    beats = planned(before)
    # cut "bank froze everything" (A 37.5 .. 38.6) but keep the swear before it
    after_words = project([(29.9, 37.45), (38.65, 60.0)])
    after = reanchor_beats(beats, after_words)
    assert after is not None
    assert not [b for b in after.beats if b.kind == "emotional_turn"]


def test_none_when_words_have_no_source_times():
    before = project(FULL)
    beats = planned(before)
    bare = [{k: v for k, v in w.items() if k not in ("src_start", "src_end")} for w in before]
    assert reanchor_beats(beats, bare) is None


def test_unchanged_timeline_is_a_fixed_point():
    words = project(FULL)
    beats = planned(words)
    after = reanchor_beats(beats, words)
    assert after is not None
    assert [(b.id, b.kind, b.start, b.end) for b in after.beats] == [(b.id, b.kind, b.start, b.end)
                                                                    for b in beats.beats]


def _id(beats, kind):
    return next(b.id for b in beats.beats if b.kind == kind)
