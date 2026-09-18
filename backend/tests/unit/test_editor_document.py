import json
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.editor_document import (
    EditDocument,
    RevisionConflict,
    caption_groups,
    editor_dir,
    mapped_words,
    output_size,
    render_document,
    save_document,
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
    assert len(caption_groups(words, 4)) == 2
    assert output_size(doc, 1920, 1080) == (1080, 1920)
    assert editor_dir("/tmp", "../task", "../clip", "../file").parent == Path(
        "/tmp/editor"
    )


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
