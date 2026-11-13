"""Face track + person matte analysis (FINAL §6.2, §6.4; WP12c)."""

from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from src.edit_fx.contracts import FaceTrack, MatteWindow
from src.media import fx_analysis as fa

HEAD = "[0:v]crop=ih*9/16:ih,scale=1080:1920:flags=lanczos,setsar=1[vout]"
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


# ----------------------------------------------------------------------------- fakes

class FakeDetector:
    """One face whose box drifts right by ``step`` px per frame, with landmarks."""

    def __init__(self, step: float = 0.0, empty_from: int | None = None):
        self.calls: list[tuple[int, int, bool]] = []
        self.step = step
        self.empty_from = empty_from

    def detect(self, frame, landmarks=False):
        n = len(self.calls)
        self.calls.append((frame.shape[1], frame.shape[0], landmarks))
        if self.empty_from is not None and n >= self.empty_from:
            return []
        x = 100.0 + self.step * n
        box = (x, 200.0, 108.0, 144.0, 0.9)
        points = (x + 30, 250.0, x + 78, 250.0, x + 54, 280.0, x + 34, 310.0, x + 74, 310.0)
        tiny = (5.0, 5.0, 4.0, 4.0, 0.8, *(6.0,) * 10)  # below FACE_PRESENCE_MIN_AREA: dropped
        return [box + points if landmarks else box, tiny if landmarks else tiny[:5]]


class BlockSegmenter:
    """Person probability 1 inside a centred block covering ``fraction`` of the frame, 0 elsewhere."""

    def __init__(self, fraction: float = 0.25):
        self.calls = 0
        side = int(round(fa.SEGMENTER_INPUT * math.sqrt(fraction)))
        lo = (fa.SEGMENTER_INPUT - side) // 2
        self.prob = np.zeros((fa.SEGMENTER_INPUT, fa.SEGMENTER_INPUT), np.float32)
        self.prob[lo:lo + side, lo:lo + side] = 1.0

    def probability(self, frame):
        self.calls += 1
        return self.prob.copy()


class FrameRunner:
    """Stands in for the ffmpeg decode: counts decodes, records commands, yields ``count`` frames."""

    def __init__(self, count: int):
        self.count = count
        self.commands: list[list[str]] = []

    def __call__(self, command, width, height):
        self.commands.append(list(command))
        for index in range(self.count):
            yield np.full((height, width, 3), index % 255, np.uint8)


@pytest.fixture
def fakes(monkeypatch):
    detector, segmenter, runner = FakeDetector(), BlockSegmenter(), FrameRunner(30)   # 30 frames = 5 s at 6 fps
    monkeypatch.setattr(fa, "open_yunet_detector", lambda *a, **k: detector)
    monkeypatch.setattr(fa, "load_segmenter", lambda *a, **k: segmenter)
    monkeypatch.setattr(fa, "_iter_frames", runner)
    return detector, segmenter, runner


def read_gray(path: Path, width: int, height: int) -> np.ndarray:
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(out, np.uint8).reshape(-1, height, width)


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
         "stream=codec_name,pix_fmt,width,height,r_frame_rate,nb_read_frames", "-of", "default=nw=1", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return dict(line.split("=", 1) for line in out.strip().splitlines())


def lavfi_video(path: Path, *, size: str, seconds: float) -> Path:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30:duration={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )
    return path


# ----------------------------------------------------------------------------- graph

def test_fps_is_placed_before_the_head():
    graph = fa.analysis_graph(HEAD, "[vout]", 540, 960)
    assert graph.startswith("[0:v]fps=6[fxan_in0];[fxan_in0]crop=ih*9/16:ih")
    assert graph.endswith("[vout]scale=540:960:flags=bilinear,setsar=1,format=bgr24[fxan_out]")
    assert graph.index("fps=6") < graph.index("crop=")
    assert "[0:v]crop" not in graph


def test_every_input_reference_gets_the_sampled_stream():
    head = "[0:v]crop=200:200:0:0[a];[0]crop=200:200:200:0[b];[a][b]vstack[stack]"
    graph = fa.analysis_graph(head, "[stack]", 200, 400)
    assert graph.startswith("[0:v]fps=6,split=2[fxan_in0][fxan_in1];")
    assert "[fxan_in0]crop=200:200:0:0[a];[fxan_in1]crop=200:200:200:0[b]" in graph
    assert "[0]" not in graph and graph.count("[0:v]") == 1


