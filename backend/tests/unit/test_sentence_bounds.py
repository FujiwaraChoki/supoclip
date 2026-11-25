"""A highlight opens on a sentence that stands alone and closes on a finished one, in any language.

Synthetic transcripts only. Each test is one way a clip used to open or close badly.
"""

import json

import pytest

from src.media import render_ranges, sentence_bounds, timeline
from src.media.sentence_bounds import is_agreement, leans_back, resolve_units, unit_index_for

LEAD = timeline.CLIP_START_LEAD_IN_SECONDS
PAD = timeline.CLIP_END_PADDING_SECONDS


def _sidecar(tmp_path, sentences, language="en", name="video"):
    """A word sidecar from [(speaker, "A sentence.", start seconds, seconds per word), ...]."""
    words = []
    for speaker, text, start, pace in sentences:
        for position, token in enumerate(text.split()):
            begin = start + position * pace
            words.append({
                "text": token, "start": int(round(begin * 1000)), "end": int(round((begin + pace * 0.8) * 1000)),
                "confidence": 0.99, "speaker": speaker,
            })
    video = tmp_path / f"{name}.mp4"
    video.with_suffix(".transcript_cache.json").write_text(
        json.dumps({"version": 2, "language_code": language, "utterances": [], "words": words}), encoding="utf-8")
    return video


def _spoken(video, bounds):
    index = unit_index_for(video)
    return " ".join(word.text for word in index.words if word.start >= bounds[0] - 0.01 and word.end <= bounds[1] + 0.01)


def _bounds(video, start, end, cap=60.0):
    found = sentence_bounds.sentence_bounds(video, start, end, cap)
    assert found is not None
    return found


# --- the opening -----------------------------------------------------------------


def test_a_label_inside_a_sentence_opens_on_that_sentence(tmp_path):
    # The analysis line began mid-sentence ("... de feria es muy común que ..."): 24-word lines are not sentences.
    video = _sidecar(tmp_path, [
        ("A", "Les voy a contar un truco muy simple.", 0.0, 0.4),
        ("A", "Cuando estás de feria es muy común que los puestos te pidan una entrada enorme.", 4.0, 0.4),
        ("A", "Por eso siempre llevo dos monedas.", 11.0, 0.4),
    ], language="es")
    text = _spoken(video, _bounds(video, 5.0, 13.0))
    assert text.startswith("Cuando estás de feria")
    assert text.endswith("dos monedas.")


def test_a_sentence_opening_in_the_labelled_second_wins_over_the_tail_before_it(tmp_path):
    video = _sidecar(tmp_path, [
        ("A", "And that was the end of it.", 0.0, 0.3),
        ("A", "Weeks ahead of the deadline we shipped it anyway.", 2.3, 0.35),
        ("A", "It cost us everything.", 6.0, 0.35),
    ])
    assert _spoken(video, _bounds(video, 2.0, 7.0)).startswith("Weeks ahead")


def test_a_speaker_label_one_word_late_does_not_split_the_sentence(tmp_path):
    # Diarization gave "What" to the previous voice: "What | do you think of pineapple?" is one sentence.
    video = _sidecar(tmp_path, [
        ("A", "That is nonsense.", 0.0, 0.4),
        ("A", "What", 2.0, 0.3),
        ("B", "do you think of pineapple?", 2.3, 0.3),
        ("B", "I think it is wrong.", 4.5, 0.35),
    ])
    bounds = _bounds(video, 2.0, 6.0)
    assert _spoken(video, bounds).startswith("What do you think")
    assert bounds[0] == pytest.approx(2.0 - LEAD)


def test_a_bare_agreement_is_skipped(tmp_path):
    video = _sidecar(tmp_path, [
        ("A", "So do you agree with that?", 0.0, 0.3),
        ("B", "Yeah, exactly.", 2.5, 0.3),
        ("B", "Most founders never talk to a single customer before they build.", 3.6, 0.35),
        ("B", "That is the whole problem.", 8.2, 0.35),
    ])
    assert _spoken(video, _bounds(video, 2.0, 10.0)).startswith("Most founders")


