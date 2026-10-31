import unicodedata

import pytest

from src.workflows.language import (
    CLAUSE_MARK_RE,
    detect_script_language,
    ends_question,
    ends_sentence,
    is_no_space,
    language_name,
    normalize_language_code,
    opens_with_back_reference,
    part_label,
)
from src.workflows.titles import compose_title, strip_numbering


def test_compose_title_with_a_part_prefix():
    assert compose_title("The bank calls back", prefix="Part 2") == "Part 2: The bank calls back"
    assert compose_title("The bank calls back") == "The bank calls back"


@pytest.mark.parametrize(
    "raw",
    [
        "Part 3: The bank calls back",
        "part 3 - The bank calls back",
        "Teil 3 – The bank calls back",
        "#3 The bank calls back",
        "Episode 3 of 5: The bank calls back",
        "Part 3/5 | The bank calls back",
        "The bank calls back (Part 3)",
        "The bank calls back – Part 3/5",
        "第3集：The bank calls back",
        "第3部分 The bank calls back",
        "パート3：The bank calls back",
        "3부: The bank calls back",
    ],
)
def test_model_numbering_is_stripped(raw):
    assert compose_title(raw, prefix="Part 2") == "Part 2: The bank calls back"


def test_list_numbers_go_under_a_part_label_and_spanish_del_stays():
    assert compose_title("1. The beginning", prefix="Part 1") == "Part 1: The beginning"
    assert compose_title("10. Mai: Der Tag") == "10. Mai: Der Tag"
    assert compose_title("Del 1 al 10: el ranking", prefix="Parte 1") == "Parte 1: Del 1 al 10: el ranking"
    assert compose_title("Del 2: Banken ringer igen", prefix="Del 2") == "Del 2: Banken ringer igen"


def test_numbering_inside_a_title_is_kept():
    assert strip_numbering("Why part 2 of the plan failed") == "Why part 2 of the plan failed"
    assert strip_numbering("3 habits that changed my art") == "3 habits that changed my art"


def test_budget_shrinks_by_the_prefix():
    title = "A really long title that keeps going and going way past every budget we have here"
    composed = compose_title(title, prefix="Part 10")
    assert composed.startswith("Part 10: ")
    assert len(composed) <= 64
    assert len(composed.split()) <= 10
    # A trim never leaves a dangling "and" or "the".
    assert not composed.endswith((" and", " the", " of"))
    assert composed == "Part 10: A really long title that keeps going"


def test_word_budget_counts_the_prefix_words():
    title = "one two three four five six seven eight nine ten"
    assert compose_title(title, prefix="Part 2") == "Part 2: one two three four five six seven eight"
    assert compose_title(title) == title


def test_sanitizing_matches_highlights():
    assert compose_title('"Why we almost lost everything?"') == "Why we almost lost everything?"
    assert compose_title("Big news #startup #money.") == "Big news"
    assert compose_title("   ") is None
    assert compose_title("", prefix="Part 4") == "Part 4"
    assert compose_title("Part 4", prefix="Part 4") == "Part 4"


def test_localized_part_labels():
    assert part_label("en", 2) == "Part 2"
    assert part_label("de", 3) == "Teil 3"
    assert part_label("es", 4) == "Parte 4"
    assert part_label("fr", 5) == "Partie 5"
    assert part_label("zh", 3) == "第3部分"
    assert part_label("ja", 2) == "パート2"
    assert part_label("ko", 2) == "2부"
    assert part_label("hi", 2) == "भाग 2"
    assert part_label("ar", 2) == "الجزء 2"
    assert part_label("pt_BR", 2) == "Parte 2"
    assert part_label(None, 7) == "Part 7"
    assert part_label("xx", 7) == "Part 7"
    assert compose_title("Die Bank ruft zurück", prefix=part_label("de", 2)) == "Teil 2: Die Bank ruft zurück"


def test_no_space_titles_trim_by_character_and_use_a_full_width_colon():
    title = "银行打来电话的那天晚上发生的一切以及第二天更令人震惊的事情和后来的故事"
    composed = compose_title(title, prefix=part_label("zh", 2))
    assert composed.startswith("第2部分：银行打来电话")
    # Full-width characters take two columns: about 32 fit in the budget.
    assert len(composed) < 40
    assert compose_title("银行打来电话", prefix=part_label("zh", 2)) == "第2部分：银行打来电话"
    # A natural pause late in the cut is preferred.
    paused = compose_title("银行打来电话的那天晚上发生的一切以及第二天，更令人震惊的事情和后来的故事", prefix="第2部分")
    assert paused.endswith("第二天")


