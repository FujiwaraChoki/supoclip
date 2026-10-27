import json
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.editor_document import (
    EditDocument,
    RevisionConflict,
    editor_dir,
    mapped_words,
    output_size,
    render_document,
    save_document,
    write_ass,
    caption_key,
)


def document(**changes):
    return EditDocument.model_validate(
        {
            "segments": [
                {"id": "a", "start": 0.25, "end": 1.25},
                {"id": "b", "start": 1.5, "end": 2},
            ],
            "words": [
                {"id": "w", "start": 0.5, "end": 1, "text": "Hello", "highlight": True}
            ],
            **changes,
        }
    )


def test_split_word_is_one_caption_across_contiguous_segments_including_overlaps():
    doc = document(segments=[{"id": "one", "start": 0, "end": .5}, {"id": "two", "start": .5, "end": 1}], words=[
        {"id": "a", "start": 0, "end": 1, "text": "blue", "highlight": True},
        {"id": "b", "start": .2, "end": .8, "text": "light"},
    ])
    before = doc.model_dump_json()
    assert mapped_words(doc) == [word.model_dump() for word in doc.words]
    assert doc.model_dump_json() == before
    repeated = doc.model_copy(update={"segments": [doc.segments[0].model_copy(update={"end": 1}), doc.segments[1].model_copy(update={"start": 0})]})
    assert [w["text"] for w in mapped_words(repeated)] == ["blue", "light", "blue", "light"]


def test_caption_keys_distinguish_occurrences_and_survive_a_contiguous_split():
    doc = document(segments=[{"id": "s", "start": 0, "end": 1}, {"id": "repeat", "start": 0, "end": 1}])
    words = mapped_words(doc)
    assert caption_key(doc, [words[0]]) == '["s","w"]'
    assert caption_key(doc, [words[1]]) == '["repeat","w"]'
    split = document(segments=[{"id": "s", "start": 0, "end": .75}, {"id": "tail", "start": .75, "end": 1}])
    assert caption_key(split, mapped_words(split)) == '["s","w"]'


def test_combining_rebases_individual_placements_to_new_word_ids():
    from src.services.editor_service import rebased_caption_transforms
    doc = document(captionTransforms={'["a","w"]': {"x": .1, "y": -.2, "scale": 1.5}})
    assert rebased_caption_transforms(doc, [{"id": "fresh"}]) == {
        '["combined","fresh"]': {"x": .1, "y": -.2, "scale": 1.5, "size": None},
    }


@pytest.mark.parametrize("template,karaoke", [("minimal", False), ("hormozi", True)])
def test_ass_applies_a_placement_only_to_its_caption(tmp_path, template, karaoke):
    doc = document(segments=[{"id": "s", "start": 0, "end": 2}], words=[
        {"id": "a", "start": 0, "end": 1, "text": "Same"},
        {"id": "b", "start": 1, "end": 2, "text": "Same"},
    ], captions={"wordsPerLine": 1, "template": template, "karaoke": karaoke},
        captionTransforms={'["s","a"]': {"x": .2, "y": -.2, "scale": 1.5}})
    path = tmp_path / "captions.ass"
    write_ass(path, doc, 1080, 1920)
    events = [line for line in path.read_text().splitlines() if line.startswith("Dialogue:")]
    assert "\\pos(756,1114)\\fs81" in events[0]
    assert "\\pos(540,1498)" in events[1]
    assert "\\fs81" not in events[1]


def test_revisions_protect_other_tabs_and_original(tmp_path):
    draft = document()
    saved = save_document(tmp_path, draft, 0, 2)
    assert saved["revision"] == 1
    with pytest.raises(RevisionConflict):
        save_document(tmp_path, document(muted=True), 0, 2)
    assert json.loads((tmp_path / "draft.json").read_text()) == saved
    assert save_document(tmp_path, document(muted=True), 1, 2)["revision"] == 2


@pytest.mark.parametrize(
    "change",
    [
        {"volume": float("nan")},
        {"volume": float("inf")},
        {"framing": {"x": -1}},
        {"captions": {"font": "../../secret"}},
        {"segments": []},
        {"segments": [{"id": "a", "start": 1, "end": 0.5}]},
        {"words": [{"id": "a", "start": 1, "end": 1, "text": "bad"}]},
        {"effects": {"blur": 1000}},
    ],
)
def test_rejects_unrenderable_edits(change):
    with pytest.raises(ValidationError):
        document(**change)


