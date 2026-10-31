import pytest

from src.workflows.boundaries import USABLE_BOUNDARY
from src.workflows.errors import WorkflowError
from src.workflows.units import build_units, format_clock, units_fingerprint


def _words(*items, speaker=None):
    """Sidecar words in ms, like the transcript cache: (text, start_s, end_s[, speaker])."""
    words = []
    for item in items:
        text, start, end = item[:3]
        words.append(
            {
                "text": text,
                "start": int(round(start * 1000)),
                "end": int(round(end * 1000)),
                "confidence": 0.99,
                "speaker": item[3] if len(item) > 3 else speaker,
            }
        )
    return words


def _flow(text: str, start: float, *, step: float = 0.3, gap: float = 0.05, speaker=None):
    """Space-separated text as words of ``step`` seconds with ``gap`` between them."""
    items = []
    cursor = start
    for token in text.split():
        items.append((token, cursor, cursor + step, speaker))
        cursor += step + gap
    return items


def _data(words, language_code="en"):
    return {"version": 2, "words": words, "utterances": [], "language_code": language_code}


def _texts(index):
    return [unit.text for unit in index.units]


def test_sentence_ends_across_scripts():
    words = _words(
        ("We", 0.0, 0.2), ("won.", 0.25, 0.5),
        ("Really!", 0.6, 0.9),
        ("Why?", 1.0, 1.3),
        ("真的。", 1.4, 1.7),
        ("为什么？", 1.8, 2.1),
        ("好！", 2.2, 2.4),
        ("لماذا؟", 2.5, 2.8),
        ("ठीक", 2.9, 3.1), ("है।", 3.15, 3.4),
        ("So…", 3.5, 3.8),
        ("Done", 3.85, 4.0), ("here", 4.05, 4.3),
    )
    index = build_units(_data(words), "")
    assert _texts(index) == ["We won.", "Really!", "Why?", "真的。", "为什么？", "好！", "لماذا؟", "ठीक है।", "So…", "Done here"]
    assert [unit.end_kind for unit in index.units][:9] == ["sentence"] * 9
    assert index.units[-1].end_kind == "eof"
    assert [unit.ends_question for unit in index.units] == [False, False, True, False, True, False, True, False, False, False]


def test_abbreviations_initials_and_german_ordinals_do_not_end_sentences():
    words = _words(
        ("Dr.", 0.0, 0.2), ("Smith", 0.25, 0.5), ("met", 0.55, 0.7), ("J.", 0.75, 0.9), ("Doe.", 0.95, 1.2),
        ("We", 1.6, 1.7), ("left", 1.75, 1.9), ("so...", 1.95, 2.2), ("quickly", 2.25, 2.5), ("though.", 2.55, 2.8),
    )
    assert _texts(build_units(_data(words), "")) == ["Dr. Smith met J. Doe.", "We left so... quickly though."]

    german = _words(
        ("Am", 0.0, 0.2), ("3.", 0.25, 0.4), ("Oktober", 0.45, 0.8), ("wählen", 0.85, 1.1), ("wir.", 1.15, 1.4),
        ("Das", 1.8, 1.9), ("war", 1.95, 2.1), ("2019.", 2.15, 2.5), ("Dann", 2.9, 3.1), ("kam", 3.15, 3.3), ("mehr.", 3.35, 3.6),
    )
    assert _texts(build_units(_data(german, "de"), "")) == ["Am 3. Oktober wählen wir.", "Das war 2019.", "Dann kam mehr."]

    answers = _words(
        ("Did", 0.0, 0.2), ("you?", 0.25, 0.5), ("No.", 0.6, 0.9), ("We", 0.95, 1.1), ("did", 1.15, 1.3),
        ("No.", 1.35, 1.5), ("5", 1.55, 1.7), ("first.", 1.75, 2.0), ("So", 2.05, 2.2), ("did", 2.25, 2.4), ("I.", 2.45, 2.6),
        ("Then", 2.65, 2.8), ("it", 2.85, 3.0), ("ended.", 3.05, 3.3),
    )
    assert _texts(build_units(_data(answers), "")) == ["Did you?", "No.", "We did No. 5 first.", "So did I.", "Then it ended."]