@pytest.mark.parametrize("language, context, leaning", [
    ("en", "We tried to buy the loaves in a shop.", "But nobody sold them in winter."),
    ("es", "Quisimos comprar las flores en una tienda.", "Pero nadie las vendía en otoño."),
    ("pt", "Tentamos comprar as flores numa loja.", "Mas ninguém vendia no outono."),
    ("it", "Volevamo comprare i fiori in un negozio.", "Ma nessuno li vendeva in autunno."),
    ("de", "Wir wollten die Blumen im Laden kaufen.", "Aber niemand verkaufte sie im Herbst."),
    ("pl", "Chcieliśmy kupić kwiaty w sklepie.", "Ale nikt ich nie sprzedawał jesienią."),
    ("fr", "Nous voulions acheter les fleurs en magasin.", "Mais personne ne les vendait en automne."),
])
def test_an_opening_that_leans_on_the_line_before_takes_it_in(tmp_path, language, context, leaning):
    follows = 3.0 + len(context.split()) * 0.35 + 0.3
    video = _sidecar(tmp_path, [
        ("A", "Intro.", 0.0, 0.4),
        ("A", context, 3.0, 0.35),
        ("A", leaning, follows, 0.35),
        ("A", "Fin.", 14.0, 0.4),
    ], language=language)
    assert leans_back(leaning, language)
    text = _spoken(video, _bounds(video, float(int(follows)), follows + 3.0))
    assert text.startswith(context.split()[0])
    assert text.endswith(leaning.split()[-1])


def test_context_that_needs_context_itself_is_not_taken_in(tmp_path):
    # "That's it." closes the thought before; a run of "And... So..." never reaches a line that stands alone.
    video = _sidecar(tmp_path, [
        ("A", "That's it.", 0.0, 0.3),
        ("A", "So here is the thing nobody tells you about saving.", 1.0, 0.3),
        ("A", "You spend first.", 5.0, 0.3),
    ])
    assert _spoken(video, _bounds(video, 1.0, 6.0)).startswith("So here is the thing")


def test_context_from_another_voice_or_after_a_long_silence_is_not_taken_in(tmp_path):
    video = _sidecar(tmp_path, [
        ("A", "I am your neighbour.", 0.0, 0.3),
        ("B", "So this whole evening was a patience test.", 2.0, 0.3),
        ("B", "She wanted to know.", 5.5, 0.3),
    ])
    assert _spoken(video, _bounds(video, 2.0, 7.0)).startswith("So this whole evening")
    video = _sidecar(tmp_path, [
        ("A", "The room goes quiet.", 0.0, 0.3),
        ("A", "So I gave him nine apples to start.", 6.0, 0.3),
        ("A", "He needed them.", 9.5, 0.3),
    ], name="gap")
    assert _spoken(video, _bounds(video, 6.0, 11.0)).startswith("So I gave him")


def test_a_question_is_context_for_its_answer_across_voices(tmp_path):
    video = _sidecar(tmp_path, [
        ("A", "Why do you believe in a strict routine?", 0.0, 0.3),
        ("B", "Because bread never comes out of ovens by chance.", 2.8, 0.3),
        ("B", "Somebody baked it.", 6.0, 0.3),
    ])
    assert _spoken(video, _bounds(video, 2.0, 7.0)).startswith("Why do you believe")


# --- the ending ------------------------------------------------------------------


def test_a_label_inside_a_sentence_finishes_it(tmp_path):
    video = _sidecar(tmp_path, [
        ("A", "We planted six hundred paper lanterns.", 0.0, 0.35),
        ("A", "Now we only need to warm the room and we can watch them glow.", 2.5, 0.35),
        ("A", "Next week we try something else.", 8.5, 0.35),
    ])
    bounds = _bounds(video, 0.0, 5.0)
    text = _spoken(video, bounds)
    assert text.endswith("watch them glow.")
    assert "Next week" not in text


def test_a_sentence_too_long_to_finish_is_left_out_whole(tmp_path):
    video = _sidecar(tmp_path, [
        ("A", "This is the point of the entire lesson.", 0.0, 0.35),
        ("A", "It changed everything for us.", 3.2, 0.35),
        ("A", " ".join(["and"] + ["word"] * 30) + ".", 5.4, 0.4),
    ])
    text = _spoken(video, _bounds(video, 0.0, 6.0))
    assert text.endswith("everything for us.")


def test_a_closing_question_takes_its_short_answer_in(tmp_path):
    video = _sidecar(tmp_path, [
        ("A", "They offered me a weekend shift.", 0.0, 0.35),
        ("A", "How did you end up here?", 2.6, 0.3),
        ("B", "Same road that brings in everyone else.", 4.6, 0.3),
        ("B", "Anyway, let us talk about the schedule.", 8.0, 0.3),
    ])
    text = _spoken(video, _bounds(video, 0.0, 4.0))
    assert text.endswith("in everyone else.")