def test_source_bounds_and_duplicate_ids_are_validated():
    with pytest.raises(ValueError):
        document().validate_duration(1)
    with pytest.raises(ValueError):
        document(
            segments=[
                {"id": "a", "start": 0, "end": 1},
                {"id": "a", "start": 1, "end": 2},
            ]
        ).validate_duration(2)


def test_reordered_cuts_project_caption_timing():
    doc = document(
        segments=[
            {"id": "b", "start": 0.75, "end": 1.5},
            {"id": "a", "start": 0, "end": 0.75},
        ]
    )
    words = mapped_words(doc)
    assert [(w["start"], w["end"]) for w in words] == [(0, 0.25), (1.25, 1.5)]
    assert all(w["highlight"] for w in words)
    assert output_size(doc, 1920, 1080) == (1080, 1920)
    assert editor_dir("/tmp", "../task", "../clip", "../file").parent == Path(
        "/tmp/editor"
    )


def test_karaoke_captions_highlight_each_spoken_word(tmp_path):
    words = [
        {"id": "a", "start": 0.0, "end": 0.4, "text": "one", "highlight": False},
        {"id": "b", "start": 0.5, "end": 0.9, "text": "two", "highlight": False},
    ]
    segments = [{"id": "s", "start": 0, "end": 1}]
    path = tmp_path / "captions.ass"

    write_ass(path, document(segments=segments, words=words), 1080, 1920)
    assert path.read_text().count("Dialogue:") == 1

    karaoke = document(
        segments=segments,
        words=words,
        captions={"karaoke": True, "uppercase": True, "accent": "#00FF00"},
    )
    write_ass(path, karaoke, 1080, 1920)
    events = [line for line in path.read_text().splitlines() if line.startswith("Dialogue:")]
    assert len(events) == 2
    accent = "\\c&H0000FF00&}"
    assert f"{accent}ONE" in events[0] and f"{accent}TWO" not in events[0]
    assert f"{accent}TWO" in events[1] and f"{accent}ONE" not in events[1]
    assert events[0].startswith("Dialogue: 0,0:00:00.00,0:00:00.50,")


def test_edited_captions_keep_the_template_animation(tmp_path):
    words = [
        {"id": "a", "start": 0.0, "end": 0.4, "text": "one", "highlight": False},
        {"id": "b", "start": 0.5, "end": 0.9, "text": "two", "highlight": False},
    ]
    segments = [{"id": "s", "start": 0, "end": 1}]
    path = tmp_path / "captions.ass"

    # "minimal" fades each line in and out rather than highlighting words.
    write_ass(
        path,
        document(segments=segments, words=words, captions={"template": "minimal"}),
        1080,
        1920,
    )
    events = [line for line in path.read_text().splitlines() if line.startswith("Dialogue:")]
    assert len(events) == 1 and "\\fad(120,120)" in events[0]

    # "hormozi" is karaoke with a pill behind the spoken word and a spring entrance.
    write_ass(
        path,
        document(
            segments=segments,
            words=words,
            captions={"template": "hormozi", "karaoke": True},
        ),
        1080,
        1920,
    )
    text = path.read_text()
    assert text.count("Dialogue:") == 2
    assert "\\fscx92" in text and "\\3c" in text


def test_export_keeps_the_hook_title_even_without_captions(tmp_path):
    path = tmp_path / "captions.ass"
    segments = [{"id": "a", "start": 0, "end": 1}, {"id": "b", "start": 5, "end": 6}]
    write_ass(
        path,
        document(segments=segments, captions={"enabled": False}, hookTitle="Stop doing this"),
        1080,
        1920,
    )
    events = [line for line in path.read_text().splitlines() if line.startswith("Dialogue:")]
    assert len(events) == 1 and ",Hook," in events[0] and "Stop" in events[0]
    # Timed against the edited 2s duration, not the last segment's 6s source end.
    assert events[0].startswith("Dialogue: 1,0:00:00.12,0:00:01.75,")


