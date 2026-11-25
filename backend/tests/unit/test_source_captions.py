"""Generated captions must avoid captions burned into the source (#291)."""

import re

import cv2
import numpy as np

from src.media import captions
from src.media.source_captions import (
    aggregate_caption_bands,
    band_through_full_height_crop,
    caption_lines_in_frame,
)


def _dialogue_lines(content):
    return [line for line in content.splitlines() if line.startswith("Dialogue: 0,")]


def _build(tmp_path, monkeypatch, words, **kwargs):
    monkeypatch.setattr(captions, "emoji_rendering_supported", lambda: False)
    output = tmp_path / "captions.ass"
    assert captions.build_assemblyai_ass_subtitles(
        tmp_path / "missing.mp4", 0.0, 10.0, 1080, 1920, output,
        caption_template="default", caption_words=words, **kwargs,
    )
    return output.read_text(encoding="utf-8")


def _timed(texts):
    return [{"text": text, "start": i * 0.4, "end": i * 0.4 + 0.35} for i, text in enumerate(texts)]


def test_captions_move_above_a_burned_in_caption_band(tmp_path, monkeypatch):
    words = _timed(["real", "time,", "and", "it's"])
    clean = _build(tmp_path, monkeypatch, words)
    # MKBHD clip 1: the source's own captions sit at 0.75-0.79 of the height.
    moved = _build(tmp_path, monkeypatch, words, avoid_band=band_through_full_height_crop((0.7484, 0.7893)))

    assert "\\pos(540,1490)" in clean
    y = int(re.search(r"\\pos\(540,(\d+)\)", moved).group(1))
    text_height = int(captions.get_scaled_font_size(32, 1080) * 1.5)
    assert y + text_height // 2 < 0.7484 * 1920
    assert y > 1920 * captions.MIN_MOVED_CAPTION_CENTER


def test_band_that_does_not_collide_keeps_the_caption_in_place():
    assert captions.caption_center_clear_of_band(1490, 93, (0.10, 0.18), 1920) == 1490
    # No room above a high band: go below it instead.
    below = captions.caption_center_clear_of_band(900, 93, (0.30, 0.48), 1920)
    assert below > 0.48 * 1920


