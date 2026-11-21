"""Hook title text hygiene: a number keeps the sign the speaker used, and one task's titles share one casing."""

from __future__ import annotations

import pytest

from src.ai import restore_number_signs, sanitize_hook_title, sentence_case_title

TRANSCRIPT = (
    "Honestly in like 90% of the time I feel like they're my mate. Genuinely 90% of the time. "
    "We went to Tokyo and Japan was great, I emailed five CEOs and they all said yes. It cost $40 for the meals. "
    "I went from cameras worth £4,000 to £40,000. It tastes like dirt, bro. My podcast sponsor became my board seat. "
    "You forgot Instagram but you remember Netflix. Eight corn dogs for 800 yen."
)


@pytest.mark.parametrize("title, expected", [
    ("90 of podcast guests become real friends", "90% of podcast guests become real friends"),
    ("40 got them three full meals in Japan", "$40 got them three full meals in Japan"),
    ("From 4,000 to 40,000 in cameras", "From £4,000 to £40,000 in cameras"),
    ("90% of podcast guests become real friends", "90% of podcast guests become real friends"),
    ("90 percent of guests become friends", "90 percent of guests become friends"),
    ("Eight corn dogs for 800 yen", "Eight corn dogs for 800 yen"),          # said without a sign
    ("The 7 habits nobody mentions", "The 7 habits nobody mentions"),        # not in the transcript: left alone
    ("90, then nothing", "90%, then nothing"),
])
def test_a_number_gets_back_only_the_sign_the_speaker_used(title, expected):
    assert restore_number_signs(title, TRANSCRIPT) == expected


def test_a_number_said_both_ways_is_left_alone():
    assert restore_number_signs("90 guests showed up", "90% of them. All 90 guests showed up.") == "90 guests showed up"
    assert restore_number_signs("90 guests", None) == "90 guests"
    assert restore_number_signs("1,000 reasons", "about 11,000% more") == "1,000 reasons"


@pytest.mark.parametrize("title, expected", [
    ("That Tastes Like Dirt, Bro", "That tastes like dirt, bro"),
    ("I Emailed 5 CEOs and They All Said Yes", "I emailed 5 CEOs and they all said yes"),
    ("Eight Corn Dogs for 800 Yen in Tokyo", "Eight corn dogs for 800 yen in Tokyo"),
    ("My Podcast Sponsor Became My Board Seat", "My podcast sponsor became my board seat"),
    # Already sentence case: names stay, nothing else moves.
    ("You forgot Instagram but remember Netflix", "You forgot Instagram but remember Netflix"),
    ("It just tastes like texture", "It just tastes like texture"),
    ("They guessed oyster. They were wrong", "They guessed oyster. They were wrong"),
    ("Why I Quit", "Why I Quit"),
    # One capital is not Title Case, and words the transcript does not have could be names: both are left alone.
    ("From £4,000 to £40,000 in Cameras", "From £4,000 to £40,000 in Cameras"),
    ("What I'd Tell My Younger Self", "What I'd Tell My Younger Self"),
])
def test_title_case_becomes_sentence_case_and_names_keep_their_capital(title, expected):
    assert sentence_case_title(title, TRANSCRIPT) == expected


def test_a_title_with_a_capital_the_transcript_cannot_explain_is_left_as_it_is():
    assert sentence_case_title("He moved to Lisbon last year", "") == "He moved to Lisbon last year"
    # Nothing to check the words against: "Lisbon" must not become "lisbon".
    assert sentence_case_title("He Moved To Lisbon Last Year", "") == "He Moved To Lisbon Last Year"
    assert sentence_case_title("He Moved To Lisbon Last Year", "so he moved to Lisbon last year") == "He moved to Lisbon last year"
    # A name nobody says in the clip.
    assert sentence_case_title("How Altman Lost The Board", "and that is how he lost the board") == "How Altman Lost The Board"


@pytest.mark.parametrize("title, transcript", [
    ("Die Regierung lässt Unternehmen im Stich", "die Regierung will unternehmen und lässt alle im Stich"),
    ("Warum Deutschlands Wirtschaft Probleme hat", "Deutschlands Wirtschaft hat große Probleme, warum eigentlich"),
])
def test_a_german_sentence_keeps_its_nouns(title, transcript):
    assert sentence_case_title(title, transcript) == title
    assert sanitize_hook_title(title, source_text=transcript) == title


def test_other_scripts_are_untouched():
    assert sentence_case_title("这是一个标题 没有大小写", "") == "这是一个标题 没有大小写"


NAMES_OPEN_SENTENCES = (
    "So here is the thing. Tesla did something nobody expected. Apple never saw it coming, "
    "and what they did next shocked everyone. That was the day everything changed."
)


@pytest.mark.parametrize("title, expected", [
    # A name the transcript only has at the start of a sentence is never seen in lower case: it stays.
    ("What Tesla Did Next Shocked Apple", "What Tesla did next shocked Apple"),
    ("What Tesla did next shocked Apple", "What Tesla did next shocked Apple"),
    ("Nobody Expected Tesla's Next Move", "Nobody Expected Tesla's Next Move"),   # "move" is not in the clip
    # Common words the transcript has in lower case still come down.
    ("The Day Everything Changed", "The day everything changed"),
    ("What They Did Next Shocked Everyone", "What they did next shocked everyone"),
])
def test_a_word_is_lowered_only_when_the_transcript_has_it_in_lower_case(title, expected):
    assert sentence_case_title(title, NAMES_OPEN_SENTENCES) == expected
    assert sanitize_hook_title(title, source_text=NAMES_OPEN_SENTENCES) == expected


def test_sanitize_applies_both_only_with_the_transcript():
    raw = '"90 of Podcast Guests Become Real Friends."'
    assert sanitize_hook_title(raw) == "90 of Podcast Guests Become Real Friends"          # callers without a transcript
    # The sign comes back; "guests" and "friends" are not in the transcript, so the casing is left alone.
    assert sanitize_hook_title(raw, source_text=TRANSCRIPT) == "90% of Podcast Guests Become Real Friends"
    assert sanitize_hook_title('"That Tastes Like Dirt, Bro."', source_text=TRANSCRIPT) == "That tastes like dirt, bro"
    assert sanitize_hook_title("", source_text=TRANSCRIPT) is None
