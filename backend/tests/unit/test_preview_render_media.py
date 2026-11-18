"""Preview renders and their full-quality replay, with real ffmpeg on tiny media."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from src.media.ffmpeg import EncodeProfile, ffprobe_video_size, final_encode_args, scaled_output_size
from src.services import full_render_service
from src.services.full_render_service import (
    fetch_render_inputs,
    publish_render_inputs,
    render_full_clip_file,
    summarize,
)
from src.video_utils import (
    RENDER_CAPTIONS_NAME,
    RENDER_RECIPE_NAME,
    RENDER_SOURCE_NAME,
    create_optimized_clip,
    render_full_from_inputs,
)

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


@pytest.fixture(scope="module")
def source(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("preview-source") / "source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def render_preview(source: Path, directory: Path, output_format: str = "vertical"):
    report: dict = {}
    output = directory / "preview.mp4"
    ok = create_optimized_clip(
        source, 0.0, 2.0, output,
        add_subtitles=False,
        output_format=output_format,
        keep_ranges=[(0.0, 2.0)],
        hook_title="A preview hook title",
        extend_to_sentence=False,
        layout_report=report,
        preview=EncodeProfile(short_side=270, crf=30, preset="ultrafast"),
        render_inputs_dir=directory / "inputs",
    )
    assert ok
    return output, report


def test_encode_profiles_scale_after_the_graph_and_keep_the_defaults():
    assert final_encode_args(1080, 1920) == final_encode_args(1080, 1920, None)
    preview = final_encode_args(1080, 1920, EncodeProfile(540, crf=28, preset="veryfast"))
    assert preview[preview.index("-crf") + 1] == "28"
    assert preview[preview.index("-preset") + 1] == "veryfast"
    assert preview[preview.index("-s") + 1] == "540x960"
    capped = final_encode_args(1080, 1920, EncodeProfile(720))
    default = final_encode_args(1080, 1920)
    assert capped[capped.index("-crf") + 1] == default[default.index("-crf") + 1]
    assert capped[capped.index("-s") + 1] == "720x1280"
    # Nothing to shrink: no scaler at all.
    assert "-s" not in final_encode_args(1280, 720, EncodeProfile(1080))
    assert scaled_output_size(1920, 1080, 540) == (960, 540)


@needs_ffmpeg
@pytest.mark.parametrize("output_format", ["vertical", "original"])
def test_a_preview_keeps_inputs_that_replay_the_full_render(source, tmp_path, output_format):
    preview, report = render_preview(source, tmp_path, output_format)
    assert report["render_quality"] == "preview"
    inputs = tmp_path / "inputs"
    assert {path.name for path in inputs.iterdir()} == {
        RENDER_RECIPE_NAME, RENDER_SOURCE_NAME, RENDER_CAPTIONS_NAME,
    }
    recipe = json.loads((inputs / RENDER_RECIPE_NAME).read_text())
    assert recipe["output_format"] == output_format
    assert recipe["captions"] is True  # the hook title is burned from the captions file

    full = tmp_path / "full.mp4"
    assert render_full_from_inputs(inputs, full)
    if output_format == "vertical":
        assert ffprobe_video_size(preview) == (270, 480)
        assert ffprobe_video_size(full) == (1080, 1920)
    else:
        assert ffprobe_video_size(preview) == (480, 270)
        assert ffprobe_video_size(full) == (640, 360)
    # Same stitched source: the full render plays exactly as long as the preview.
    durations = [
        float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, check=True,
        ).stdout)
        for path in (preview, full)
    ]
    assert abs(durations[0] - durations[1]) < 0.1


@needs_ffmpeg
def test_a_720p_plan_gets_its_cap_in_the_same_pass(source, tmp_path):
    render_preview(source, tmp_path)
    full_render_service.update_render_recipe(tmp_path / "inputs", max_short_side=720)
    output = tmp_path / "full.mp4"
    render_full_clip_file(tmp_path / "inputs", output)
    assert ffprobe_video_size(output) == (720, 1280)


@needs_ffmpeg
def test_a_full_render_without_inputs_fails(tmp_path):
    (tmp_path / RENDER_RECIPE_NAME).write_text(json.dumps({"version": 99}))
    with pytest.raises(ValueError):
        render_full_from_inputs(tmp_path, tmp_path / "out.mp4")


class FakeBucket:
    """Just enough of MediaStorage for render inputs in object storage."""

    enabled = True
    bucket = "media"

    def __init__(self, root: Path, *, fail_on: str | None = None):
        self.root = root
        self.fail_on = fail_on
        self.deleted: list[str] = []
        self.client = self

    def key(self, *parts):
        return "/".join(("prefix",) + parts)

    def reference(self, key):
        return f"s3://media/{key}"

    def parse(self, reference):
        return reference.removeprefix("s3://media/")

    def upload_file(self, path, key, **_extra):
        if self.fail_on and key.endswith(self.fail_on):
            raise OSError("bucket unavailable")
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        return self.reference(key)

    def fetch(self, reference, path):
        stored = self.root / self.parse(reference)
        if not stored.exists():
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(stored, path)
        return True

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)
        (self.root / Key).unlink(missing_ok=True)


def make_inputs(directory: Path, *, captions=True) -> Path:
    directory.mkdir(parents=True)
    (directory / RENDER_SOURCE_NAME).write_bytes(b"source")
    if captions:
        (directory / RENDER_CAPTIONS_NAME).write_text("[Script Info]")
    (directory / RENDER_RECIPE_NAME).write_text(json.dumps({"version": 1, "captions": captions}))
    return directory


def test_inputs_round_trip_through_the_bucket(tmp_path):
    bucket = FakeBucket(tmp_path / "bucket")
    local = make_inputs(tmp_path / "scratch" / "token123")

    reference = publish_render_inputs(bucket, local, "task-1")
    assert reference == "s3://media/prefix/render-inputs/task-1/token123/render.json"
    assert not local.exists()  # the bucket holds them now

    fetched = fetch_render_inputs(bucket, reference, tmp_path / "worker" / "inputs")
    assert {path.name for path in fetched.iterdir()} == {
        RENDER_RECIPE_NAME, RENDER_SOURCE_NAME, RENDER_CAPTIONS_NAME,
    }


def test_a_partial_upload_is_removed_and_the_local_inputs_stay(tmp_path):
    bucket = FakeBucket(tmp_path / "bucket", fail_on=RENDER_RECIPE_NAME)
    local = make_inputs(tmp_path / "scratch" / "token123")

    with pytest.raises(OSError):
        publish_render_inputs(bucket, local, "task-1")
    assert local.exists()
    assert sorted(Path(key).name for key in bucket.deleted) == [RENDER_CAPTIONS_NAME, RENDER_SOURCE_NAME]


def test_missing_inputs_are_reported(tmp_path):
    bucket = FakeBucket(tmp_path / "bucket")
    with pytest.raises(FileNotFoundError):
        fetch_render_inputs(bucket, "s3://media/prefix/render-inputs/t/x/render.json", tmp_path / "w")
    with pytest.raises(FileNotFoundError):
        fetch_render_inputs(bucket, str(tmp_path / "nowhere" / "render.json"), tmp_path / "w")


def test_task_summary_counts_ready_clips():
    clips = [
        {"id": "a", "render_quality": "full", "full_render": {"status": "ready", "progress": 100, "error": None}},
        {"id": "b", "render_quality": "preview", "full_render": {"status": "running", "progress": 40, "error": None}},
    ]
    summary = summarize(clips)
    assert (summary["total"], summary["ready"], summary["progress"], summary["status"]) == (2, 1, 70, "preparing")
    assert summarize([])["status"] == "ready"


@pytest.mark.asyncio
async def test_generation_renders_full_quality_when_inputs_cannot_be_stored(tmp_path, monkeypatch):
    from src.services import task_service as task_service_module
    from src.services.task_service import TaskService

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"preview")
    inputs = make_inputs(tmp_path / "inputs")
    finalized = []

    def unavailable(*_args):
        raise OSError("bucket unavailable")

    async def finalize(inputs_dir, clip_path, placements):
        finalized.append((inputs_dir, clip_path, placements))

    monkeypatch.setattr(task_service_module, "publish_render_inputs", unavailable)
    monkeypatch.setattr(task_service_module, "finalize_locally", finalize)
    service = TaskService.__new__(TaskService)
    info = {"path": str(clip), "filename": clip.name, "render_quality": "preview", "broll": [{"id": 1}]}

    assert await service._publish_preview_inputs("task-1", info, inputs) is None
    assert info["render_quality"] == "full"
    assert finalized == [(inputs, clip, [{"id": 1}])]


@pytest.mark.asyncio
async def test_generation_records_the_stored_inputs(tmp_path, monkeypatch):
    from src.services import task_service as task_service_module
    from src.services.task_service import TaskService

    monkeypatch.setattr(task_service_module, "publish_render_inputs", lambda storage, directory, task: "s3://ref")
    service = TaskService.__new__(TaskService)
    info = {"path": str(tmp_path / "clip.mp4"), "render_quality": "preview"}
    assert await service._publish_preview_inputs("task-1", info, tmp_path) == "s3://ref"
    assert info["render_quality"] == "preview"


def test_api_keys_and_ios_always_need_the_full_file():
    from types import SimpleNamespace

    from src.api.routes.full_renders import needs_full_file

    def request(headers):
        return SimpleNamespace(headers=headers, state=SimpleNamespace())

    assert needs_full_file(request({}), download=False) is False
    assert needs_full_file(request({}), download=True) is True
    assert needs_full_file(request({"x-supoclip-client": "ios"}), download=False) is True
    assert needs_full_file(request({"authorization": "Bearer sk_test"}), download=False) is True
    assert needs_full_file(request({"x-api-key": "sk_test", "user-agent": "supoclip-mcp/1"}), download=False) is True
    # An updated app says when it only plays the clip; saving stays full quality.
    playing = {"x-supoclip-client": "ios", "x-supoclip-accept-preview": "1"}
    assert needs_full_file(request(playing), download=False) is False
    assert needs_full_file(request(playing), download=True) is True