def test_a_closing_line_that_only_announces_the_next_thought_is_dropped(tmp_path):
    video = _sidecar(tmp_path, [
        ("A", "We could not find the bakery anywhere in the village this winter.", 0.0, 0.35),
        ("A", "So we baked six hundred of them out of rye.", 4.6, 0.35),
        ("A", "But then the problem is the crust.", 8.5, 0.35),
        ("A", "They are too thick.", 11.5, 0.35),
    ])
    text = _spoken(video, _bounds(video, 0.0, 10.0))
    assert text.endswith("out of rye.")


def test_the_cap_ends_on_the_last_sentence_that_fits(tmp_path):
    sentences = [("A", f"Sentence number {n} has exactly seven words.", n * 4.0, 0.4) for n in range(20)]
    video = _sidecar(tmp_path, sentences)
    bounds = _bounds(video, 0.0, 79.0, cap=30.0)
    text = _spoken(video, bounds)
    assert bounds[1] - bounds[0] <= 30.05
    assert text.endswith("seven words.")
    assert text.count("Sentence number") == 7  # 7 x 4 s fit, the 8th would pass the cap


def test_context_goes_before_the_payoff_when_the_cap_is_tight(tmp_path):
    video = _sidecar(tmp_path, [
        ("A", "We tried the shop.", 0.0, 0.4),
        ("A", "But nobody sold them in winter.", 2.0, 0.4),
        ("A", "We baked them ourselves out of plain flour and it worked.", 5.0, 0.4),
    ])
    bounds = _bounds(video, 2.0, 9.0, cap=8.0)
    text = _spoken(video, bounds)
    assert text.startswith("But nobody") and text.endswith("it worked.")


# --- sparse punctuation, other scripts, action ---------------------------------------


def test_a_transcript_without_punctuation_cuts_on_pauses_and_voices_never_inside_a_run(tmp_path):
    video = _sidecar(tmp_path, [
        ("A", "Nein nein du siehst es nicht schau mal her", 0.0, 0.3),
        ("A", "Er war die ganze Woche in Lissabon", 4.5, 0.3),
        ("B", "Okay das muss man verstehen", 8.0, 0.3),
        ("A", "Dann wird es nach Hamburg gebracht und das dauert noch", 11.0, 0.9),
    ], language="de")
    index = unit_index_for(video)
    first, last = resolve_units(index, 5.0, 10.0, 60.0)
    starts = {round(unit.start, 2) for unit in index.units}
    ends = {round(unit.end, 2) for unit in index.units}
    assert round(index.unit(first).start, 2) in starts and round(index.unit(last).end, 2) in ends
    text = _spoken(video, (index.cut_in[first], index.cut_out[last]))
    assert text.startswith("Er war die ganze Woche")
    assert text.endswith("verstehen")


def test_sentence_ends_of_other_scripts_count(tmp_path):
    video = _sidecar(tmp_path, [
        ("A", "これは 最初の 文です。", 0.0, 0.5),
        ("A", "次の 文は ここで 終わります。", 2.0, 0.5),
        ("A", "三つ目の 文です。", 5.0, 0.5),
    ], language="ja")
    text = _spoken(video, _bounds(video, 2.0, 3.0))
    assert text.startswith("次の") and text.endswith("終わります。")


def test_a_pick_that_opens_and_closes_on_action_keeps_its_labels(tmp_path):
    # Gameplay with one line in the middle: the silence around it is the moment, not something to cut to.
    video = _sidecar(tmp_path, [("A", "No way he made that jump.", 12.0, 0.3)])
    bounds = _bounds(video, 0.0, 30.0)
    assert bounds == (0.0, 30.0)
    # A review that left both edges where they were stores them; the action around the line is still the clip.
    index = unit_index_for(video)
    edges = (index.unit(1).start, index.unit(1).end)
    assert sentence_bounds.sentence_bounds(video, 0.0, 30.0, 60.0, edges=edges) == (0.0, 30.0)


def test_every_cut_lands_between_words(tmp_path):
    video = _sidecar(tmp_path, [
        ("A", f"Fast talk number {n} goes on, and on, without a breath, okay?", n * 3.0, 0.25) for n in range(12)
    ])
    index = unit_index_for(video)
    for start in range(0, 30):
        found = sentence_bounds.sentence_bounds(video, float(start), float(start + 5), 60.0)
        assert found is not None
        for word in index.words:
            assert not (word.start + 0.005 < found[0] < word.end - 0.005), (start, word)
            assert not (word.start + 0.005 < found[1] < word.end - 0.005), (start, word)


# --- the pieces ------------------------------------------------------------------