def test_pause_and_speaker_changes_end_units():
    words = _words(
        ("We", 0.0, 0.2, "A"), ("waited", 0.25, 0.6, "A"),
        ("Then", 1.6, 1.8, "A"), ("it", 1.85, 2.0, "A"),
        ("Right", 2.1, 2.5, "B"), ("okay", 2.55, 2.8, "B"),
    )
    index = build_units(_data(words), "")
    assert _texts(index) == ["We waited", "Then it", "Right okay"]
    assert [unit.end_kind for unit in index.units] == ["pause", "speaker", "eof"]
    assert [unit.speaker for unit in index.units] == ["A", "A", "B"]
    assert [unit.starts_sentence for unit in index.units] == [True, True, True]
    assert index.boundary_strength(1) == pytest.approx(0.55)
    assert index.units[0].gap_after == pytest.approx(1.0)


def test_a_sentence_carrying_on_in_lowercase_after_a_breath_stays_one_unit():
    breath = _words(("its", 0.0, 0.2), ("disruption", 0.25, 0.8), ("of", 1.65, 1.8), ("sleep.", 1.85, 2.2))
    index = build_units(_data(breath), "")
    assert _texts(index) == ["its disruption of sleep."]

    # A very long silence still ends the unit, but the rest is mid-sentence:
    # no sentence start, and too weak a cut for a planner to use.
    long_silence = _words(("I", 0.0, 0.2), ("was", 0.25, 0.4), ("thinking", 0.45, 0.8), ("about", 3.5, 3.7), ("it.", 3.75, 4.0))
    index = build_units(_data(long_silence), "")
    assert _texts(index) == ["I was thinking", "about it."]
    assert index.units[0].end_kind == "pause"
    assert not index.units[1].starts_sentence
    assert index.boundary_strength(1) < USABLE_BOUNDARY

    # Scripts without case can't tell, so the pause ends the unit as before.
    hindi = _words(("मैं", 0.0, 0.2), ("सोच", 0.25, 0.5), ("रहा", 1.5, 1.7), ("था।", 1.75, 2.0))
    assert len(build_units(_data(hindi, "hi"), "").units) == 2


def test_ms_are_converted_to_seconds_after_repairing_zero_length_words():
    words = _words(("Okay.", 1.0, 1.4), ("And", 2.0, 2.2), ("then", 2.25, 2.4), ("go.", 2.5, 2.5))
    index = build_units(_data(words), "")
    assert index.words[0].start == pytest.approx(1.0)
    assert index.words[0].end == pytest.approx(1.4)
    # The zero-length "go." gets a slot instead of vanishing.
    assert index.words[-1].end > index.words[-1].start
    assert index.units[0].start == pytest.approx(1.0)
    assert index.units[-1].text == "And then go."


def test_long_sentences_split_at_the_widest_breath():
    tokens = [f"w{n}" for n in range(30)]
    items = _flow(" ".join(tokens[:15]), 0.0, step=0.4, gap=0.05)
    items += _flow(" ".join(tokens[15:]) + ".", items[-1][2] + 0.5, step=0.4, gap=0.05)
    index = build_units(_data(_words(*items)), "")
    # 30 words but ~13 s and 30 < 40: one sentence, no split.
    assert len(index.units) == 1

    slow = _flow(" ".join(tokens[:15]), 0.0, step=0.6, gap=0.1)
    slow += _flow(" ".join(tokens[15:]) + ".", slow[-1][2] + 0.45, step=0.6, gap=0.1)
    index = build_units(_data(_words(*slow)), "")
    assert [len(unit.text.split()) for unit in index.units] == [15, 15]
    assert index.units[0].end_kind == "forced"
    assert not index.units[1].starts_sentence


def test_force_split_prefers_a_clause_mark_then_fills_to_forty_words():
    clause = " ".join(f"w{n}" for n in range(20)) + ", " + " ".join(f"v{n}" for n in range(25)) + "."
    index = build_units(_data(_words(*_flow(clause.replace(" ,", ","), 0.0, step=0.2, gap=0.02))), "")
    assert [len(unit.text.split()) for unit in index.units] == [20, 25]
    assert index.units[0].end_kind == "clause"

    plain = " ".join(f"w{n}" for n in range(90)) + "."
    index = build_units(_data(_words(*_flow(plain, 0.0, step=0.15, gap=0.02))), "")
    counts = [len(unit.text.split()) for unit in index.units]
    assert counts[0] == 40 and sum(counts) == 90
    assert all(count <= 40 for count in counts)
    assert all(unit.end - unit.start <= 20 for unit in index.units)


