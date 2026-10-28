"""Static-picture detection for selected segments (#288)."""

import shutil
import subprocess

import numpy as np
import pytest

from src.media import visual_suitability
from src.media.visual_suitability import (
    is_visually_static,
    longest_static_run,
    segment_static_fraction,
    static_run_fraction,
    still_picture_shows_face,
)
from src.services import video_service
from src.services.video_service import VideoService


def _card(seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, size=(18, 32), dtype=np.uint8)


def test_held_card_with_compression_noise_is_static():
    card = _card()
    rng = np.random.default_rng(1)
    frames = [np.clip(card.astype(int) + rng.integers(-4, 5, card.shape), 0, 255).astype(np.uint8)
              for _ in range(40)]
    assert static_run_fraction(frames) == 1.0
    assert is_visually_static(static_run_fraction(frames))


def test_changing_footage_is_not_static():
    frames = [_card(seed) for seed in range(40)]
    assert static_run_fraction(frames) == pytest.approx(1 / 40)
    assert not is_visually_static(static_run_fraction(frames))


def test_slow_drift_does_not_chain_into_one_run():
    # A slow pan changes little frame to frame but a lot over the segment;
    # comparing against the run's first frame keeps it from reading as static.
    base = np.tile(np.arange(64, dtype=np.uint8) * 4, (18, 1))
    frames = [base[:, offset:offset + 32].copy() for offset in range(32)]
    assert static_run_fraction(frames) < 0.8


def test_card_covering_part_of_the_segment_is_not_static():
    frames = [_card(seed) for seed in range(10)] + [_card(99)] * 30
    assert static_run_fraction(frames) == pytest.approx(0.75)
    assert not is_visually_static(0.75)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_segment_fraction_decodes_real_video(tmp_path):
    path = tmp_path / "card_then_motion.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "color=c=navy:s=320x180:d=6:r=25",
            "-f", "lavfi", "-i", "testsrc2=s=320x180:d=6:r=25",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
    )
    assert segment_static_fraction(path, [(0.0, 6.0)]) == 1.0
    assert segment_static_fraction(path, [(6.0, 12.0)]) < 0.5
    assert segment_static_fraction(path, [(0.0, 1.0)]) is None


def _segments():
    return [
        {"start_time": "13:26", "end_time": "13:48", "text": "Kane over the interruption card"},
        {"start_time": "06:49", "end_time": "07:20", "text": "Endlager"},
        {"start_time": "01:42", "end_time": "02:17", "text": "Eisenkot"},
    ]


@pytest.fixture
def no_faces(monkeypatch):
    monkeypatch.setattr(video_service, "still_picture_shows_face", lambda path, ranges: False)


def test_interruption_card_segment_is_dropped(monkeypatch, tmp_path, no_faces):
    fractions = {806: 1.0, 409: 0.12, 102: 0.08}
    monkeypatch.setattr(
        video_service, "segment_static_fraction",
        lambda path, ranges: fractions[int(ranges[0][0])],
    )
    kept = VideoService._drop_static_segments(tmp_path / "source.mp4", _segments())
    assert [segment["start_time"] for segment in kept] == ["06:49", "01:42"]


def test_still_presenter_shot_is_kept(monkeypatch, tmp_path):
    # The 2026-10 tagesschau "Endlager" clip: an anchor in front of a fixed
    # backdrop scores 1.0 like the card, but a face is on screen.
    fractions = {806: 1.0, 409: 1.0, 102: 0.08}
    monkeypatch.setattr(
        video_service, "segment_static_fraction",
        lambda path, ranges: fractions[int(ranges[0][0])],
    )
    monkeypatch.setattr(
        video_service, "still_picture_shows_face",
        lambda path, ranges: int(ranges[0][0]) == 409,
    )
    kept = VideoService._drop_static_segments(tmp_path / "source.mp4", _segments())
    assert [segment["start_time"] for segment in kept] == ["06:49", "01:42"]