@pytest.mark.parametrize("text, language, expected", [
    ("Yeah, exactly.", "en", True),
    ("Sí, claro.", "es", True),
    ("Ja, genau.", "de", True),
    ("Yes we shipped it on Friday.", "en", False),
    ("Claro que el mercado cambió mucho.", "es", False),
])
def test_agreements(text, language, expected):
    assert is_agreement(text, language) is expected


@pytest.mark.parametrize("text, language, expected", [
    ("So we moved the table.", "en", True),
    ("And there's a simple reason why.", "en", True),
    ("Um, but in the printed manual it is different.", "en", True),
    ("He made that point already.", "en", True),
    ("Entonces ni yo escuché lo que dijo.", "es", True),
    ("È stato il giorno più lungo.", "it", False),
    ("E poi abbiamo cambiato piano.", "it", True),
    ("Solo tres estrellas.", "es", False),
    ("Sophie changed the plan.", "en", False),
])
def test_openings_that_lean_back(text, language, expected):
    assert leans_back(text, language) is expected


def test_without_word_timings_the_word_level_snap_still_runs(tmp_path, monkeypatch):
    video = tmp_path / "plain.mp4"
    assert sentence_bounds.sentence_bounds(video, 1.0, 9.0, 60.0) is None
    resolved = render_ranges.resolve_render_ranges(
        video, {"start_time": "00:01", "end_time": "00:09", "text": "as picked"}, None, 60.0)
    assert resolved.keep_ranges == [(1.0, 9.0)]


def test_the_resolver_uses_sentence_bounds_and_does_not_extend_again(tmp_path, monkeypatch):
    video = _sidecar(tmp_path, [
        ("A", "Old topic ends here.", 0.0, 0.3),
        ("A", "Cuando", 2.0, 0.3),
    ] + [("A", "estás de feria los puestos piden entrada.", 2.3, 0.3),
         ("A", "Llevo dos monedas.", 5.0, 0.3),
         ("A", "Otra cosa distinta empieza aquí.", 6.2, 0.3)], language="es")
    monkeypatch.setattr(timeline, "get_config", lambda: type("C", (), {"hook_first_teaser": False})())
    resolved = render_ranges.resolve_render_ranges(
        video, {"start_time": "00:03", "end_time": "00:05", "text": ""}, None, 60.0)
    assert resolved.text.startswith("Cuando estás de feria")
    assert resolved.text.endswith("dos monedas.")
    assert resolved.bounds[1] == pytest.approx(resolved.keep_ranges[-1][1])


# --- cases from screening real clips (synthetic stand-ins) ---------------------------


def test_a_voice_change_inside_a_written_sentence_is_not_an_opening(tmp_path):
    # "Pero si no entra, | también aquí hay tres cartas": the voice label changes at the comma, half a second on.
    video = _sidecar(tmp_path, [
        ("A", "Yo dejo entrar.", 0.0, 0.3),
        ("A", "Pero si no entra,", 1.5, 0.3),
        ("B", "también aquí hay tres cartas más.", 3.3, 0.3),
        ("B", "Estas me gustan un poco.", 5.6, 0.3),
    ], language="es")
    text = _spoken(video, _bounds(video, 3.0, 7.0))
    # The sentence is whole (and, opening on "Pero", it takes in the line it follows).
    assert text.startswith("Yo dejo entrar. Pero si no entra, también aquí hay tres cartas más.")
    # A capitalised word (every German noun) straight after the change, without a breath, is no opening either.
    video = _sidecar(tmp_path, [
        ("A", "Das war knapp.", 0.0, 0.3),
        ("A", "Ach, sind die", 2.0, 0.3),
        ("B", "Kisten.", 2.9, 0.3),
        ("B", "Du stellst den Topf jetzt da drauf.", 3.6, 0.3),
    ], language="de", name="noun")
    assert _spoken(video, _bounds(video, 2.0, 6.0)).startswith("Ach, sind die Kisten.")


def test_speech_without_punctuation_or_capitals_opens_after_its_longest_silence(tmp_path):
    video = _sidecar(tmp_path, [
        ("A", "Erster Satz.", 0.0, 0.3),
        ("A", "und dann rollt mir das ding vor den fuss", 14.0, 0.3),
        ("A", "rechts hinten am tisch ist noch platz", 22.0, 0.3),
        ("A", "nein nein nicht anfangen hey ihr lauft los", 30.4, 0.3),
        ("A", "bitte warten warten ich komm gleich", 33.1, 0.3),
    ], language="de")
    bounds = _bounds(video, 30.0, 35.0)
    assert _spoken(video, bounds).startswith("nein nein nicht anfangen")


