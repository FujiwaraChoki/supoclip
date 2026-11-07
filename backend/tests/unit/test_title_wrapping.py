"""Burned-in titles stay inside the frame, in every script."""

from src.media.captions import _title_lines, _title_width, build_hook_title_ass, build_point_label_ass

CHINESE = "\u539f\u6765\u6211\u4eec\u575a\u6301\u7684\u77ac\u95f4\u771f\u7684\u80fd\u591f\u6210\u4e3a\u522b\u4eba\u5b9e\u4e60\u7684\u52c7\u6c14"


def _font_px(style_line):
    return int(style_line.split(",")[2])


def test_english_titles_wrap_as_before():
    assert _title_width("Heaven") == 6
    assert _title_lines("Heaven", 20) == ["Heaven"]
    assert _title_lines("A really very long compound word", 12) == ["A really very", "long compound word"]


def test_chinese_title_breaks_into_two_balanced_lines():
    first, second = _title_lines(CHINESE, 26)
    assert first + second == CHINESE
    assert abs(len(first) - len(second)) <= 1


def test_chinese_hook_title_fits_the_frame():
    style, events = build_hook_title_ass(CHINESE, {}, 1080, 1920, 6, "Arial", 62)
    assert "\\N" in events[0]
    # Each line is 11-12 characters at about one em each, inside the frame's width.
    assert _font_px(style) * 12 <= 1080


def test_short_chinese_title_stays_on_one_line():
    _, events = build_hook_title_ass(CHINESE[:6], {}, 1080, 1920, 6, "Arial", 62)
    assert "\\N" not in events[0]


def test_chinese_point_label_wraps():
    _, events = build_point_label_ass(
        [{"text": CHINESE, "start": 5.0}], {}, 1080, 1920, 20.0, "Arial", 62, hook_end=4.0
    )
    assert "\\N" in events[0]
