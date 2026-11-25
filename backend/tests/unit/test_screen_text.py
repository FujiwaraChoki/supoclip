"""Words read off a frame (the OCR's output), and the reader's budget."""

from src.media.screen_text import FrameReader, ScreenWord, content_tokens, parse_tsv, text_lines, words_outside

HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"


def _row(left, top, width, height, conf, text, level=5):
    return f"{level}\t1\t1\t1\t1\t1\t{left}\t{top}\t{width}\t{height}\t{conf}\t{text}"


def test_tsv_rows_become_words_in_frame_fractions():
    tsv = "\n".join([
        HEADER,
        _row(0, 0, 1280, 720, -1, "", level=1),
        _row(128, 360, 256, 36, 96.5, "¿Por"),
        _row(400, 362, 128, 36, 91.0, "qué"),
        _row(640, 100, 64, 20, 31.0, "blurry"),        # below the confidence floor: texture
        _row(700, 100, 10, 20, 90.0, "|"),             # not a word
        _row(900, 100, 0, 20, 90.0, "zero"),           # no box
        "garbage line",
    ])
    words = parse_tsv(tsv, 1280, 720)
    assert [word.text for word in words] == ["¿Por", "qué"]
    assert words[0].left == 0.1 and words[0].right == 0.3
    assert words[0].top == 0.5 and words[0].bottom == 0.55


def test_words_group_into_lines_and_the_crop_loses_the_ones_outside():
    words = [
        ScreenWord("se", 0.40, 0.73, 0.43, 0.77, 90), ScreenWord("ponen", 0.44, 0.73, 0.52, 0.77, 90),
        ScreenWord("¿Por", 0.38, 0.68, 0.44, 0.72, 90), ScreenWord("qué", 0.45, 0.68, 0.50, 0.72, 90),
        ScreenWord("A)", 0.08, 0.83, 0.11, 0.87, 90), ScreenWord("la", 0.12, 0.83, 0.14, 0.87, 90),
        ScreenWord("luz", 0.15, 0.83, 0.19, 0.87, 90),
    ]
    lines = text_lines(words)
    assert [" ".join(word.text for word in line) for line in lines] == ["¿Por qué", "se ponen", "A) la luz"]
    lost = words_outside(words, 0.342, 0.658)
    assert [word.text for word in lost] == ["A)", "la", "luz"]
    assert content_tokens(words) == {"ponen", "por", "qué", "luz"}


def test_the_reader_reads_each_moment_once_and_stops_at_its_budget():
    calls = []

    def read(_path, moment):
        calls.append(moment)
        return [ScreenWord("word", 0.1, 0.1, 0.2, 0.2, 90)]

    reader = FrameReader("clip.mp4", budget=3, read=read)
    assert reader(1.0) and reader(1.04) and reader(0.96)       # the same frame, read once
    assert calls == [1.0]
    assert reader(2.0) and reader(3.0)
    assert reader(4.0) is None                                 # past the budget: unknown
    assert reader(2.0)                                         # already read: still known
    assert calls == [1.0, 2.0, 3.0]


def test_a_box_that_runs_from_one_reading_into_the_next_is_not_a_word(monkeypatch):
    # The frame is read three ways, stacked into one page. The OCR now and then draws one tall box across
    # two of them; placed in the frame it reached below its bottom edge and stretched a caption band to a
    # third of the picture, which is then too tall to be painted.
    from src.media import screen_text

    header = "level\tpage\tblock\tpar\tline\tword\tleft\ttop\twidth\theight\tconf\ttext"
    rows = [
        "5\t1\t1\t1\t1\t1\t500\t70\t200\t40\t92\tSubtitle",       # in the first reading
        "5\t1\t1\t1\t2\t1\t500\t242\t200\t40\t91\tSubtitle",      # the same word in the second: counted once
        "5\t1\t1\t1\t3\t1\t300\t150\t100\t130\t80\tHH",           # from the first reading into the second
    ]
    monkeypatch.setattr(screen_text, "_frame_png", lambda *_a: b"png")
    monkeypatch.setattr(screen_text, "_tesseract_tsv", lambda _png: "\n".join([header, *rows]))
    monkeypatch.setattr("src.media.ffmpeg.ffprobe_video_size", lambda _p: (1280, 720))
    words = screen_text.read_frame_words("clip.mp4", 3.0, region=(0.76, 1.0))
    # The read band is 1280 x 172, scaled to 1280 x 172: the page is three of them, 516 rows.
    assert [word.text for word in words] == ["Subtitle"]
    assert 0.76 <= words[0].top < words[0].bottom <= 1.0