def _caption_frame(text, y=0.78, size=1.6):
    frame = np.full((1920, 1080, 3), 150, np.uint8)
    cv2.circle(frame, (540, 700), 260, (90, 120, 160), -1)  # a "face"
    cv2.rectangle(frame, (250, 1100), (830, 1700), (40, 60, 160), -1)  # a "shirt"
    scale = size * 1.5
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX, scale, 6)
    origin = ((1080 - tw) // 2, int(1920 * y))
    cv2.putText(frame, text, origin, cv2.FONT_HERSHEY_DUPLEX, scale, (0, 0, 0), 16, cv2.LINE_AA)
    cv2.putText(frame, text, origin, cv2.FONT_HERSHEY_DUPLEX, scale, (255, 255, 255), 6, cv2.LINE_AA)
    return frame


def test_detector_finds_a_burned_in_caption_band():
    texts = ["AND IT'S HONESTLY", "PICK UP THINGS", "TRIGGER IS FUNNY", "USB AND HDMI"]
    per_frame = [caption_lines_in_frame(_caption_frame(text)) for text in texts * 3]

    band = aggregate_caption_bands(per_frame)
    assert band is not None
    assert band[0] < 0.78 < band[1] + 0.03
    assert band[1] - band[0] < 0.1


def test_detector_ignores_clean_frames():
    plain = _caption_frame("")
    assert caption_lines_in_frame(plain) == []
    assert aggregate_caption_bands([[] for _ in range(12)]) is None
    # One title card is not a caption track.
    lines = [caption_lines_in_frame(_caption_frame("BREAKING"))] + [[] for _ in range(11)]
    assert aggregate_caption_bands(lines) is None


def test_band_follows_the_ken_burns_zoom_toward_its_focus_point():
    # zoompan keeps y = (ih - ih/zoom) * 0.35 fixed, so a source row y lands at
    # zoom * y - 0.35 * (zoom - 1) at the end of the 5% punch-in.
    top, bottom = band_through_full_height_crop((0.7484, 0.7893))
    assert top == 0.7484
    assert abs(bottom - (1.05 * 0.7893 - 0.35 * 0.05)) < 1e-9


# --- reading the source's text and deciding what to do with it (letterbox / duplicate-caption fixes) ---------

import subprocess  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from src.media import source_captions  # noqa: E402
from src.media.source_captions import (  # noqa: E402
    SourceCaptions,
    band_in_output,
    captions_from_frames,
    cropped_intervals,
    mask_box,
    mask_caption_band,
    mask_filter,
    cut_intervals,
    plan_caption_action,
    presence_intervals,
    sample_moments,
    text_energy,
)


def _line(text, top=0.74, bottom=0.78, left=0.25, right=0.75):
    tokens = [token.lower().strip(".,?!") for token in text.split() if len(token.strip(".,?!")) >= 3]
    return {"top": top, "bottom": bottom, "left": left, "right": right, "tokens": tokens, "words": len(text.split())}


def _said(text):
    return [token.lower().strip(".,?!") for token in text.split()]


def test_small_plain_subtitles_that_say_the_speech_are_speech_captions():
    # A reposted Short: lowercase, no outline, the same words as the audio. The shape test never saw these.
    lines = ["like this blue paper crane", "what could we try", "so we agreed", "rice-paper sheet",
             "fold this for many times", "now we have a paper crane"]
    frames = [[_line(text)] for text in lines] + [[], []]
    spoken = [_said(text + " and some more words") for text in lines] + [[], []]
    found = captions_from_frames(frames, spoken)
    assert found is not None and found.kind == "speech"
    assert found.band[0] == pytest.approx(0.74) and found.band[1] == pytest.approx(0.78)
    assert found.coverage == pytest.approx(6 / 8)
    assert found.match == pytest.approx(1.0)


def test_ocr_slips_still_match_the_speech():
    frames = [[_line("se cuelgan cintas ver")], [_line("los patjos pequenos")], [_line("para evitar tropiezos")]]
    spoken = [_said("se cuelgan cintas verdes"), _said("los patios patjos pequeños pequenos"), _said("evitar tropiezos")]
    found = captions_from_frames(frames, spoken)
    assert found is not None and found.kind == "speech"


def test_subtitles_in_another_language_are_text_not_speech():
    german = ["Also willst du mich abholen", "Das ist ja wunderbar gewesen", "Ich habe vierzig Seiten gelesen",
              "Die Brote sind riesig"]
    frames = [[_line(text, top=0.88, bottom=0.93)] for text in german]
    spoken = [_said("are you going to fetch me"), _said("that is wonderful"), _said("forty fifty pages"),
              _said("the loaves are huge")]
    found = captions_from_frames(frames, spoken)
    assert found is not None and found.kind == "text"


def test_a_label_that_never_changes_is_static():
    frames = [[_line("Breaking news tonight", top=0.9, bottom=0.94)] for _ in range(6)]
    found = captions_from_frames(frames, [_said("completely different words here")] * 6)
    assert found is not None and found.kind == "static"


def test_captions_for_one_of_two_voices_are_found_though_they_come_and_go():
    # Only the caller on the phone is subtitled: 3 of 12 speech moments show a line.
    shown = [[_line("Bueno si es breve", top=0.86, bottom=0.94, left=0.1, right=0.9)],
             [_line("Es que traigo algo", top=0.86, bottom=0.94, left=0.2, right=0.8)],
             [_line("Que si me quiere ayudar", top=0.86, bottom=0.94, left=0.15, right=0.85)]]
    frames = [shown[0], [], [], [], shown[1], [], [], [], shown[2], [], [], []]
    said = {0: "bueno si es breve que estoy cocinando", 4: "es que traigo algo que mostrarle",
            8: "le digo que si me quiere ayudar"}
    spoken = [_said(said.get(index, "eso lo dice la otra voz")) for index in range(12)]
    found = captions_from_frames(frames, spoken, moments=[float(i) for i in range(12)])
    assert found is not None and found.kind == "speech"
    # The same lines when they are not what is being said are still subtitles (a translation, a game's own):
    # sentences centred at the bottom, changing. A crop would slice them, so they count as paintable.
    other = captions_from_frames(frames, [_said("nothing of this is written")] * 12)
    assert other is not None and other.kind == "text" and other.subtitle_like and other.paintable == (other,)
    # A prompt off to one side in a few frames (a game's hint, a notification) is not captions at all.
    prompt = [[_line(text, top=0.86, bottom=0.94, left=0.02, right=0.3)] if lines else []
              for lines, text in zip(frames, ["Pulsa X para entrar"] * 12)]
    assert captions_from_frames(prompt, [_said("nothing of this is written")] * 12) is None
    assert found.coverage == pytest.approx(0.25)
    assert found.shown == (0.0, 4.0, 8.0) and len(found.absent) == 9


def test_stray_marks_and_texture_are_not_captions():
    junk = {"top": 0.5, "bottom": 0.52, "left": 0.9, "right": 0.95, "tokens": ["oop"], "words": 1}
    assert captions_from_frames([[junk], [], [], [junk], [], [], [], []], [[]] * 8) is None
    assert captions_from_frames([[], [], []], [[], [], []]) is None
    # Single unmatched tokens at one height in many frames (a logo, a scoreboard digit) are not caption lines.
    assert captions_from_frames([[junk]] * 8, [_said("nothing alike")] * 8) is None


def test_outlined_caption_rows_the_ocr_cannot_read_still_make_a_band():
    row = {"top": 0.7, "bottom": 0.76, "left": 0.25, "right": 0.75, "tokens": [], "words": 0, "shape": True}
    found = captions_from_frames([[row]] * 5 + [[]] * 3, [[]] * 8)
    assert found is not None and found.kind == "text"


def test_frames_are_sampled_while_somebody_speaks():
    words = [{"text": "w", "start": 20.0 + i * 0.5, "end": 20.3 + i * 0.5} for i in range(40)]
    moments = sample_moments(60.0, words, count=8)
    assert len(moments) == 8 and all(20.0 <= moment <= 40.0 for moment in moments)
    assert sample_moments(12.0, [], count=4) == pytest.approx([1.5, 4.5, 7.5, 10.5])
    assert sample_moments(0.0, words) == []


SPEECH = SourceCaptions(band=(0.74, 0.78), left=0.2, right=0.8, kind="speech", coverage=0.9, match=0.9)


@pytest.mark.parametrize("fmt, size, layout, expected", [
    ("original", (1920, 1080), None, []),
    ("vertical_fit", (1920, 1080), None, []),
    ("vertical", (1080, 1920), {"kind": "crop"}, []),                     # a Short is not cropped sideways
    ("vertical", (1920, 1080), {"kind": "fit"}, []),
    ("vertical", (1920, 1080), {"kind": "crop"}, [(0.0, 30.0)]),
    ("vertical", (1920, 1080), {"kind": "interview"}, [(0.0, 30.0)]),
    ("vertical", (1920, 1080), {"kind": "gaming"}, [(0.0, 30.0)]),        # the game panel crops the width too
    ("vertical", (1920, 1080), {"kind": "reaction"}, [(0.0, 30.0)]),
    ("vertical", (1920, 1080), None, None),
    ("vertical_pan", (1920, 1080), None, [(0.0, 30.0)]),
    ("vertical", (1920, 1080), {"kind": "compositor", "interview_segments": [
        {"start": 0.0, "end": 10.0, "kind": "single"}, {"start": 10.0, "end": 18.0, "kind": "fit"},
        {"start": 18.0, "end": 30.0, "kind": "single"}]}, [(0.0, 10.0), (18.0, 30.0)]),
])
def test_where_the_layout_crops_the_source(fmt, size, layout, expected):
    assert cropped_intervals(fmt, size[0], size[1], 30.0, layout) == expected


@pytest.mark.parametrize("captions, cropped, expected", [
    (None, [], "none"),
    (SPEECH, [], "suppress"),                                              # whole captions that say the speech
    (SPEECH, [(0.0, 30.0)], "mask"),                                       # cut by the crop: one clean layer
    (SPEECH, [(0.0, 5.0)], "suppress"),                                    # cropped for a moment only
    (SPEECH, None, "avoid"),
    (SourceCaptions((0.7, 0.8), 0.2, 0.8, "speech", coverage=0.4, match=0.9), [], "avoid"),   # half the clip
    (SourceCaptions((0.7, 0.8), 0.2, 0.8, "text", coverage=0.9, match=0.0), [], "avoid"),     # a translation
    (SourceCaptions((0.7, 0.8), 0.2, 0.8, "text", coverage=0.9, match=0.0), [(0.0, 30.0)], "mask"),
    (SourceCaptions((0.7, 0.8), 0.2, 0.8, "static", coverage=1.0, match=0.0), [(0.0, 30.0)], "avoid"),
])
def test_what_the_render_does_with_source_captions(captions, cropped, expected):
    assert plan_caption_action(captions, cropped, 30.0) == expected


def test_the_mask_covers_the_band_and_only_the_cropped_stretches():
    # The whole width of the band: a line longer than any that was read would stick out of a narrower box,
    # cut off and legible.
    assert mask_box(SPEECH, 1280, 720) == (1, 518, 1278, 58)
    # Filled from the box's edges, then softened: a smooth patch, no streaks with glyph shapes in them.
    assert mask_filter(SPEECH, 1280, 720, [(0.0, 30.0)], 30.0) == (
        "delogo=x=1:y=518:w=1278:h=58,split[whole][patch];[patch]crop=1278:58:1:518,gblur=sigma=8[soft];"
        "[whole][soft]overlay=1:518")
    gated = mask_filter(SPEECH, 1280, 720, [(0.0, 10.0), (18.0, 30.0)], 30.0)
    when = ":enable='between(t,0.000,10.000)+between(t,18.000,30.000)'"
    assert gated == (
        f"delogo=x=1:y=518:w=1278:h=58{when},split[whole][patch];[patch]crop=1278:58:1:518,gblur=sigma=8[soft];"
        f"[whole][soft]overlay=1:518{when}")
    assert mask_filter(SPEECH, 1280, 720, [], 30.0) is None


def test_text_presence_follows_the_frames_that_were_read():
    times = [i * 0.2 + 0.1 for i in range(50)]                       # 10 s at 5 fps
    energies = [0.12 if 2.0 <= t < 4.0 or 7.0 <= t < 8.0 else 0.01 for t in times]
    found = presence_intervals(times, energies, shown=[3.0, 7.5], absent=[1.0, 5.0, 9.0], duration=10.0)
    assert found is not None and len(found) == 2
    assert found[0][0] == pytest.approx(1.8, abs=0.11) and found[0][1] == pytest.approx(4.2, abs=0.11)
    assert found[1][0] == pytest.approx(6.8, abs=0.11) and found[1][1] == pytest.approx(8.2, abs=0.11)
    # Nothing separates the two sets (a busy shirt under the box): unknown, the whole stretch is painted.
    assert presence_intervals(times, [0.05] * 50, shown=[3.0], absent=[1.0], duration=10.0) is None
    assert presence_intervals(times, energies, shown=[3.0], absent=[], duration=10.0) is None


def test_where_the_band_lands_in_the_output():
    assert band_in_output(SPEECH, "original", 1920, 1080, None) == SPEECH.band
    top, bottom = band_in_output(SPEECH, "vertical", 1920, 1080, "fit")
    assert top == pytest.approx(0.5 + 0.24 * 607.5 / 1920) and bottom == pytest.approx(0.5 + 0.28 * 607.5 / 1920)
    assert band_in_output(SPEECH, "vertical", 1080, 1920, "crop") == band_through_full_height_crop(SPEECH.band)
    assert band_in_output(SPEECH, "vertical", 1920, 1080, "interview") is None


def test_masking_removes_the_lines_and_leaves_the_rest_of_the_picture(tmp_path):
    # A grey picture with a block of hard vertical stripes (stand-in for letters) in the band for the first 2 s.
    video = tmp_path / "subtitled.mp4"
    stripes = "".join(f",drawbox=x={420 + 16 * i}:y=540:w=6:h=28:color=white:t=fill:enable='lt(t,2)'" for i in range(28))
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x606060:s=1280x720:r=10:d=4",
         "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "4",
         "-vf", "drawbox=x=100:y=100:w=200:h=200:color=red:t=fill" + stripes,
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video)],
        check=True, capture_output=True)
    captions = SourceCaptions(band=(0.75, 0.79), left=0.3, right=0.7, kind="speech", coverage=0.5, match=1.0,
                              shown=(0.5, 1.5), absent=(2.5, 3.5))

    def band_energy(seconds):
        capture = cv2.VideoCapture(str(video))
        capture.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
        ok, frame = capture.read()
        capture.release()
        assert ok
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return text_energy(gray[540:568, 400:880]), frame[150:250, 150:250].mean(axis=(0, 1))

    before, red_before = band_energy(1.0)
    assert before > 0.05
    assert mask_caption_band(video, captions, [(0.0, 4.0)]) is True
    after, red_after = band_energy(1.0)
    assert after < before * 0.1
    assert np.allclose(red_before, red_after, atol=12)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, check=True)
    assert sorted(probe.stdout.split()) == ["audio", "video"]