def test_short_fragments_merge_into_the_next_unit():
    # Clause units: five slow words up to a comma (8 s, over the 7 s clause
    # limit), then "and we left." The force split leaves "w4," as a one-word
    # fragment that runs straight on (0.27 s), so it joins the next clause.
    items = _flow("w0 w1 w2 w3 w4,", 0.0, step=1.6, gap=0.02)
    items += _flow("and we left.", items[-1][2] + 0.27, step=0.15, gap=0.02)
    index = build_units(_data(_words(*items)), "", granularity="clause")
    assert _texts(index) == ["w0 w1 w2 w3", "w4, and we left."]
    assert index.units[0].end_kind == "forced"

    # A 16-word clause splits at 14 words only if that leaves 3 behind;
    # otherwise the split moves earlier instead of stranding a scrap.
    items = _flow(" ".join(f"w{n}" for n in range(15)) + " w15,", 0.0, step=0.15, gap=0.02)
    items += _flow("and we left.", items[-1][2] + 0.27, step=0.15, gap=0.02)
    index = build_units(_data(_words(*items)), "", granularity="clause")
    assert _texts(index) == [" ".join(f"w{n}" for n in range(13)), "w13 w14 w15,", "and we left."]

    # A fragment that is a whole sentence ("Yes.") stays on its own.
    words = _words(("Yes.", 0.0, 0.3), ("We", 0.35, 0.5), ("did", 0.52, 0.7), ("it.", 0.72, 1.0))
    assert _texts(build_units(_data(words), "")) == ["Yes.", "We did it."]
    # So does one that ends on a pause.
    words = _words(("So", 0.0, 0.2), ("um", 0.25, 0.4), ("We", 1.4, 1.5), ("did", 1.52, 1.7), ("it.", 1.72, 1.9))
    assert _texts(build_units(_data(words), "")) == ["So um", "We did it."]


def test_cjk_text_joins_without_spaces_and_detects_language():
    words = _words(("我们", 0.0, 0.3), ("昨天", 0.32, 0.6), ("去了", 0.62, 0.9), ("银行。", 0.92, 1.3), ("然后", 1.6, 1.9), ("呢？", 1.92, 2.2))
    index = build_units(_data(words, None), "")
    assert index.language == "zh"
    assert index.no_space
    assert _texts(index) == ["我们昨天去了银行。", "然后呢？"]
    assert index.text(1, 2) == "我们昨天去了银行。然后呢？"
    assert index.units[1].ends_question

    japanese = _words(("銀行に", 0.0, 0.4), ("行きました。", 0.42, 1.0))
    assert build_units(_data(japanese, "ja"), "").language == "ja"


def test_language_codes_are_normalized_and_scripts_detected():
    assert build_units(_data(_words(("Hi.", 0, 0.3)), "en_us"), "").language == "en"
    assert build_units(_data(_words(("Hi.", 0, 0.3)), "pt-BR"), "").language == "pt"
    hindi = _words(("मुझे", 0.0, 0.3), ("नहीं", 0.32, 0.6), ("पता।", 0.62, 0.9))
    assert build_units(_data(hindi, None), "").language == "hi"
    assert build_units(_data(_words(("Hello", 0, 0.3)), None), "").language is None


def test_clause_granularity_splits_at_breaths_and_commas():
    words = _words(
        ("When", 0.0, 0.2), ("we", 0.22, 0.3), ("finally", 0.32, 0.6), ("landed,", 0.62, 1.0),
        ("the", 1.3, 1.4), ("phone", 1.42, 1.7), ("rang", 1.72, 2.0),
        ("and", 2.6, 2.7), ("nobody", 2.72, 3.0), ("answered.", 3.02, 3.5),
    )
    sentence = build_units(_data(words), "")
    clause = build_units(_data(words), "", granularity="clause")
    assert len(sentence.units) == 1
    assert _texts(clause) == ["When we finally landed,", "the phone rang", "and nobody answered."]
    assert [unit.end_kind for unit in clause.units] == ["clause", "clause", "eof"]
    assert clause.granularity == "clause"
    # Both sides keep at least three words: a comma after two words stays inside.
    short = _words(("Yes,", 0.0, 0.3), ("so", 0.6, 0.7), ("we", 0.72, 0.8), ("went", 0.82, 1.0), ("home.", 1.02, 1.3))
    assert len(build_units(_data(short), "", granularity="clause").units) == 1