def test_export_follows_editor_highlights_position_and_wrapping(tmp_path):
    words = [
        {"id": "a", "start": 0.0, "end": 0.4, "text": "you", "highlight": False},
        {"id": "b", "start": 0.5, "end": 0.9, "text": "need", "highlight": False},
        {"id": "c", "start": 1.0, "end": 1.4, "text": "you", "highlight": True},
        {"id": "d", "start": 1.5, "end": 1.9, "text": "money", "highlight": False},
    ]
    path = tmp_path / "captions.ass"
    write_ass(
        path,
        document(
            segments=[{"id": "s", "start": 0, "end": 2}],
            words=words,
            captions={"template": "hormozi", "karaoke": False, "y": 0.9, "accent": "#00FF00"},
        ),
        1080,
        1920,
    )
    text = path.read_text()
    assert "WrapStyle: 0" in text
    # Centred exactly where the editor placed it, without the safe-area clamp.
    assert "\\pos(540,1728)" in text
    [event] = [line for line in text.splitlines() if line.startswith("Dialogue:")]
    # Hormozi accents emphasis in its own yellow; only the highlighted
    # occurrence gets it, with no automatic power words.
    emphasis = "\\c&H0000E0FF&}"
    assert event.count(emphasis) == 1 and f"{emphasis}you" in event
    assert f"{emphasis}money" not in event


def test_background_box_is_padded_and_uses_the_template_colour(tmp_path):
    path = tmp_path / "captions.ass"
    write_ass(
        path,
        document(captions={"template": "minimal", "background": True, "size": 60}),
        1080,
        1920,
    )
    style = next(line for line in path.read_text().splitlines() if line.startswith("Style: Default,"))
    fields = style.split(",")
    # OutlineColour fills the box: 50% black; BorderStyle 3 with real padding.
    assert fields[5] == "&H7F000000&" and fields[15] == "3" and int(fields[16]) >= 10


def test_captions_disabled_write_a_valid_empty_script(tmp_path):
    path = tmp_path / "captions.ass"
    write_ass(path, document(captions={"enabled": False}), 1080, 1920)
    assert "Dialogue:" not in path.read_text()
    assert "[Events]" in path.read_text()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
@pytest.mark.parametrize("audio", [True, False])
def test_real_render_preserves_cuts_dimensions_and_audio(tmp_path, audio):
    args = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=320x180:rate=24:duration=2",
    ]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio:
        args += ["-c:a", "aac"]
    subprocess.run(
        args + [str(tmp_path / "clean.mp4")], check=True, capture_output=True
    )
    doc = document(
        framing={"aspect": "original", "fit": "cover", "zoom": 1.2, "x": 0.2},
        muted=True,
    )
    render_document(tmp_path, "a" * 32, doc, "tiktok")
    output = tmp_path / f"export-{'a' * 32}.mp4"
    probe = json.loads(
        subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(output),
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (320, 180)
    assert float(probe["format"]["duration"]) == pytest.approx(1.5, abs=0.1)
    assert any(s["codec_type"] == "audio" for s in probe["streams"]) == audio
    if audio:
        import numpy as np

        pcm = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(output),
                "-f",
                "f32le",
                "-ac",
                "1",
                "-",
            ],
            capture_output=True,
            check=True,
        ).stdout
        assert float(np.max(np.abs(np.frombuffer(pcm, dtype="float32")))) < 0.001


def test_cancelled_render_does_not_publish_output(tmp_path):
    from src.editor_document import run_render

    job_id = "b" * 32
    (tmp_path / f"cancel-{job_id}").touch()
    with pytest.raises(InterruptedError):
        run_render(["sleep", "10"], tmp_path, job_id, 1)
    assert not (tmp_path / f"export-{job_id}.mp4").exists()


def test_run_render_adds_progress_flags_only_for_ffmpeg(tmp_path, monkeypatch):
    from src.editor_document import run_render

    seen = {}

    class Process:
        returncode = 0

        def poll(self):
            return 0

        def terminate(self):
            pass

        def kill(self):
            pass

        def wait(self, timeout=None):
            return 0

    def fake_popen(command, **kwargs):
        seen["command"] = command
        return Process()

    monkeypatch.setattr("src.editor_document.subprocess.Popen", fake_popen)
    job_id = "c" * 32
    run_render(["ffmpeg", "-i", "in.mp4", "out.mp4"], tmp_path, job_id, 1)
    progress = str(tmp_path / f"progress-{job_id}.txt")
    assert seen["command"][:4] == ["ffmpeg", "-progress", progress, "-nostats"]
    assert seen["command"][4:] == ["-i", "in.mp4", "out.mp4"]

    run_render(["sleep", "10"], tmp_path, job_id, 1)
    assert seen["command"] == ["sleep", "10"]