def test_a_short_reply_takes_its_question_in(tmp_path):
    video = _sidecar(tmp_path, [
        ("A", "Vamos con el primer objeto.", 0.0, 0.3),
        ("A", "¿Qué fruta tienes ahí?", 2.0, 0.3),
        ("B", "¿Ahora mismo?", 3.6, 0.3),
        ("B", "Un melón.", 4.5, 0.3),
        ("A", "¿Un melón grande o pequeño?", 5.6, 0.3),
        ("B", "El tamaño común de un melón de toda la vida.", 7.6, 0.3),
    ], language="es")
    text = _spoken(video, _bounds(video, 4.0, 11.0))
    assert text.startswith("¿Qué fruta tienes ahí? ¿Ahora mismo? Un melón.")
    # A full sentence after a question is its own opening.
    video = _sidecar(tmp_path, [
        ("A", "Did you like the movie?", 0.0, 0.3),
        ("A", "The studio spent two hundred million dollars on that ending alone.", 2.0, 0.3),
        ("A", "It shows.", 6.0, 0.3),
    ], name="statement")
    assert _spoken(video, _bounds(video, 2.0, 7.0)).startswith("The studio spent")


# --- the edge review: the model proposes, the code decides ---------------------------

from src.media.sentence_bounds import (  # noqa: E402
    apply_edge_review,
    edge_review_request,
    quietest_instant,
    units_at,
)

QUIZ = [
    ("A", "Vamos con el segundo objeto.", 0.0, 0.3),          # 1
    ("A", "¿Has pintado muchas ventanas en tu vida?", 2.0, 0.3),  # 2
    ("B", "Más de veinte, sí.", 5.0, 0.3),                  # 3
    ("B", "Las pintaba con una brocha y con mucha paciencia.", 6.6, 0.3),  # 4
    ("A", "¿Y nunca te cansaste?", 10.0, 0.3),                  # 5
    ("B", "Una vez.", 11.6, 0.3),                               # 6
    ("B", "Pero esa historia es para otro día.", 12.6, 0.3),    # 7
    ("A", "Ahora vamos a probar vuestra memoria.", 15.5, 0.3),  # 8
    ("A", "Primera pregunta.", 18.0, 0.3),                      # 9
]


def test_the_review_is_shown_the_sentences_around_both_edges(tmp_path):
    index = unit_index_for(_sidecar(tmp_path, QUIZ, language="es"))
    request = edge_review_request(index, 3, 5, clip=1)
    assert [s["id"] for s in request["opening"]] == [1, 2, 3, 4, 5]
    assert [s["id"] for s in request["closing"]] == [3, 4, 5, 6, 7, 8, 9]
    assert [s["in_clip"] for s in request["opening"]] == [False, False, True, True, True]
    assert request["opening"][1]["text"] == "¿Has pintado muchas ventanas en tu vida?"
    assert request["first"] == 3 and request["last"] == 5


def test_an_accepted_review_moves_both_edges_onto_sentences(tmp_path):
    index = unit_index_for(_sidecar(tmp_path, QUIZ, language="es"))
    # Open on the question the bare answer replies to; close on the answer to the closing question.
    assert apply_edge_review(index, 3, 5, opening_id=2, closing_id=6, min_seconds=5.0, max_seconds=60.0) == (2, 6)
    # Nothing wrong: unchanged ids stay unchanged.
    assert apply_edge_review(index, 3, 5, 3, 5, 5.0, 60.0) == (3, 5)


def test_a_review_cannot_move_an_edge_off_a_sentence_too_far_or_out_of_the_length_range(tmp_path):
    index = unit_index_for(_sidecar(tmp_path, QUIZ, language="es"))
    # Ids the clip was never shown (nine sentences back, nine ahead) are ignored.
    assert apply_edge_review(index, 8, 9, opening_id=1, closing_id=9, min_seconds=1.0, max_seconds=60.0) == (8, 9)
    # The longer clip would pass the cap: the closing move (the payoff) is kept, the opening move dropped.
    assert apply_edge_review(index, 3, 5, opening_id=2, closing_id=6, min_seconds=5.0, max_seconds=8.2) == (3, 6)
    # Neither fits: the resolved edges stand.
    assert apply_edge_review(index, 3, 5, opening_id=2, closing_id=7, min_seconds=5.0, max_seconds=6.5) == (3, 5)
    # A shortening that would leave less than the minimum is refused.
    assert apply_edge_review(index, 3, 7, opening_id=5, closing_id=5, min_seconds=8.0, max_seconds=60.0) == (3, 7)
    # An edge more than EDGE_MAX_SHIFT_SECONDS away is refused even when it is listed.
    slow = [("A", f"Sentence {n} takes a long while to say here.", n * 9.0, 0.9) for n in range(8)]
    wide = unit_index_for(_sidecar(tmp_path, slow, name="slow"))
    assert apply_edge_review(wide, 5, 6, opening_id=2, closing_id=6, min_seconds=1.0, max_seconds=120.0) == (5, 6)