def test_clause_granularity_forces_splits_at_fourteen_words():
    words = _flow(" ".join(f"w{n}" for n in range(30)) + ".", 0.0, step=0.15, gap=0.02)
    index = build_units(_data(_words(*words)), "", granularity="clause")
    assert all(len(unit.text.split()) <= 14 for unit in index.units)
    assert sum(len(unit.text.split()) for unit in index.units) == 30


def test_ranges_never_reach_into_a_neighbouring_word():
    words = _words(
        ("First", 0.0, 0.3), ("one.", 0.32, 0.6),
        ("Close", 0.65, 0.9), ("call.", 0.92, 1.2),  # starts 50 ms after "one."
        ("Long", 3.0, 3.3), ("pause.", 3.32, 3.6),
        ("Overlap", 3.55, 3.9), ("here.", 3.92, 4.2),  # ASR overlap with "pause."
    )
    index = build_units(_data(words), "")
    assert len(index.units) == 4
    for unit in index.units:
        start, end = index.range_seconds(unit.id, unit.id)
        if unit.w0 > 0:
            assert start >= index.words[unit.w0 - 1].end
        if unit.w1 + 1 < len(index.words):
            following = index.words[unit.w1 + 1].start
            assert end <= following
            assert end <= max(index.words[unit.w1].end, following - 0.03) + 1e-9
        overlapped = unit.w0 > 0 and index.words[unit.w0 - 1].end > index.words[unit.w0].start
        # Only an ASR overlap pushes the cut-in past the unit's first word start.
        assert (start <= index.words[unit.w0].start) != overlapped
    # Lead-in and tail where there is room.
    assert index.range_seconds(3, 3)[0] == pytest.approx(2.9)
    assert index.range_seconds(2, 2)[1] == pytest.approx(1.55)
    # Whole-millisecond truncation never lands on the previous word's end.
    for unit in index.units[1:]:
        assert int(index.cut_in[unit.id] * 1000) >= int(round(index.words[unit.w0 - 1].end * 1000))


def test_ranges_for_splits_around_skips_and_text_follows():
    words = []
    for n in range(6):
        words += _flow(f"s{n} a b c.", n * 2.0, step=0.3, gap=0.05)
    index = build_units(_data(_words(*words)), "")
    assert len(index.units) == 6
    ranges = index.ranges_for(1, 6, [(3, 4)])
    assert len(ranges) == 2
    assert ranges[0] == (index.cut_in[1], index.cut_out[2])
    assert ranges[1] == (index.cut_in[5], index.cut_out[6])
    assert index.text(1, 6, [(3, 4)]) == "s0 a b c. s1 a b c. s4 a b c. s5 a b c."
    assert index.ranges_for(2, 3, [(1, 1), (6, 6)]) == [index.range_seconds(2, 3)]


def test_unit_lookup_and_clamping():
    index = build_units(_data(_words(("One.", 1.0, 1.5), ("Two.", 3.0, 3.5), ("Three.", 5.0, 5.5))), "")
    assert index.unit(2).text == "Two."
    with pytest.raises(KeyError):
        index.unit(0)
    with pytest.raises(KeyError):
        index.unit(4)
    assert index.clamp_id(-5) == 1 and index.clamp_id(99) == 3
    assert index.unit_at(0.0) == 1
    assert index.unit_at(3.2) == 2
    assert index.unit_at(4.0) == 3  # in the silence: the next unit
    assert index.unit_at(99.0) == 3


def test_coarse_fallback_builds_units_from_timed_lines():
    text = "\n".join(
        [
            "[00:00 - 00:04] Speaker A: Welcome back to the show.",
            "[00:04 - 00:09] Speaker A: Today we talk about money",
            "[00:10 - 00:15] Speaker B: Where did it all go?",
            "not a timed line",
            "[01:02:03 - 01:02:08] And that is it.",
        ]
    )
    index = build_units(None, text)
    assert index.coarse
    assert [unit.text for unit in index.units] == [
        "Welcome back to the show.", "Today we talk about money", "Where did it all go?", "And that is it."
    ]
    assert [unit.speaker for unit in index.units] == ["A", "A", "B", None]
    assert [unit.end_kind for unit in index.units] == ["line", "line", "line", "eof"]
    assert index.units[0].w0 == index.units[0].w1 == -1
    # Lines are stamped in whole seconds: a line keeps the stamped second
    # after its end unless the next line starts inside it.
    assert index.range_seconds(1, 1) == (0.0, 4.0)
    assert index.range_seconds(2, 2) == (4.0, 10.0)
    assert index.range_seconds(2, 3) == (4.0, 16.0)
    assert index.range_seconds(4, 4) == (3723.0, 3728.0)
    assert index.units[1].gap_after == pytest.approx(1.0)
    assert index.units[2].ends_question
    assert index.boundary_strength(1) == 0.8 and index.boundary_strength(2) == 0.6
    # An empty sidecar falls back too.
    assert build_units({"words": []}, text).coarse