def test_unlabelled_head_reads_the_sampled_stream():
    graph = fa.analysis_graph("scale=1080:1920,setsar=1[base]", "[base]", 540, 960)
    assert graph.startswith("[0:v]fps=6[fxan_in0];[fxan_in0]scale=1080:1920")


def test_a_zoompan_head_is_sampled_after_the_head():
    head = "[0:v]scale=1620:2880,zoompan=z='1+0.05*on/150':d=1:s=1080x1920:fps=30[vout]"
    graph = fa.analysis_graph(head, "[vout]", 540, 960)
    assert graph.startswith(head + ";[vout]fps=6,scale=540:960")


@pytest.mark.parametrize("size, expected", [
    ((1080, 1920), (540, 960)), ((1920, 1080), (960, 540)), ((1080, 1080), (540, 540)),
    ((480, 854), (480, 854)), ((1081, 1921), (540, 960)),
])
def test_analysis_size(size, expected):
    assert fa.analysis_size(*size) == expected


# ----------------------------------------------------------------------------- one decode

def test_face_and_matte_share_one_decode(fakes, tmp_path):
    detector, segmenter, runner = fakes
    dest = tmp_path / "matte.mkv"
    track, windows = fa.analyse(Path("source.mp4"), HEAD, "[vout]", out_w=1080, out_h=1920, face=True,
                                matte_windows=[(1.0, 2.0)], matte_dest=dest)
    assert len(runner.commands) == 1                       # ONE decode for face + matte (AR-M8)
    command = runner.commands[0]
    graph = command[command.index("-filter_complex") + 1]
    assert graph.index("fps=6") < graph.index("crop=")     # fps=6 before the head
    assert command[command.index("-i") + 1] == "source.mp4"
    assert "-an" in command and command[-1] == "pipe:1"
    # Face on every analysis frame at 540x960 with landmarks; segmentation only inside the window ± 0.2 s.
    assert len(detector.calls) == 30 and set(detector.calls) == {(540, 960, True)}
    assert segmenter.calls == sum(1 for i in range(30) if 0.8 - 1e-6 <= i / 6 <= 2.2 + 1e-6)
    assert windows == [MatteWindow(start=1.0, end=2.0, person=True)]
    assert dest.is_file()

    parsed = FaceTrack.model_validate(track)
    assert parsed.space == "picture" and parsed.fps == 6
    assert [t for t, _ in parsed.samples] == pytest.approx([i / 6 for i in range(30)], abs=1e-4)
    first = parsed.samples[0][1]
    assert len(first) == 1 and len(first[0]) == 15         # tiny box dropped; box + score + 5 landmarks
    assert first[0][:5] == pytest.approx((100 / 540, 200 / 960, 108 / 540, 144 / 960, 0.9), abs=1e-4)
    assert first[0][5:7] == pytest.approx((130 / 540, 250 / 960), abs=1e-4)


def test_nothing_to_analyse_decodes_nothing(fakes, tmp_path):
    _, _, runner = fakes
    assert fa.analyse(Path("s.mp4"), HEAD, "[vout]", out_w=1080, out_h=1920, face=False,
                      matte_windows=[], matte_dest=tmp_path / "m.mkv") == (None, [])
    assert runner.commands == []


def test_face_only_runs_no_segmentation(fakes, tmp_path):
    _, segmenter, runner = fakes
    track, windows = fa.analyse(Path("s.mp4"), HEAD, "[vout]", out_w=1080, out_h=1920, face=True,
                                matte_windows=[], matte_dest=None)
    assert len(runner.commands) == 1 and segmenter.calls == 0
    assert windows == [] and len(track["samples"]) == 30