def test_analysis_reads_frames_at_speech_moments_and_takes_a_second_look(tmp_path, monkeypatch):
    from src.media.screen_text import ScreenWord

    video = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=320x568:r=5:d=16",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True, capture_output=True)
    words = [{"text": f"word{i}", "start": i * 0.5, "end": i * 0.5 + 0.4} for i in range(32)]
    reads = []

    def read(_path, moment, region):
        reads.append((round(moment, 2), region))
        spoken_now = f"word{int(moment / 0.5)}"
        wide = region[1] - region[0] > 0.5
        # The half-frame read misses the small text in part of the clip; the enlarged look at the band finds it.
        if wide and (4.0 <= moment < 8.0 or 12.0 <= moment < 14.0):
            return []
        return [ScreenWord(spoken_now, 0.3, 0.74, 0.5, 0.78, 95.0), ScreenWord("here", 0.52, 0.74, 0.7, 0.78, 95.0)]

    found = source_captions.analyze_source_captions(video, words, read=read)
    assert found is not None and found.kind == "speech"
    assert found.coverage == pytest.approx(1.0)
    assert any(region[1] - region[0] < 0.2 for _moment, region in reads)   # the second, enlarged look
    assert all(0.0 <= moment <= 16.0 for moment, _region in reads)


def test_a_clip_without_any_text_costs_only_the_first_look(tmp_path):
    video = tmp_path / "clean.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=320x180:r=5:d=20",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True, capture_output=True)
    reads = []
    assert source_captions.analyze_source_captions(
        video, [], read=lambda _p, moment, _r: reads.append(moment) or []) is None
    assert len(reads) == source_captions.FIRST_LOOK_SAMPLES