def test_coarse_ignores_clause_granularity():
    index = build_units(None, "[00:00 - 00:04] One, two, three, four.", granularity="clause")
    assert index.granularity == "sentence" and len(index.units) == 1


def test_untimed_transcript_raises():
    with pytest.raises(WorkflowError) as raised:
        build_units(None, "Just some words without any timing")
    assert raised.value.code == "transcript_untimed"
    assert "Try uploading the file instead" in raised.value.message


def test_fingerprint_is_stable_and_granularity_independent():
    words = _words(("One.", 1.0, 1.5), ("Two.", 3.0, 3.5))
    first = units_fingerprint(_data(words), "")
    assert first == units_fingerprint(_data([dict(word) for word in words]), "ignored")
    assert build_units(_data(words), "").fingerprint == build_units(_data(words), "", granularity="clause").fingerprint == first
    changed = [dict(words[0]), {**words[1], "text": "Three."}]
    assert units_fingerprint(_data(changed), "") != first
    lines = "[00:00 - 00:04] Hello there."
    assert units_fingerprint(None, lines) == build_units(None, lines).fingerprint != first


def test_listing_marks_speakers_pauses_and_hidden_stretches():
    words = _words(
        ("And", 2480.0, 2480.3, "B"), ("that's", 2480.35, 2480.6, "B"), ("when", 2480.65, 2480.9, "B"),
        ("the", 2480.95, 2481.1, "B"), ("bank", 2481.15, 2481.5, "B"), ("called.", 2481.55, 2483.2, "B"),
        ("Twice.", 2484.0, 2485.1, "B"),
        ("What", 2486.9, 2487.2, "A"), ("did", 2487.25, 2487.5, "A"), ("you", 2487.55, 2487.8, "A"), ("do?", 2487.85, 2489.3, "A"),
        ("Nothing.", 2789.3, 2790.0, "A"),
    )
    index = build_units(_data(words), "")
    assert index.listing() == "\n".join(
        [
            "#1 41:20 +3.2s B: And that's when the bank called.",
            "#2 41:24 +1.1s Twice. ⏸1.8s",
            "#3 41:26 +2.4s A: What did you do? ⏸300.0s",
            "#4 46:29 +0.7s Nothing.",
        ]
    )
    assert index.listing([1, 4]) == "\n".join(
        [
            "#1 41:20 +3.2s B: And that's when the bank called.",
            "··· 5:06 not shown ···",
            "#4 46:29 +0.7s A: Nothing.",
        ]
    )
    assert index.listing([2], with_durations=False) == "#2 41:24 B: Twice. ⏸1.8s"


def test_clock_format():
    assert format_clock(5) == "0:05"
    assert format_clock(752.9) == "12:32"
    assert format_clock(3725) == "1:02:05"


def test_whisper_words_without_speakers_or_language():
    words = [
        {"text": " Hello.", "start": 2660, "end": 3240, "confidence": 0.6, "speaker": None},
        {"text": " Is", "start": 3580, "end": 4019, "confidence": 0.8, "speaker": None},
        {"text": " this", "start": 4019, "end": 4100, "confidence": 0.9, "speaker": None},
        {"text": " working?", "start": 4100, "end": 4300, "confidence": 0.9, "speaker": None},
        {"text": " ", "start": 4400, "end": 4500, "confidence": 0.9, "speaker": None},
    ]
    index = build_units({"words": words}, "")
    assert _texts(index) == ["Hello.", "Is this working?"]
    assert all(unit.speaker is None for unit in index.units)
    assert index.listing() == "#1 0:02 +0.6s Hello.\n#2 0:03 +0.7s Is this working?"