def test_stored_review_edges_resolve_exactly_and_fall_back_when_the_transcript_changed(tmp_path):
    video = _sidecar(tmp_path, QUIZ, language="es")
    index = unit_index_for(video)
    edges = (index.unit(2).start, index.unit(6).end)
    assert units_at(index, *edges) == (2, 6)
    # The labels say 00:05-00:11 (the bare answer); the reviewed edges win.
    found = sentence_bounds.sentence_bounds(video, 5.0, 11.0, 60.0, edges=edges)
    text = _spoken(video, found)
    assert text.startswith("¿Has pintado muchas ventanas") and text.endswith("Una vez.")
    # Edges that match no sentence any more (a re-transcribed source): the labels are resolved as usual.
    assert units_at(index, 3.1, 9.4) is None
    fallback = sentence_bounds.sentence_bounds(video, 5.0, 11.0, 60.0, edges=(3.1, 9.4))
    assert fallback == sentence_bounds.sentence_bounds(video, 5.0, 11.0, 60.0)
    # Reviewed edges still respect the cap, on a sentence.
    capped = sentence_bounds.sentence_bounds(video, 5.0, 11.0, 6.0, edges=edges)
    assert capped[1] - capped[0] <= 6.05 and _spoken(video, capped).endswith("sí.")


def test_the_resolver_passes_stored_edges_through(tmp_path, monkeypatch):
    video = _sidecar(tmp_path, QUIZ, language="es")
    index = unit_index_for(video)
    monkeypatch.setattr(timeline, "get_config", lambda: type("C", (), {"hook_first_teaser": False})())
    segment = {"start_time": "00:05", "end_time": "00:11", "text": "",
               "edge_seconds": [index.unit(2).start, index.unit(6).end]}
    resolved = render_ranges.resolve_render_ranges(video, segment, None, 60.0)
    assert resolved.text.startswith("¿Has pintado muchas ventanas") and resolved.text.endswith("Una vez.")


def test_a_reply_to_a_question_two_seconds_earlier_still_takes_it_in(tmp_path):
    # The contestant thinks for a moment before "Más de veinte, sí.".
    video = _sidecar(tmp_path, QUIZ, language="es")
    assert _spoken(video, _bounds(video, 5.0, 9.0)).startswith("¿Has pintado muchas ventanas en tu vida? Más de")


def test_a_closing_question_takes_the_other_voices_reply_even_when_it_ends_on_a_tag_question(tmp_path):
    video = _sidecar(tmp_path, [
        ("C", "The judge heard plenty from you.", 0.0, 0.3),
        ("C", "Why can't you answer, huh?", 2.4, 0.3),
        ("H", "I was going to fetch the ladder, okay?", 4.2, 0.3),
        ("C", "Where is my ladder then?", 7.0, 0.3),
    ])
    assert _spoken(video, _bounds(video, 0.0, 4.0)).endswith("fetch the ladder, okay?")
    # The same voice asking on is not an answer.
    video = _sidecar(tmp_path, [
        ("C", "The judge heard plenty from you.", 0.0, 0.3),
        ("C", "Why can't you answer, huh?", 2.4, 0.3),
        ("C", "Did you lose your notes?", 4.2, 0.3),
    ], name="same")
    assert _spoken(video, _bounds(video, 0.0, 4.0)).endswith("answer, huh?")


def _tone_video(tmp_path, quiet_from, quiet_to, duration=3.0):
    """Audio that is loud except for one silent stretch, in a file ffmpeg can read."""
    import subprocess

    path = tmp_path / "tone.mp4"
    volume = f"volume='if(between(t,{quiet_from},{quiet_to}),0,1)':eval=frame"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
         "-f", "lavfi", "-i", f"color=c=black:s=64x36:r=10:d={duration}", "-af", volume,
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True)
    return path