def test_captions_that_are_almost_always_there_are_painted_throughout(tmp_path, monkeypatch):
    # The presence test can miss a faint line; with captions in most of the clip a missed line would show cut
    # off, so the whole cropped stretch is painted. Only captions that come and go are gated by presence.
    calls = []
    monkeypatch.setattr(source_captions, "caption_presence", lambda *_a: calls.append(1) or [(0.0, 1.0)])
    commands = []

    def run(command):
        commands.append(command)
        Path(command[-1]).write_bytes(b"masked")
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr("src.media.ffmpeg.run_ffmpeg_command", run)
    monkeypatch.setattr("src.media.ffmpeg.ffprobe_video_size", lambda _p: (1280, 720))
    monkeypatch.setattr("src.media.ffmpeg.ffprobe_duration", lambda _p: 30.0)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"source")
    always = SourceCaptions(band=(0.8, 0.9), left=0.2, right=0.8, kind="speech", coverage=0.9, match=0.9,
                            shown=(1.0,), absent=(2.0,))
    assert mask_caption_band(video, always, [(0.0, 30.0)]) is True
    assert calls == [] and "enable" not in commands[-1][commands[-1].index("-filter_complex") + 1]
    sometimes = SourceCaptions(band=(0.8, 0.9), left=0.2, right=0.8, kind="speech", coverage=0.3, match=0.9,
                               shown=(1.0,), absent=(2.0,))
    assert mask_caption_band(video, sometimes, [(0.0, 30.0)]) is True
    assert calls == [1] and "between(t,0.000,1.000)" in commands[-1][commands[-1].index("-filter_complex") + 1]