def test_face_track_is_smoothed_with_a_one_pole_ema(monkeypatch, fakes):
    detector = FakeDetector(step=20.0)
    monkeypatch.setattr(fa, "open_yunet_detector", lambda *a, **k: detector)
    track, _ = fa.analyse(Path("s.mp4"), HEAD, "[vout]", out_w=1080, out_h=1920, face=True,
                          matte_windows=[], matte_dest=None)
    xs = [faces[0][0] for _, faces in track["samples"][:6]]
    raw = [(100 + 20 * i) / 540 for i in range(6)]
    expected = [raw[0]]
    for value in raw[1:]:
        expected.append(0.5 * value + 0.5 * expected[-1])
    assert xs == pytest.approx(expected, abs=1e-4)


def test_a_cut_to_a_face_elsewhere_snaps_instead_of_sliding():
    smoother = fa._FaceSmoother()
    left = np.array([0.1, 0.3, 0.2, 0.1, 0.9] + [0.2] * 10)
    right = np.array([0.7, 0.3, 0.2, 0.1, 0.9] + [0.8] * 10)
    smoother.update([left])
    (after_cut,) = smoother.update([right])
    assert after_cut == pytest.approx(right)
    (settling,) = smoother.update([right + 0.02])
    assert settling[0] == pytest.approx(0.71)


def test_face_track_keeps_samples_without_a_face(monkeypatch, fakes):
    monkeypatch.setattr(fa, "open_yunet_detector", lambda *a, **k: FakeDetector(empty_from=10))
    track, _ = fa.analyse(Path("s.mp4"), HEAD, "[vout]", out_w=1080, out_h=1920, face=True,
                          matte_windows=[], matte_dest=None)
    assert len(track["samples"]) == 30
    assert all(faces for _, faces in track["samples"][:10])
    assert all(faces == [] for _, faces in track["samples"][10:])


def test_two_faces_at_most_largest_first():
    class Many:
        def detect(self, frame, landmarks=False):
            return [(10.0 * i, 0.0, 40.0 + 20 * i, 40.0 + 20 * i, 0.9, *(1.0,) * 10) for i in range(4)]

    rows = fa._frame_faces(Many(), np.zeros((960, 540, 3), np.uint8))
    assert len(rows) == 2 and rows[0][2] > rows[1][2]


# ----------------------------------------------------------------------------- matte file

@needs_ffmpeg
def test_matte_file_is_255_outside_windows(fakes, tmp_path):
    dest = tmp_path / "matte.mkv"
    windows_in = [(1.0, 1.6), (3.0, 3.5)]
    _, windows = fa.analyse(Path("s.mp4"), HEAD, "[vout]", out_w=1080, out_h=1920, face=False,
                            matte_windows=windows_in, matte_dest=dest)
    assert [w.person for w in windows] == [True, True]
    info = probe(dest)
    assert (info["codec_name"], info["pix_fmt"], info["width"], info["height"], info["r_frame_rate"]) == \
        ("ffv1", "gray", "540", "960", "15/1")
    frames = read_gray(dest, 540, 960)
    assert len(frames) == math.ceil(30 * 15 / 6)               # full length: 5 s at 15 fps
    half = 0.5 / 15
    for j, frame in enumerate(frames):
        t = j / 15
        inside = any(s - half <= t <= e + half for s, e in windows_in)
        if inside:
            assert frame.min() == 0 and frame.max() == 255     # the block is the person, the rest is cut
            assert frame[480, 270] == 255 and frame[5, 5] == 0
        else:
            assert frame.min() == 255, f"frame {j} (t={t:.3f}) is not opaque outside the windows"


@needs_ffmpeg
def test_a_window_without_a_person_is_drawn_in_front_and_writes_no_file(monkeypatch, fakes, tmp_path):
    monkeypatch.setattr(fa, "load_segmenter", lambda *a, **k: BlockSegmenter(fraction=0.02))
    dest = tmp_path / "matte.mkv"
    dest.write_bytes(b"stale")
    _, windows = fa.analyse(Path("s.mp4"), HEAD, "[vout]", out_w=1080, out_h=1920, face=False,
                            matte_windows=[(1.0, 2.0)], matte_dest=dest)
    assert windows == [MatteWindow(start=1.0, end=2.0, person=False)]
    assert not dest.exists()