def test_the_quietest_instant_is_found_between_two_loud_stretches(tmp_path):
    video = _tone_video(tmp_path, 1.50, 1.56)
    quiet = quietest_instant(video, 1.40, 1.65)
    assert quiet is not None and 1.49 <= quiet <= 1.57
    assert quietest_instant(video, 1.0, 1.01) is None           # too short to measure
    assert quietest_instant(tmp_path / "missing.mp4", 1.0, 1.3) is None


def test_run_on_speech_is_cut_at_the_quiet_instant_without_taking_a_neighbouring_word_in(tmp_path):
    # Three sentences without a breath between them. The transcript puts the boundaries at 2.00 and 4.00; the
    # only quiet instants are just inside the middle sentence, at 2.01-2.04 and 3.96-3.99.
    import subprocess

    video = tmp_path / "runon.mp4"
    volume = "volume='if(between(t,2.01,2.04)+between(t,3.96,3.99),0,1)':eval=frame"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=330:duration=6",
         "-f", "lavfi", "-i", "color=c=black:s=64x36:r=10:d=6", "-af", volume,
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video)],
        check=True, capture_output=True)
    words = []
    for sentence, start in (("One two three four.", 0.0), ("Five six seven eight.", 2.0), ("Nine ten eleven twelve.", 4.0)):
        for position, token in enumerate(sentence.split()):
            words.append({"text": token, "start": int((start + position * 0.5) * 1000),
                          "end": int((start + position * 0.5 + 0.5) * 1000), "confidence": 0.99, "speaker": "A"})
    video.with_suffix(".transcript_cache.json").write_text(
        json.dumps({"version": 2, "language_code": "en", "utterances": [], "words": words}), encoding="utf-8")
    cut_in, cut_out = sentence_bounds.sentence_bounds(video, 2.0, 4.0, 60.0)
    assert 2.0 < cut_in <= 2.05
    assert 3.95 <= cut_out < 4.0
    # The cut never reaches into the word before or after as the transcript times them: "four." and "Nine"
    # are not in the clip's text, so they are not in its first or last caption either.
    monkeypatch_config = type("C", (), {"hook_first_teaser": False})()
    original = timeline.get_config
    timeline.get_config = lambda: monkeypatch_config
    try:
        resolved = render_ranges.resolve_render_ranges(video, {"start_time": "00:02", "end_time": "00:04", "text": ""}, None, 60.0)
    finally:
        timeline.get_config = original
    assert resolved.text == "Five six seven eight."


# --- sound the transcript does not time -----------------------------------------


def _levels(*runs):
    """dB levels, one per 5 ms, from (seconds, level) runs."""
    return [level for seconds, level in runs for _ in range(int(round(seconds / sentence_bounds.QUIET_HOP_SECONDS)))]


def test_a_sustained_sound_after_a_dip_is_found_and_a_click_or_a_tail_is_not():
    from src.media.sentence_bounds import sound_onset

    # The last word's tail, a dip, then another voice for 300 ms: the dip is where the cut belongs.
    found = sound_onset(_levels((0.05, -28.0), (0.02, -40.0), (0.30, -8.0), (0.10, -45.0)), speech_level=-20.0)
    assert found is not None
    dip, start = found
    assert 10 <= dip <= 13 and start == 14
    # A click (40 ms) is not a word; nor is a breath well below the level of the speech.
    assert sound_onset(_levels((0.05, -28.0), (0.05, -50.0), (0.04, -10.0), (0.30, -50.0)), speech_level=-20.0) is None
    assert sound_onset(_levels((0.05, -28.0), (0.05, -55.0), (0.30, -40.0)), speech_level=-20.0) is None
    # A word that simply dies away never rises.
    assert sound_onset(_levels((0.05, -20.0), (0.10, -30.0), (0.20, -50.0)), speech_level=-20.0) is None


def _spoken_tone(tmp_path, loud, sentences, duration):
    """A tone that sounds only during ``loud`` (start, end) stretches, with a word sidecar for ``sentences``."""
    import subprocess

    video = tmp_path / "padding.mp4"
    gate = "+".join(f"between(t,{start},{end})" for start, end in loud)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=330:duration={duration}",
         "-f", "lavfi", "-i", f"color=c=black:s=64x36:r=10:d={duration}", "-af", f"volume='if({gate},1,0)':eval=frame",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video)],
        check=True, capture_output=True)
    words = []
    for sentence, start in sentences:
        for position, token in enumerate(sentence.split()):
            words.append({"text": token, "start": int((start + position * 0.5) * 1000),
                          "end": int((start + position * 0.5 + 0.5) * 1000), "confidence": 0.99, "speaker": "A"})
    video.with_suffix(".transcript_cache.json").write_text(
        json.dumps({"version": 2, "language_code": "en", "utterances": [], "words": words}), encoding="utf-8")
    return video