def test_captions_at_the_top_and_the_bottom_are_two_bands_and_both_are_painted(tmp_path, monkeypatch):
    # A creator's captions jump: over the cartoon they sit at the top, over footage at the bottom.
    top = [[_line(text, top=0.10, bottom=0.16)] for text in
           ["carrying a box on his cart", "does what most of us might", "landlord is moving it"]]
    low = [[_line(text, top=0.82, bottom=0.88)] for text in
           ["go greet the neighbours first", "you open a single lesson", "and see who is around", "they all have recipes"]]
    frames = top + low + [[]]
    spoken = [_said(frame[0]["tokens"] and " ".join(frame[0]["tokens"]) or "") for frame in top + low] + [[]]
    found = captions_from_frames(frames, spoken, moments=[float(i) for i in range(8)])
    assert found is not None and len(found.bands) == 2
    assert {round(band.band[0], 2) for band in found.bands} == {0.10, 0.82}
    assert all(band.kind == "speech" for band in found.bands)
    assert len(found.paintable) == 2
    # Text at the top that is not the speech is a title or a logo, not captions.
    title = [[_line("Episode Twelve Finale", top=0.08, bottom=0.14)] for _ in range(8)]
    assert captions_from_frames(title, [_said("nothing alike here")] * 8) is None


def test_a_label_or_a_document_the_crop_cuts_is_left_in_the_picture():
    # A checkout screen in the middle of the frame, read in every sample, never spoken: it is what
    # the video shows. Painting it out would destroy the shot.
    screen = [[_line(text, top=0.42, bottom=0.50, left=0.3, right=0.7)] for text in
              ["1.200,00 total amount due", "service rate 0.3500 applied", "incluye 9,50 de envio", "aceptar pedido ahora"]]
    found = captions_from_frames(screen * 2, [_said("miren lo que pide la tienda")] * 8)
    assert found is not None and found.kind == "text" and not found.subtitle_like
    assert found.paintable == ()
    assert plan_caption_action(found, [(0.0, 30.0)], 30.0) == "avoid"


def test_heavy_display_fonts_the_ocr_garbles_still_match_the_speech():
    frames = [[_line("SOMETHING FEELS DIEEERENT", top=0.84, bottom=0.9)], [_line("EVEN TOUCHAS THE", top=0.84, bottom=0.9)],
              [_line("NO JIGNGER WORKS", top=0.84, bottom=0.9)]]
    spoken = [_said("you sense something feels different"), _said("hardly even touches the ceiling"),
              _said("that no longer works at all")]
    found = captions_from_frames(frames, spoken)
    assert found is not None and found.kind == "speech"


def test_words_in_the_middle_of_the_picture_stay_unless_they_are_a_caption_track(tmp_path, monkeypatch):
    # A motion-graphics explainer sets its key words beside what it shows, in the middle of the frame, as the
    # narrator says them. They come and go with the points being made. Painting them was tried twice: it
    # blurs a third of the picture. They are cut by the crop like the rest of it, and ours move clear.
    texts = ["read the rules first", "the board counts you", "your card your ticket", "up as labels under"]
    middle = [frame for text in texts for frame in ([_line(text, top=0.38, bottom=0.50)], [])]
    spoken = [_said(text) for text in texts for _ in range(2)]
    found = captions_from_frames(middle, spoken)
    assert found is not None and found.kind == "speech" and found.coverage == 0.5
    assert not found.in_caption_zone and found.paintable == ()
    assert plan_caption_action(found, [(0.0, 30.0)], 30.0) == "avoid"
    # A block taller than a few lines is a page or a card, not a caption, wherever it is.
    tall = SourceCaptions(band=(0.70, 0.95), left=0.2, right=0.8, kind="speech", coverage=0.9, match=0.9)
    assert tall.paintable == ()
    # Strips at the bottom and at the top are painted, up to three of them: a creator's captions jump.
    low = SourceCaptions(band=(0.80, 0.90), left=0.2, right=0.8, kind="speech", coverage=0.9, match=0.9)
    high = SourceCaptions(band=(0.09, 0.21), left=0.2, right=0.8, kind="speech", coverage=0.5, match=0.8)
    late = SourceCaptions(band=(0.92, 0.98), left=0.2, right=0.8, kind="speech", coverage=0.2, match=1.0)
    both = SourceCaptions(**{**low.__dict__, "more": (high, tall, found, late)})
    assert both.paintable == (both, high, late)
    assert plan_caption_action(both, [(0.0, 30.0)], 30.0) == "mask"

    # A caption track for everything that is said, set at chest height, is captions: it is up in nearly
    # every frame, which key words are not. Outside the caption strips a patch is never always there: it is
    # timed by the text, and when that cannot be told only the moments the words were read are painted.
    track = SourceCaptions(band=(0.45, 0.55), left=0.1, right=0.9, kind="speech", coverage=0.95, match=0.95,
                           shown=(1.0, 7.0), absent=(4.0,))
    assert track.paintable == (track,) and plan_caption_action(track, [(0.0, 30.0)], 30.0) == "mask"
    commands = []

    def run(command):
        commands.append(command)
        Path(command[-1]).write_bytes(b"masked")
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr("src.media.ffmpeg.run_ffmpeg_command", run)
    monkeypatch.setattr("src.media.ffmpeg.ffprobe_video_size", lambda _p: (1280, 720))
    monkeypatch.setattr("src.media.ffmpeg.ffprobe_duration", lambda _p: 30.0)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"source")
    monkeypatch.setattr(source_captions, "caption_presence", lambda *_a: [(4.0, 8.0)])
    assert mask_caption_band(video, track, [(0.0, 30.0)]) is True
    assert "between(t,4.000,8.000)" in commands[-1][commands[-1].index("-filter_complex") + 1]
    monkeypatch.setattr(source_captions, "caption_presence", lambda *_a: None)
    assert mask_caption_band(video, track, [(0.0, 30.0)]) is True
    graph = commands[-1][commands[-1].index("-filter_complex") + 1]
    assert "between(t,0.400,1.600)" in graph and "between(t,6.400,7.600)" in graph and "between(t,3." not in graph