def test_windows_come_back_in_input_order_including_empty_and_late_ones(fakes, tmp_path):
    _, windows = fa.analyse(Path("s.mp4"), HEAD, "[vout]", out_w=1080, out_h=1920, face=False,
                            matte_windows=[(3.0, 3.4), (2.0, 2.0), (40.0, 41.0), (0.5, 1.0)], matte_dest=None)
    assert [(w.start, w.end, w.person) for w in windows] == [
        (3.0, 3.4, True), (2.0, 2.0, False), (40.0, 41.0, False), (0.5, 1.0, True)]


@pytest.mark.parametrize("model", ["missing", "corrupt"])
def test_model_load_failure_means_no_person_never_an_error(monkeypatch, tmp_path, model):
    path = tmp_path / "pphumanseg.onnx"
    if model == "corrupt":
        path.write_bytes(b"not an onnx model")
    monkeypatch.setattr(fa, "PPHUMANSEG_MODEL_PATH", path)
    monkeypatch.setattr(fa, "open_yunet_detector", lambda *a, **k: FakeDetector())
    runner = FrameRunner(12)
    monkeypatch.setattr(fa, "_iter_frames", runner)
    assert fa.load_segmenter() is None
    dest = tmp_path / "matte.mkv"
    track, windows = fa.analyse(Path("s.mp4"), HEAD, "[vout]", out_w=1080, out_h=1920, face=True,
                                matte_windows=[(0.5, 1.5)], matte_dest=dest)
    assert windows == [MatteWindow(start=0.5, end=1.5, person=False)]
    assert not dest.exists()
    assert track is not None and len(track["samples"]) == 12   # the face track is unaffected


def test_matte_only_with_no_model_decodes_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(fa, "PPHUMANSEG_MODEL_PATH", tmp_path / "missing.onnx")
    runner = FrameRunner(12)
    monkeypatch.setattr(fa, "_iter_frames", runner)
    _, windows = fa.analyse(Path("s.mp4"), HEAD, "[vout]", out_w=1080, out_h=1920, face=False,
                            matte_windows=[(0.5, 1.5)], matte_dest=None)
    assert windows == [MatteWindow(start=0.5, end=1.5, person=False)] and runner.commands == []


@needs_ffmpeg
def test_decode_failure_is_logged_not_raised(tmp_path):
    track, windows = fa.analyse(tmp_path / "missing.mp4", HEAD, "[vout]", out_w=1080, out_h=1920, face=True,
                                matte_windows=[(0.0, 1.0)], matte_dest=tmp_path / "m.mkv")
    assert track is None and windows == [MatteWindow(start=0.0, end=1.0, person=False)]
    assert not (tmp_path / "m.mkv").exists()


# ----------------------------------------------------------------------------- real ffmpeg + real models

@needs_ffmpeg
def test_real_decode_samples_the_framed_picture_at_6_fps(tmp_path):
    source = lavfi_video(tmp_path / "src.mp4", size="640x360", seconds=2)
    head = "[0:v]crop=ih*9/16:ih,scale=1080:1920,setsar=1[vout]"
    track, windows = fa.analyse(source, head, "[vout]", out_w=1080, out_h=1920, face=True,
                                matte_windows=[(0.5, 1.0)], matte_dest=tmp_path / "m.mkv")
    assert len(track["samples"]) == 12                     # 2 s at 6 fps
    assert all(faces == [] for _, faces in track["samples"])   # a test pattern has no face
    assert len(windows) == 1 and isinstance(windows[0].person, bool)


def test_bundled_segmentation_model_loads_and_returns_a_probability():
    assert fa.PPHUMANSEG_MODEL_PATH.is_file()
    segmenter = fa.load_segmenter()
    assert segmenter is not None and fa.load_segmenter() is segmenter    # cached per thread
    prob = segmenter.probability(np.zeros((960, 540, 3), np.uint8))
    assert prob.shape == (192, 192) and prob.dtype == np.float32
    assert 0.0 <= prob.min() and prob.max() <= 1.0


# ----------------------------------------------------------------------------- studio helpers

@needs_ffmpeg
def test_face_track_for_video_samples_the_whole_video(monkeypatch, tmp_path):
    source = lavfi_video(tmp_path / "base.mp4", size="1080x1920", seconds=2)
    detector = FakeDetector()
    monkeypatch.setattr(fa, "open_yunet_detector", lambda *a, **k: detector)
    track = fa.face_track_for_video(source)
    parsed = FaceTrack.model_validate(track)
    assert parsed.space == "picture" and parsed.fps == 6 and len(parsed.samples) == 12
    assert set(detector.calls) == {(540, 960, True)}
    assert fa.face_track_for_video(tmp_path / "missing.mp4") is None


