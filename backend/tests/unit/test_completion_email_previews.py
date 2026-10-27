import email
import random
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import cv2
import numpy as np
import pytest

from src.services import email_previews
from src.services.email_previews import (
    ClipPreview,
    TaskPreviews,
    _Deadline,
    build_task_previews,
    center_crop,
    colorfulness,
    source_still,
)
from src.services.email_service import EmailContent, InlineImage, SesEmailService
from src.services.task_completion_email_service import (
    TaskCompletionEmailService,
    TaskCompletionRecipient,
)

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def email_config(**extra):
    return SimpleNamespace(
        aws_region="eu-west-1", aws_access_key_id="k", aws_secret_access_key="s",
        ses_from_email="SupoClip <f@x.test>", app_base_url="https://app.test", **extra,
    )


def make_video(path: Path, *inputs: str, size="320x180") -> Path:
    args = []
    for source in inputs:
        args += ["-f", "lavfi", "-i", f"{source}:s={size}"]
    subprocess.run(
        ["ffmpeg", "-v", "error", *args, "-filter_complex",
         f"{''.join(f'[{i}]' for i in range(len(inputs)))}concat=n={len(inputs)}:v=1",
         "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )
    return path


def decode(jpeg: bytes) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)


def test_colorfulness_rejects_black_and_prefers_color():
    black = np.zeros((90, 160, 3), np.uint8)
    gray = np.full((90, 160, 3), 128, np.uint8)
    color = cv2.cvtColor(
        np.dstack([np.tile(np.arange(160, dtype=np.uint8), (90, 1)), np.full((90, 160, 2), 200, np.uint8)]),
        cv2.COLOR_HSV2BGR,
    )
    assert colorfulness(black) == 0
    assert colorfulness(color) > colorfulness(gray)


def test_center_crop_strips_letterbox_bars():
    # hqdefault-style 4:3 thumbnail: a 16:9 picture between black bars.
    image = np.zeros((360, 480, 3), np.uint8)
    image[45:315] = 200
    cropped = center_crop(image, 16 / 9)
    assert cropped.shape[:2] == (270, 480) and cropped.min() == 200


@needs_ffmpeg
def test_source_still_skips_black_footage(tmp_path):
    video = make_video(tmp_path / "source.mp4", "color=black:d=6", "smptebars=d=4")
    jpeg = source_still(video, _Deadline(30), samples=12, rng=random.Random(1))
    assert colorfulness(decode(jpeg)) > 0


@needs_ffmpeg
def test_portrait_source_is_skipped(tmp_path):
    video = make_video(tmp_path / "portrait.mp4", "smptebars=d=2", size="180x320")
    assert source_still(video, _Deadline(30)) is None


@needs_ffmpeg
def test_clip_stills_match_the_clip_shape(tmp_path):
    vertical = make_video(tmp_path / "v.mp4", "smptebars=d=6", size="180x320")
    wide = make_video(tmp_path / "w.mp4", "smptebars=d=6")
    previews = build_task_previews(
        source_path=None, youtube_video_id=None, clips=[(str(vertical), "A"), (str(wide), None)],
    )
    tall, square = (decode(clip.image).shape[:2] for clip in previews.clips)
    assert tall[0] > tall[1] and abs(square[0] - square[1]) <= 1


@needs_ffmpeg
def test_one_failing_clip_does_not_drop_the_others(tmp_path):
    good = make_video(tmp_path / "good.mp4", "smptebars=d=6", size="180x320")

    @contextmanager
    def open_clip(reference):
        if reference == "s3://broken":
            raise OSError("storage unavailable")
        yield Path(reference)

    previews = build_task_previews(
        source_path=good, youtube_video_id=None,
        clips=[("s3://broken", "Lost"), (str(good), "Kept")], open_clip=open_clip,
    )
    assert [clip.title for clip in previews.clips] == ["Kept"]


def test_previews_stop_when_the_budget_runs_out(monkeypatch):
    calls = []
    monkeypatch.setattr(email_previews, "clip_still", lambda path, deadline: calls.append(path) or b"x")
    previews = build_task_previews(
        source_path=None, youtube_video_id=None, clips=[("a", None), ("b", None)], budget_seconds=0,
    )
    assert previews == TaskPreviews() and calls == []


def test_completion_email_embeds_previews_inline():
    service = TaskCompletionEmailService(email_config())
    content = service._build_task_completed_email(
        recipient=TaskCompletionRecipient("a@b.test", first_name="Ada"),
        task_id="t1",
        source_title="Talk",
        clips_count=5,
        previews=TaskPreviews(
            source=b"src", clips=(ClipPreview(b"c0", "Why <this> works"), ClipPreview(b"c1")),
        ),
    )
    assert 'src="cid:source@supoclip"' in content.html and 'src="cid:clip1@supoclip"' in content.html
    assert 'alt="Talk"' in content.html and 'alt="Clip 2"' in content.html
    assert "Why &lt;this&gt; works" in content.html and "- Why <this> works" in content.text
    assert [image.content_id for image in content.inline_images] == [
        "source@supoclip", "clip0@supoclip", "clip1@supoclip",
    ]


def test_completion_email_without_previews_has_no_images():
    service = TaskCompletionEmailService(email_config())
    content = service._build_task_completed_email(
        recipient=TaskCompletionRecipient("a@b.test"), task_id="t1", source_title=None, clips_count=1,
    )
    assert "cid:" not in content.html and content.inline_images == ()


def test_inline_images_are_sent_as_related_mime():
    service = SesEmailService(email_config())
    service._client = MagicMock()
    service._send("to@x.test", EmailContent(
        "Clips ready ✂", "<p>x</p>", "x", headers=(("X-Test", "1"),),
        inline_images=(InlineImage("clip0@supoclip", b"jpg"),),
    ))
    kwargs = service._client.send_email.call_args.kwargs
    message = email.message_from_bytes(kwargs["Content"]["Raw"]["Data"], policy=email.policy.default)
    assert message["Subject"] == "Clips ready ✂" and message["X-Test"] == "1"
    assert message.get_content_type() == "multipart/alternative"
    text, related = message.get_payload()
    html, image = related.get_payload()
    assert (text.get_content_type(), related.get_content_type(), html.get_content_type()) == (
        "text/plain", "multipart/related", "text/html",
    )
    assert image["Content-ID"] == "<clip0@supoclip>" and image.get_content() == b"jpg"
    assert image.get_content_disposition() == "inline"


def test_emails_without_images_keep_simple_content():
    service = SesEmailService(email_config())
    service._client = MagicMock()
    service._send("to@x.test", EmailContent("S", "<p>x</p>", "x"))
    assert "Simple" in service._client.send_email.call_args.kwargs["Content"]