def test_captions_shown_only_on_stressed_phrases_are_found_from_two_frames():
    # Big captions pop up for a couple of words now and then: 2 of 16 sampled frames, both saying the speech.
    frames = [[] for _ in range(16)]
    frames[13] = [_line("even touches the", top=0.84, bottom=0.93)]
    frames[15] = [_line("has in stock", top=0.84, bottom=0.93)]
    spoken = [[] for _ in range(16)]
    spoken[13] = _said("hardly even touches the ceiling")
    spoken[15] = _said("what the bakery has in stock for us")
    found = captions_from_frames(frames, spoken, moments=[float(i) for i in range(16)])
    assert found is not None and found.kind == "speech" and found.coverage == pytest.approx(2 / 16)
    assert found.paintable == (found,)
    # One frame is a coincidence, and single words are not lines.
    frames[15] = []
    assert captions_from_frames(frames, spoken) is None


def test_a_subtitle_for_a_voice_the_transcript_missed_is_found_from_the_two_frames_it_is_up():
    # A big yellow line for somebody off-camera, on screen for a second. The scan for outlined rows put two
    # reads on it; it is not what the transcript says at that moment.
    frames = [[] for _ in range(18)]
    frames[9] = [_line("vaya mierda de nudos que han hecho", top=0.86, bottom=0.92, left=0.1, right=0.9)]
    frames[10] = [_line("vaya mierda de nudos han hecho", top=0.86, bottom=0.92, left=0.1, right=0.9)]
    found = captions_from_frames(frames, [_said("johnny te has quedado solo")] * 18, moments=[float(i) for i in range(18)])
    assert found is not None and found.kind == "text" and found.paintable == (found,)
    assert found.shown == (9.0, 10.0)
    # Off to one side it is a prompt, not a subtitle.
    frames[9] = [_line("vaya mierda de nudos que han hecho", top=0.86, bottom=0.92, left=0.02, right=0.42)]
    frames[10] = [_line("vaya mierda de nudos han hecho", top=0.86, bottom=0.92, left=0.02, right=0.42)]
    assert captions_from_frames(frames, [_said("johnny te has quedado solo")] * 18) is None


def test_a_crop_that_holds_the_lines_whole_does_not_cut_them():
    # A portrait picture set in a 16:9 frame: its captions are narrower than the 9:16 crop taken from the
    # middle. They were painted out, or doubled by ours. They are whole: ours are left off.
    narrow = SourceCaptions(band=(0.57, 0.67), left=0.37, right=0.63, kind="speech", coverage=0.94, match=0.94)
    centre = [(0.0, 30.0, 0.342, 0.658)]
    everywhere = [(0.0, 30.0)]
    assert cut_intervals(narrow, everywhere, centre) == []
    assert plan_caption_action(narrow, everywhere, 30.0, cut=[]) == "suppress"
    # The same lines under a crop that sits to one side are cut there, and only there.
    windows = [(0.0, 12.0, 0.342, 0.658), (12.0, 30.0, 0.10, 0.416)]
    assert cut_intervals(narrow, everywhere, windows) == [(12.0, 30.0)]
    assert plan_caption_action(narrow, everywhere, 30.0, cut=[(12.0, 30.0)]) == "mask"
    # Whole lines that are only there now and then (a creator captions a few phrases) cannot carry the clip:
    # left in, they sat under ours as a second layer. They are painted out like cut ones.
    sometimes = SourceCaptions(band=(0.94, 0.98), left=0.40, right=0.60, kind="speech", coverage=0.19, match=0.67)
    assert cut_intervals(sometimes, everywhere, centre) == []
    assert plan_caption_action(sometimes, everywhere, 30.0, cut=[]) == "mask"
    # Nothing is cropped (a Short): nothing is painted.
    assert plan_caption_action(sometimes, [], 30.0, cut=[]) == "avoid"
    # Lines written across the frame are cut by every crop; so is everything when the layout names no crops.
    assert cut_intervals(SPEECH, [(0.0, 30.0)], centre) == [(0.0, 30.0)]
    assert cut_intervals(narrow, [(0.0, 30.0)], None) == [(0.0, 30.0)]
    assert cut_intervals(narrow, None, centre) is None
    # A second band that would be painted counts; a label elsewhere in the picture does not.
    low = SourceCaptions(band=(0.80, 0.88), left=0.05, right=0.95, kind="speech", coverage=0.9, match=0.9)
    label = SourceCaptions(band=(0.45, 0.50), left=0.02, right=0.30, kind="static", coverage=1.0, match=0.0)
    assert cut_intervals(SourceCaptions(**{**narrow.__dict__, "more": (low,)}), [(0.0, 30.0)], centre) == [(0.0, 30.0)]
    assert cut_intervals(SourceCaptions(**{**narrow.__dict__, "more": (label,)}), [(0.0, 30.0)], centre) == []