def test_a_word_the_transcript_times_late_is_kept_out_of_the_padding_after_the_last_word(tmp_path):
    # "...to get you." ends at 3.00. The other voice answers at 3.08, but the transcript times that word at
    # 5.00: the third of a second kept after the last word held it, and the clip ended on "Come".
    video = _spoken_tone(
        tmp_path, loud=[(1.0, 3.0), (3.08, 3.5), (5.0, 7.0)],
        sentences=[("One two three four.", 1.0), ("Five six seven eight.", 5.0)], duration=8.0)
    cut_in, cut_out = sentence_bounds.sentence_bounds(video, 1.0, 3.0, 60.0)
    assert 2.95 <= cut_out <= 3.09
    assert cut_in == pytest.approx(1.0 - LEAD, abs=0.02)          # silence before the first word: untouched


def test_the_tail_of_an_untimed_word_is_kept_out_of_the_lead_in_before_the_first_word(tmp_path):
    video = _spoken_tone(
        tmp_path, loud=[(1.0, 3.0), (4.55, 4.93), (5.0, 7.0)],
        sentences=[("One two three four.", 1.0), ("Five six seven eight.", 5.0)], duration=8.0)
    cut_in, cut_out = sentence_bounds.sentence_bounds(video, 5.0, 7.0, 60.0)
    assert 4.92 <= cut_in <= 5.0
    assert cut_out == pytest.approx(7.0 + PAD, abs=0.02)          # silence after the last word: untouched


def test_reviewed_edges_keep_untimed_speech_out_of_the_padding_too(tmp_path):
    # The same late-timed word as above, on a clip whose edges a review already settled: the stored edges
    # name the sentences, and the audio still decides how much is kept after the last word.
    video = _spoken_tone(
        tmp_path, loud=[(1.0, 3.0), (3.08, 3.5), (5.0, 7.0)],
        sentences=[("One two three four.", 1.0), ("Five six seven eight.", 5.0)], duration=8.0)
    index = unit_index_for(video)
    edges = (index.unit(1).start, index.unit(1).end)
    assert units_at(index, *edges) == (1, 1)
    cut_in, cut_out = sentence_bounds.sentence_bounds(video, 1.0, 3.0, 60.0, edges=edges)
    assert 2.95 <= cut_out <= 3.09
    assert cut_in == pytest.approx(1.0 - LEAD, abs=0.02)


# --- false starts -----------------------------------------------------------------


def test_a_clip_does_not_close_on_a_sentence_the_speaker_drops_and_starts_again(tmp_path):
    video = _sidecar(tmp_path, [
        ("A", "Today we try to bake six loaves in the morning.", 0.0, 0.4),
        ("A", "Shall we knead for a little while?", 5.0, 0.4),
        ("A", "As if it were the oven?", 8.4, 0.3),
        ("A", "As if it were the oven we have sixteen hours of heat already so let us go.", 11.0, 0.6),
    ])
    assert _spoken(video, _bounds(video, 0.0, 10.0)).endswith("a little while?")


def test_a_clip_does_not_open_on_a_false_start(tmp_path):
    video = _sidecar(tmp_path, [
        ("A", "That was the first part.", 0.0, 0.4),
        ("A", "The thing about.", 3.0, 0.3),
        ("A", "The thing about engines is that they need air.", 4.5, 0.3),
        ("A", "Without air nothing burns.", 8.0, 0.4),
    ])
    text = _spoken(video, _bounds(video, 3.0, 10.0))
    assert text.startswith("The thing about engines") and text.endswith("nothing burns.")


def test_a_reply_that_repeats_the_question_is_not_a_false_start(tmp_path):
    from src.media.sentence_bounds import is_false_start

    video = _sidecar(tmp_path, [
        ("A", "You did it?", 0.0, 0.4),
        ("B", "You did it without any help at all.", 2.0, 0.4),
        ("A", "Yes I did.", 6.0, 0.4),
        ("A", "Yes I did it alone.", 8.0, 0.4),
    ])
    index = unit_index_for(video)
    assert not is_false_start(index, 1)        # another voice
    assert is_false_start(index, 3)
    assert not is_false_start(index, 4)        # the last sentence


def test_a_line_that_restates_the_one_before_leans_back():
    assert leans_back("Es decir, la culpa es del horario de la tienda.", "es")
    assert leans_back("O sea que no vienes.", "es")
    assert leans_back("In other words, it failed.", "en")
    assert not leans_back("Es un problema del horario.", "es")