def test_failed_face_check_keeps_the_segment(monkeypatch, tmp_path):
    monkeypatch.setattr(
        video_service, "segment_static_fraction",
        lambda path, ranges: 1.0 if int(ranges[0][0]) == 806 else 0.1,
    )

    def fail(path, ranges):
        raise RuntimeError("detector unavailable")

    monkeypatch.setattr(video_service, "still_picture_shows_face", fail)
    segments = _segments()
    assert VideoService._drop_static_segments(tmp_path / "source.mp4", segments) == segments


def test_longest_static_run_reports_where_the_run_is():
    frames = [_card(seed) for seed in range(5)] + [_card(99)] * 12 + [_card(7)]
    assert longest_static_run(frames) == (5, 12)
    assert longest_static_run([]) == (0, 0)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_face_check_looks_inside_the_held_picture(tmp_path):
    path = tmp_path / "motion_then_card.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=s=320x180:d=3:r=25",
            "-f", "lavfi", "-i", "color=c=navy:s=320x180:d=8:r=25",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
    )

    class Recorder:
        def __init__(self):
            self.frames = []

        def detect(self, frame):
            self.frames.append(frame)
            return []

    recorder = Recorder()
    assert still_picture_shows_face(path, [(0.0, 11.0)], detector=recorder) is False
    assert len(recorder.frames) == visual_suitability.STATIC_FACE_SAMPLES
    for frame in recorder.frames:
        assert frame.shape[1] == visual_suitability.STATIC_FACE_FRAME_WIDTH
        # navy in BGR: blue high, green and red near zero.
        assert frame[..., 0].mean() > 100 and frame[..., 2].mean() < 20

    class Presenter(Recorder):
        def detect(self, frame):
            return [(300.0, 100.0, 40.0, 50.0, 0.94)]

    assert still_picture_shows_face(path, [(0.0, 11.0)], detector=Presenter()) is True

    class TinyFaces(Recorder):
        def detect(self, frame):
            return [(300.0, 100.0, 8.0, 10.0, 0.95), (100.0, 100.0, 60.0, 60.0, 0.5)]

    assert still_picture_shows_face(path, [(0.0, 11.0)], detector=TinyFaces()) is False


def test_all_static_source_keeps_every_segment(monkeypatch, tmp_path, no_faces):
    # An audio podcast published over cover art: nothing better to pick.
    monkeypatch.setattr(video_service, "segment_static_fraction", lambda path, ranges: 1.0)
    segments = _segments()
    assert VideoService._drop_static_segments(tmp_path / "source.mp4", segments) == segments


def test_failed_visual_check_keeps_the_segment(monkeypatch, tmp_path, no_faces):
    def fail(path, ranges):
        if int(ranges[0][0]) == 806:
            raise RuntimeError("no video stream")
        return 0.1

    monkeypatch.setattr(video_service, "segment_static_fraction", fail)
    segments = _segments()
    assert VideoService._drop_static_segments(tmp_path / "source.mp4", segments) == segments


def test_section_download_ranges_are_mapped_to_the_local_timeline(monkeypatch, tmp_path):
    from src.youtube_source import build_time_map, save_time_map

    source = tmp_path / "sections.mp4"
    source.write_bytes(b"")
    save_time_map(source, build_time_map([(792.0, 840.0), (396.0, 450.0)]))
    seen = []
    monkeypatch.setattr(
        video_service, "segment_static_fraction",
        lambda path, ranges: seen.append(ranges) or 0.1,
    )
    VideoService._drop_static_segments(source, _segments()[:2])
    assert seen[0] == [(14.0, 36.0)]
    assert seen[1] == [(61.0, 92.0)]


def test_sampling_constants_stay_cheap():
    assert visual_suitability.STATIC_SAMPLE_WIDTH * visual_suitability.STATIC_SAMPLE_HEIGHT <= 1024