def test_subtitle_lines_that_stay_up_for_seconds_are_not_a_label():
    # A game's subtitles, sampled twice a line, with the stream's own "Day 1" counter in the same rows. Two
    # neighbouring frames often show the same line; that made the band a static label, and the lines stayed,
    # cut off, under ours.
    lines = ["Can never have too many spare shelves", "Just picture him being all proud",
             "He has no clue what is next", "Thinking of taking a little detour"]
    frames = [
        [_line(text, top=0.88, bottom=0.92, left=0.28, right=0.72), _line("Day played", top=0.93, bottom=0.96, left=0.76, right=0.94)]
        for text in lines for _ in range(2)
    ]
    found = captions_from_frames(frames, [_said("why do they keep them in a shed")] * len(frames))
    assert found is not None and found.kind == "text"
    # The counter alone is a label.
    alone = [[_line("Day played", top=0.93, bottom=0.96, left=0.76, right=0.94)] for _ in range(8)]
    assert captions_from_frames(alone, [_said("why do they keep them in a shed")] * 8).kind == "static"


def test_the_second_line_of_a_two_line_subtitle_is_part_of_the_band():
    # A game's subtitles are one line most of the time and two now and then, the extra one above. The band
    # was the usual row alone, so the upper line stayed in the picture, legible, over the painted strip.
    one = ["Hey are you waving at someone", "Yeah at you my friend", "In a tiny little cabin",
           "Next to the fountain over there", "He is waving at someone", "Nobody is in here"]
    frames = [[_line(text, top=0.885, bottom=0.914, left=0.33, right=0.67)] for text in one]
    frames[0].append(_line("They put a little shine on them", top=0.833, bottom=0.861, left=0.30, right=0.70))
    found = captions_from_frames(frames, [_said(text) for text in one])
    assert found is not None and found.kind == "speech"
    assert found.band[0] == pytest.approx(0.833) and found.band[1] >= 0.91
    assert found.left == pytest.approx(0.30) and found.right == pytest.approx(0.70)
    # Text further up is something else, and a band never grows past a few lines.
    frames[1].append(_line("Mission objective updated now", top=0.60, bottom=0.64))
    frames[2].append(_line("A third line far above it", top=0.72, bottom=0.76))
    assert captions_from_frames(frames, [_said(text) for text in one]).band[0] == pytest.approx(0.833)


def test_a_line_that_was_read_is_painted_whatever_its_edge_energy_says():
    # A short line over a busy picture measured below the level for half a second, between two lines that
    # measured above it: it showed, cut off, under ours. The moments it was read count as text.
    times = [i * 0.2 + 0.1 for i in range(50)]                       # 10 s at 5 fps
    energies = [0.12 if 2.0 <= t < 4.0 or 4.6 <= t < 6.0 else 0.01 for t in times]
    read = [2.2, 2.6, 3.0, 3.4, 4.3, 5.0, 5.4, 5.8, 8.8]
    found = presence_intervals(times, energies, shown=read, absent=[1.0, 7.4], duration=10.0)
    assert found is not None
    assert any(start <= 4.3 <= end for start, end in found)          # the weak line, bridged
    assert any(start <= 8.8 <= end for start, end in found)          # a read moment with no energy at all
    assert not any(start <= 7.4 <= end for start, end in found)      # an empty stretch stays unpainted


def test_how_far_the_lines_reach_is_taken_from_the_lines_that_were_read():
    # Rows found by their shape alone have no measured width. Their nominal one made narrow captions look
    # wider than the crop that held them, so they were painted out instead of kept.
    texts = ["are you a gardener still", "she bakes bread on sundays", "proper cooks do it that way", "then it is settled"]
    frames = [[_line(text, top=0.66, bottom=0.72, left=0.37, right=0.63)] for text in texts]
    frames[1].append({"top": 0.60, "bottom": 0.64, "left": 0.25, "right": 0.75, "tokens": [], "words": 0, "shape": True})
    found = captions_from_frames(frames, [_said(text) for text in texts])
    assert found is not None and (found.left, found.right) == (0.37, 0.63)
    # With nothing read, the nominal width stands.
    shapes = [[{"top": 0.80, "bottom": 0.86, "left": 0.25, "right": 0.75, "tokens": [], "words": 0, "shape": True}]
              for _ in range(6)]
    only = captions_from_frames(shapes, [[]] * 6)
    assert only is not None and (only.left, only.right) == (0.25, 0.75)


def test_a_subtitle_that_is_up_for_under_a_second_is_found_by_watching_the_bottom_strip():
    from src.media.source_captions import STRIP_FPS, bright_edge_energy, strip_jumps

    # Light glyphs with dark outlines over a patterned jacket: edges alone do not tell them apart, edges
    # next to bright pixels do.
    rng = np.random.default_rng(4)
    jacket = rng.integers(60, 150, size=(50, 380), dtype=np.uint8)
    captioned = jacket.copy()
    cv2.putText(captioned, "Unas 80 o mas?", (90, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0,), 5, cv2.LINE_AA)
    cv2.putText(captioned, "Unas 80 o mas?", (90, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255,), 2, cv2.LINE_AA)
    assert bright_edge_energy(jacket) < 0.01 < 0.05 < bright_edge_energy(captioned)
    # The strip is quiet, a line comes up for three frames at 3 s, a longer one at 7 s: the stronger jump
    # first, three looks at each (one look would not make a band).
    energies = [0.008] * 40
    energies[12:15] = [0.16, 0.17, 0.16]
    energies[28:36] = [0.12] * 8
    moments = strip_jumps(energies, duration=10.0)
    assert len(moments) == 6                                     # three looks at each
    assert all(12.5 / STRIP_FPS <= moment <= 14.5 / STRIP_FPS for moment in moments[:3])
    assert all(28.5 / STRIP_FPS <= moment <= 35.5 / STRIP_FPS for moment in moments[3:])
    assert strip_jumps([0.2] * 40, duration=10.0) == []          # a strip that is always busy says nothing