@pytest.mark.parametrize("layer", ["hook", "captions"])
def test_text_transforms_persist_and_reject_invalid_values(tmp_path, layer):
    draft = document(textTransforms={layer: {"x": .2, "y": -.1, "scale": 1.7}})
    payload = save_document(tmp_path, draft, 0, 2)
    assert payload["document"]["textTransforms"][layer] == {"x": .2, "y": -.1, "scale": 1.7}
    for value in (float("nan"), float("inf"), -1, 0, 4.1):
        with pytest.raises(ValidationError):
            document(textTransforms={layer: {"scale": value}})
    with pytest.raises(ValidationError):
        document(textTransforms={"unknown": {"scale": 1}})


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
@pytest.mark.parametrize("layer", ["hook", "captions"])
def test_export_moves_and_resizes_text_independently(tmp_path, layer):
    import numpy as np
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:s=640x360:r=24:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(tmp_path / "clean.mp4")], check=True)
    draft = document(
        segments=[{"id": "s", "start": 0, "end": 2}],
        words=[{"id": "w", "start": 0, "end": 2, "text": "CAPTION"}],
        hookTitle="TITLE" if layer == "hook" else "",
        captions={"enabled": layer == "captions", "template": "minimal", "background": False, "color": "#FFFFFF", "accent": "#FFFFFF"},
        framing={"aspect": "original"},
    )
    transform = {"x": .15, "y": .2 if layer == "hook" else -.2, "scale": 1.5}
    moved = EditDocument.model_validate({**draft.model_dump(), "textTransforms": {layer: transform}})
    def bounds(job):
        raw = subprocess.check_output(["ffmpeg", "-v", "error", "-ss", "0.5", "-i", str(tmp_path / f"export-{job}.mp4"), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
        pixels = np.frombuffer(raw, dtype=np.uint8).reshape(360, 640, 3)
        ys, xs = np.where(np.all(pixels > 180, axis=2))
        return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1) if len(xs) else None
    render_document(tmp_path, "base", draft, "tiktok")
    render_document(tmp_path, "moved", moved, "tiktok")
    a, b = bounds("base"), bounds("moved")
    assert a and b
    assert (b[0] + b[2] - a[0] - a[2]) / 2 == pytest.approx(640 * .15, abs=4)
    assert (b[1] + b[3] - a[1] - a[3]) / 2 == pytest.approx(360 * transform["y"], abs=5)
    assert (b[2] - b[0]) / (a[2] - a[0]) == pytest.approx(1.5, abs=.15)
    assert moved.words == draft.words and moved.captions == draft.captions

@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_export_changes_one_caption_and_leaves_the_next_caption_unchanged(tmp_path):
    import numpy as np
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:s=640x360:r=24:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(tmp_path / "clean.mp4")], check=True)
    original = document(segments=[{"id": "s", "start": 0, "end": 2}], words=[
        {"id": "a", "start": 0, "end": 1, "text": "CAPTION"},
        {"id": "b", "start": 1, "end": 2, "text": "CAPTION"},
    ], framing={"aspect": "original"}, captions={"wordsPerLine": 1, "template": "minimal", "background": False, "color": "#FFFFFF", "accent": "#FFFFFF"})
    changed = EditDocument.model_validate({**original.model_dump(), "captionTransforms": {'["s","a"]': {"x": .15, "y": -.2, "scale": 1.5}}})
    render_document(tmp_path, "original", original, "tiktok")
    render_document(tmp_path, "changed", changed, "tiktok")
    def pixels(job, time):
        raw = subprocess.check_output(["ffmpeg", "-v", "error", "-ss", str(time), "-i", str(tmp_path / f"export-{job}.mp4"), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
        return np.frombuffer(raw, dtype=np.uint8).reshape(360, 640, 3)
    def bounds(picture):
        ys, xs = np.where(np.all(picture > 180, axis=2))
        return (xs.min(), ys.min(), xs.max(), ys.max())
    before, after = bounds(pixels("original", .5)), bounds(pixels("changed", .5))
    assert (after[0] + after[2] - before[0] - before[2]) / 2 == pytest.approx(96, abs=4)
    assert (after[1] + after[3] - before[1] - before[3]) / 2 == pytest.approx(-72, abs=4)
    assert (after[2] - after[0]) / (before[2] - before[0]) == pytest.approx(1.5, abs=.15)
    assert bounds(pixels("changed", 1.5)) == bounds(pixels("original", 1.5))