@needs_ffmpeg
def test_matte_for_video_writes_a_browser_playable_grey_matte(monkeypatch, tmp_path):
    source = lavfi_video(tmp_path / "base.mp4", size="1080x1920", seconds=1)
    segmenter = BlockSegmenter()
    monkeypatch.setattr(fa, "load_segmenter", lambda *a, **k: segmenter)
    dest = tmp_path / "matte.mp4"
    assert fa.matte_for_video(source, dest) is True
    info = probe(dest)
    assert (info["codec_name"], info["pix_fmt"], info["width"], info["height"], info["r_frame_rate"]) == \
        ("h264", "yuv420p", "540", "960", "15/1")
    assert int(info["nb_read_frames"]) == 15 and segmenter.calls == 15
    frame = read_gray(dest, 540, 960)[5]
    assert frame[480, 270] >= 250 and frame[5, 5] <= 5
    assert not list(tmp_path.glob(".*partial*"))


@needs_ffmpeg
def test_matte_for_video_fails_softly(monkeypatch, tmp_path):
    dest = tmp_path / "matte.mp4"
    assert fa.matte_for_video(tmp_path / "missing.mp4", dest) is False and not dest.exists()
    source = lavfi_video(tmp_path / "base.mp4", size="1080x1920", seconds=1)
    monkeypatch.setattr(fa, "PPHUMANSEG_MODEL_PATH", tmp_path / "missing.onnx")
    assert fa.matte_for_video(source, dest) is False and not dest.exists()


# ----------------------------------------------------------------------------- review fixes

class FailingAfter:
    """Wraps a fake model: works for ``ok`` calls, then raises like a cv2.error would."""

    def __init__(self, inner, ok: int):
        self.inner, self.ok, self.calls = inner, ok, 0

    def _tick(self):
        self.calls += 1
        if self.calls > self.ok:
            raise RuntimeError("cv2.error: forward failed")

    def detect(self, frame, landmarks=False):
        self._tick()
        return self.inner.detect(frame, landmarks=landmarks)

    def probability(self, frame):
        self._tick()
        return self.inner.probability(frame)


def test_a_segmentation_failure_keeps_the_face_track(monkeypatch, fakes, tmp_path):
    monkeypatch.setattr(fa, "load_segmenter", lambda *a, **k: FailingAfter(BlockSegmenter(), ok=2))
    dest = tmp_path / "matte.mkv"
    track, windows = fa.analyse(Path("s.mp4"), HEAD, "[vout]", out_w=1080, out_h=1920, face=True,
                                matte_windows=[(1.0, 2.0)], matte_dest=dest)
    assert windows == [MatteWindow(start=1.0, end=2.0, person=False)]
    assert not dest.exists()
    assert track is not None and len(track["samples"]) == 30


@needs_ffmpeg
def test_a_face_detection_failure_keeps_the_matte(monkeypatch, fakes, tmp_path):
    monkeypatch.setattr(fa, "open_yunet_detector", lambda *a, **k: FailingAfter(FakeDetector(), ok=3))
    dest = tmp_path / "matte.mkv"
    track, windows = fa.analyse(Path("s.mp4"), HEAD, "[vout]", out_w=1080, out_h=1920, face=True,
                                matte_windows=[(1.0, 2.0)], matte_dest=dest)
    assert track is None
    assert windows == [MatteWindow(start=1.0, end=2.0, person=True)] and dest.is_file()


def test_matte_smoothing_has_no_lag():
    group = fa._WindowSamples(0.0, 2.0)
    for index in range(13):                       # a person appears at sample 6
        group.add(index / 6, np.full((2, 2), 1.0 if index >= 6 else 0.0, np.float32))
    group.smooth()
    values = [float(p[0, 0]) for p in group.probs]
    # Zero phase: the step's half-way point stays at the step, and the curve is symmetric about it.
    assert values[5] < 0.5 < values[6]
    assert values[5] == pytest.approx(1.0 - values[6], abs=1e-6)
    assert values == sorted(values)