def test_written_words_match_the_speech_without_accents_and_numbers_by_how_they_start():
    # The OCR reads "¿Unas 80?" as "Tengo unas 8072" and "unas 807": the same line twice, said as "Tengo unas 80, creo."
    spoken = [_said("Bastantes. Tengo unas 80, creo.")] * 2
    frames = [[_line("Tengo unas 8072", top=0.89, bottom=0.94, left=0.40, right=0.60)],
              [_line("unas 807", top=0.89, bottom=0.94, left=0.46, right=0.60)]]
    found = captions_from_frames(frames + [[]] * 12, spoken + [_said("otra cosa distinta")] * 12, moments=[3.0, 3.4] + [10.0 + i for i in range(12)])
    assert found is not None and found.kind == "speech" and found.paintable == (found,)
    assert found.shown == (3.0, 3.4)


def test_a_subtitle_that_shares_its_rows_with_a_score_bar_is_painted_only_where_it_was_read(tmp_path, monkeypatch):
    # A quiz show: a subtitle for one line at 3 s, and at other times a score bar in the same rows. Read
    # together they were "text that is mostly not the speech", so the subtitle stayed, cut off, under ours.
    said = _said("Bastantes. Tengo unas 80, creo.")
    other = _said("el otro grupo va primero ahora")
    frames = [[_line("Tengo unas 80", top=0.90, bottom=0.94)], [_line("Tengo unas 80", top=0.90, bottom=0.94)]]
    frames += [[_line("Rojos 2 Azules 3", top=0.90, bottom=0.95)] for _ in range(5)] + [[] for _ in range(9)]
    moments = [3.0, 3.3, 17.0, 19.0, 34.0, 41.0, 43.0] + [50.0 + step for step in range(9)]
    found = captions_from_frames(frames, [said, said] + [other] * 14, moments=moments)
    assert found is not None and found.kind == "speech" and found.mixed
    assert found.shown == (3.0, 3.3) and found.paintable == (found,)
    # It cannot carry the clip, whatever share of the frames the rows hold text in.
    assert plan_caption_action(found, [], 60.0, cut=[]) == "avoid"
    assert plan_caption_action(found, [(0.0, 60.0)], 60.0, cut=[]) == "mask"
    commands = []

    def run(command):
        commands.append(command)
        Path(command[-1]).write_bytes(b"masked")
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr("src.media.ffmpeg.run_ffmpeg_command", run)
    monkeypatch.setattr("src.media.ffmpeg.ffprobe_video_size", lambda _p: (1280, 720))
    monkeypatch.setattr("src.media.ffmpeg.ffprobe_duration", lambda _p: 60.0)
    monkeypatch.setattr(source_captions, "caption_presence", lambda *_a: [(0.0, 60.0)])     # the score bar is text too
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"source")
    assert mask_caption_band(video, found, [(0.0, 60.0)]) is True
    graph = commands[-1][commands[-1].index("-filter_complex") + 1]
    assert graph.count("between(") == 2 and graph.count("between(t,2.400,3.900)") == 2    # the box and its soft patch


def test_a_misread_taller_than_any_caption_line_is_not_a_line(monkeypatch):
    from src.media.screen_text import ScreenWord

    def read(_video, _moment, _region=None):
        return [ScreenWord("Unas", 0.40, 0.90, 0.47, 0.94, 90.0), ScreenWord("de", 0.48, 0.90, 0.52, 0.94, 90.0),
                ScreenWord("Hll", 0.20, 0.60, 0.30, 0.95, 70.0)]

    lines = source_captions._frame_lines("clip.mp4", None, 3.0, read)
    assert [line["tokens"] for line in lines] == [["unas"]]


def test_a_games_subtitles_stay_one_band_painted_throughout_though_the_streamer_reads_a_line_or_two():
    # A game's own subtitles are up in most frames and are not the streamer's speech, except where the
    # streamer reads a line aloud. Those two frames made the whole band "captions where they were read", and
    # the subtitles showed under ours for the rest of the clip.
    lines = ["Can never have too many shelves", "Just picture him being all proud", "He has no clue what is next",
             "Thinking of taking a little detour", "We could walk to the lake", "Solar powered lanterns over here",
             "That is a heavy load", "Nobody told me about this"]
    frames = [[_line(text, top=0.88, bottom=0.92, left=0.28, right=0.72)] for text in lines]
    spoken = [_said("why do they keep them in a shed")] * len(lines)
    spoken[2] = _said("he has no clue what is next")
    spoken[5] = _said("solar powered lanterns over here")
    found = captions_from_frames(frames, spoken, moments=[float(3 * step) for step in range(len(lines))])
    assert found is not None and found.kind == "text" and not found.mixed
    assert found.coverage == 1.0 and found.paintable == (found,)