def test_trimming_never_orphans_a_combining_mark():
    thai = "ธนาคารโทรกลับมาและทุกอย่างก็เปลี่ยนไปตลอดกาลจนไม่มีใครเชื่อเลยว่าจะเกิดขึ้นได้จริงๆ"
    composed = compose_title(thai, prefix=part_label("th", 2))
    assert composed.startswith("ตอนที่ 2：")
    assert unicodedata.category(composed[len("ตอนที่ 2："):][0])[0] != "M"
    assert len(composed) <= 64


def test_language_names_and_codes():
    assert language_name("de") == "German"
    assert language_name("en_us") == "English"
    assert language_name(None) == "the language the speakers mainly use"
    assert normalize_language_code("iw") == "he"
    assert normalize_language_code("zh-Hant") == "zh"
    assert normalize_language_code("") is None


def test_script_detection():
    assert detect_script_language("我们昨天去了银行") == "zh"
    assert detect_script_language("銀行に行きました") == "ja"
    assert detect_script_language("은행에 갔어요") == "ko"
    assert detect_script_language("मुझे नहीं पता") == "hi"
    assert detect_script_language("ذهبنا إلى البنك") == "ar"
    assert detect_script_language("ہم بینک گئے تھے") == "ur"
    assert detect_script_language("We went to the bank") is None
    assert detect_script_language("Мы пошли в банк") is None
    assert is_no_space("ja", "")
    assert is_no_space(None, "我们昨天去了银行 ok")
    assert not is_no_space("ko", "은행에 갔어요")
    assert not is_no_space(None, "plain english")


def test_back_references():
    assert opens_with_back_reference("That's why we left.", "en")
    assert opens_with_back_reference("Um, he said no.", "en_us")
    assert opens_with_back_reference("And that changed everything.", "en")
    assert opens_with_back_reference("“As I said, the bank...”", "en")
    assert not opens_with_back_reference("Hello there, everyone.", "en")
    assert not opens_with_back_reference("Thermal paste matters.", "en")
    assert opens_with_back_reference("Deshalb sind wir gegangen.", "de")
    assert opens_with_back_reference("Wie gesagt, die Bank.", "de")
    assert opens_with_back_reference("Por eso nos fuimos.", "es")
    assert opens_with_back_reference("Comme je disais, la banque.", "fr")
    assert opens_with_back_reference("C’est pour ça qu'on est partis.", "fr")
    assert not opens_with_back_reference("That's why we left.", "ja")
    assert not opens_with_back_reference("That's why we left.", None)


def test_sentence_end_detection():
    assert ends_sentence("done.", "Next")
    assert ends_sentence("done?”", "Next")
    assert ends_sentence("完了。", "次")
    assert ends_sentence("है।", "फिर")
    assert ends_sentence("end.", None)
    assert not ends_sentence("Dr.", "Smith")
    assert ends_sentence("Dr.", "Smith", gap=1.0)
    assert not ends_sentence("U.S.", "economy")
    assert not ends_sentence("so...", "maybe")
    assert ends_sentence("so...", "Maybe")
    assert not ends_sentence("3.", "Oktober", language="de")
    assert ends_sentence("2019.", "Dann", language="de")
    assert not ends_sentence("well,", "then")
    # "No." and "I." are words that end sentences, not abbreviations or initials.
    assert ends_sentence("No.", "We")
    assert ends_sentence("no.", "Then")
    assert not ends_sentence("No.", "5")
    assert not ends_sentence("Nr.", "12", language="de")
    assert ends_sentence("I.", "Then")
    assert not ends_sentence("J.", "Doe")
    # Greek asks with ";" (U+037E or ASCII); elsewhere ";" only marks a clause.
    assert ends_sentence("γιατί;", "Και", language="el") and ends_question("γιατί;", "el")
    assert ends_question("γιατί\u037e")
    assert not ends_sentence("first;", "then") and not ends_question("first;", "en")
    # Arabic and Ethiopic commas are clause marks.
    assert CLAUSE_MARK_RE.search("قال،") and CLAUSE_MARK_RE.search("ሰላም፣")
