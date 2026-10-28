"""Regressions at the generation/editor boundary, including actual ffmpeg output."""
import json
import subprocess

import cv2
import numpy as np
import pytest
from pydantic import ValidationError

from src.clip_source_map import load_clip_output_offsets, save_clip_source_ranges, slice_source_timeline
from src.editor_document import EditDocument, write_ass
from src.media import gap_activity
from src.media.source_captions import aggregate_caption_bands, caption_lines_in_frame
from src.services.editor_service import initial_caption_style
from src.services.video_service import VideoService


@pytest.mark.parametrize("timing", [{"start": 1, "end": 1}, {"start": -1, "end": 2}, {"start": 0, "end": float("inf")}])
def test_title_timing_rejects_invalid_intervals(timing):
    with pytest.raises(ValidationError):
        EditDocument(segments=[{"id": "s", "start": 0, "end": 4}], hookTiming=timing)


def test_custom_title_timing_reaches_ass_and_clamps_after_trim(tmp_path):
    doc = EditDocument(segments=[{"id": "s", "start": 0, "end": 4}], hookTitle="Editable title", hookTiming={"start": .75, "end": 2.25})
    path = tmp_path / "captions.ass"
    write_ass(path, doc, 1080, 1920)
    events = [s for s in path.read_text().splitlines() if s.startswith("Dialogue:")]
    assert any(",0:00:00.75,0:00:02.25," in s and "Editable " in s for s in events)
    doc.segments[0].end = 1
    write_ass(path, doc, 1080, 1920)
    assert ",0:00:00.75,0:00:01.00," in path.read_text()


def test_persisted_caption_center_is_not_shifted_again():
    style = initial_caption_style({"caption_template": "default"}, {"position_y": .61, "position_is_center": True}, True, 1080, 1920)
    assert style["y"] == pytest.approx(.61, abs=.001)


@pytest.mark.parametrize("boxed", [False, True])
def test_source_detector_finds_dark_captions(boxed):
    frame = np.full((960, 540, 3), 210 if not boxed else 100, np.uint8)
    if boxed:
        cv2.rectangle(frame, (25, 700), (515, 780), (255, 255, 255), -1)
    cv2.putText(frame, "CAPTIONS IN SOURCE", (35, 755), cv2.FONT_HERSHEY_DUPLEX, 1.15, (0, 0, 0), 3, cv2.LINE_AA)
    band = aggregate_caption_bands([caption_lines_in_frame(frame)] * 12)
    assert band is not None
    assert .70 < band[0] < band[1] < .83


def test_action_burst_keeps_only_its_window(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.touch()
    monkeypatch.setattr(gap_activity, "select_audio_stream_index", lambda _: 0)
    levels = np.full(200, -80, np.float32)
    levels[:20] = -20  # speech reference
    levels[100:110] = -22  # action at 5.0-5.5, surrounded by quiet filler
    monkeypatch.setattr(gap_activity, "_window_levels_db", lambda *_: levels)
    frames = np.zeros((36, 36, 64), np.uint8)
    monkeypatch.setattr(gap_activity, "_decode", lambda _: frames.tobytes())
    assert gap_activity.active_gap_ranges(source, [(1, 9)], [(0, 1)]) == [[(4.8, 5.7)]]


def test_camera_translation_is_not_local_action():
    image = np.random.default_rng(5).integers(0, 255, (36, 64), dtype=np.uint8)
    shifted = np.roll(image, 3, axis=1)
    assert gap_activity._local_motion(image, shifted) < 1
    changed = image.copy()
    changed[8:28, 10:54] = 0
    assert gap_activity._local_motion(image, changed) > gap_activity.GAP_MOTION_THRESHOLD


def test_failed_or_empty_video_decode_preserves_uncertain_action(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.touch()
    monkeypatch.setattr(gap_activity, "select_audio_stream_index", lambda _: None)
    monkeypatch.setattr(gap_activity, "_decode", lambda _: b"")
    assert gap_activity.active_gap_ranges(source, [(2, 8)], []) == [[(2, 8)]]


def test_crossfade_source_offsets_survive_trim_and_copy(tmp_path):
    source = tmp_path / "clip.mp4"
    ranges = [(10., 12.), (20., 22.), (30., 32.)]
    offsets = [0., 1.85, 3.7]
    save_clip_source_ranges(source, ranges, offsets)
    assert load_clip_output_offsets(source, ranges) == offsets
    sliced, mapped = slice_source_timeline(ranges, 1.9, 4.5, offsets)
    assert np.asarray(sliced) == pytest.approx(np.asarray([(11.9, 12), (20.05, 22), (30, 30.8)]))
    assert mapped == pytest.approx([0, 0, 1.8])
    save_clip_source_ranges(source, sliced, mapped)
    assert load_clip_output_offsets(source, sliced) == pytest.approx(mapped)


def test_animated_interruption_notice_is_dropped_but_presenter_is_kept(monkeypatch, tmp_path):
    module = "src.services.video_service."
    monkeypatch.setattr(module + "local_ranges_for", lambda _p, ranges: ranges)
    monkeypatch.setattr(module + "segment_shows_interruption_notice", lambda *_: True)
    monkeypatch.setattr(module + "segment_static_fraction", lambda *_: .1)  # animation defeats still-frame detection
    monkeypatch.setattr(module + "still_picture_shows_face", lambda _p, ranges: ranges[0][0] == 10)
    notice = {"start_time": "00:00", "end_time": "00:05", "text": "These images cannot be shown due to technical difficulties."}
    presenter = {**notice, "start_time": "00:10", "end_time": "00:15"}
    assert VideoService._drop_static_segments(tmp_path / "source.mp4", [notice, presenter]) == [presenter]
    assert VideoService._drop_static_segments(tmp_path / "source.mp4", [notice]) == []


def test_full_content_layout_keeps_raw_source_for_landscape(tmp_path, monkeypatch):
    from src.services import editor_service
    source = tmp_path / "clean.mp4"
    source.write_bytes(b"original-wide-video")
    monkeypatch.setattr(editor_service, "editor_preserved_layout", lambda *_a, **_k: {"kind": "fit", "landscape_recommended": True, "output_format": "vertical", "vertical_plan": ("fit", "vf")})
    def render(_source, destination, *_a, **_k):
        destination.write_bytes(b"vertical-layout")
        return True, 1080, 1920
    monkeypatch.setattr("src.media.reframing.render_reframed_clip_ffmpeg", render)
    assert editor_service.bake_editor_layout(source, "vertical", source, [(0, 4)], preserve_content=True)
    assert source.read_bytes() == b"original-wide-video"
    assert (tmp_path / "layout.mp4").read_bytes() == b"vertical-layout"
    assert json.loads((tmp_path / "layout.json").read_text())["landscapeRecommended"]


def test_real_render_uses_mixed_crossfade_and_hard_join_offsets(tmp_path):
    from src.media.ffmpeg import render_source_ranges_ffmpeg
    source, output = tmp_path / "source.mp4", tmp_path / "joined.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30:duration=4", "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)], check=True, capture_output=True)
    assert render_source_ranges_ffmpeg(source, [(0, 1), (1.5, 2.5), (3, 4)], output, output_offsets=[0, .85, 1.85])
    info = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(output)]))
    assert float(info["format"]["duration"]) == pytest.approx(2.85, abs=.07)
    assert {s["codec_type"] for s in info["streams"]} == {"audio", "video"}


def test_caption_probe_uses_split_tile_geometry(monkeypatch, tmp_path):
    from src.media.reframing import caption_probe_plan
    regions = {"left": {"tile_w": 160, "tile_h": 90, "tile_x": 0, "tile_y": 0}, "right": {"tile_w": 160, "tile_h": 90, "tile_x": 160, "tile_y": 0}}
    monkeypatch.setattr("src.media.reframing.detect_speaker_reframe_plan", lambda *_: {"mode": "split", "regions": regions})
    graph, mode = caption_probe_plan(tmp_path / "source.mp4", "vertical_split")
    assert mode == "complex"
    assert "crop=160:90:0:0" in graph and "crop=160:90:160:0" in graph
    assert graph.endswith("[vout]")


def test_caption_band_is_measured_after_full_frame_composition(tmp_path):
    from src.media.source_captions import detect_rendered_caption_band
    frame = np.full((360, 640, 3), 150, np.uint8)
    cv2.putText(frame, "CAPTIONS IN SOURCE", (80, 290), cv2.FONT_HERSHEY_DUPLEX, 1.1, (0, 0, 0), 6, cv2.LINE_AA)
    cv2.putText(frame, "CAPTIONS IN SOURCE", (80, 290), cv2.FONT_HERSHEY_DUPLEX, 1.1, (255, 255, 255), 2, cv2.LINE_AA)
    image, video = tmp_path / "frame.png", tmp_path / "source.mp4"
    cv2.imwrite(str(image), frame)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(image), "-t", "2", "-r", "12", "-pix_fmt", "yuv420p", str(video)], check=True, capture_output=True)
    band = detect_rendered_caption_band(video, "scale=540:304,pad=540:960:0:328", "vf", 2)
    assert band is not None
    # Source text at y=.8 is at output y~.59, not y=.8.
    assert .55 < band[0] < band[1] < .62


def test_partial_download_restore_retains_crossfade_offsets(tmp_path):
    from src.youtube_source import TimeMap, TimeSpan, restore_clip_timeline
    from src.clip_source_map import load_clip_source_ranges
    path = tmp_path / "clip.mp4"
    ranges = [(0., 2.), (3., 5.)]
    save_clip_source_ranges(path, ranges, [0, 1.85])
    mapping = TimeMap((TimeSpan(100, 104, 0), TimeSpan(200, 204, 4)))
    restore_clip_timeline({"path": str(path)}, mapping, {})
    originals = load_clip_source_ranges(path)
    assert originals == [(100, 102), (103, 104), (200, 201)]
    assert load_clip_output_offsets(path, originals) == pytest.approx([0, 1.85, 2.85])


def test_local_ocr_rejects_an_animated_notice_without_transcript_evidence(tmp_path, monkeypatch):
    import shutil
    if shutil.which("tesseract") is None:
        pytest.skip("Tesseract is installed in the backend image")
    from src.media import visual_suitability
    source = tmp_path / "animated-notice.mp4"
    source.touch()
    samples = []
    for index in range(3):
        image = np.full((360, 640, 3), 30 + index * 50, np.uint8)
        cv2.putText(image, "Kurze Unterbrechung", (70 + index * 3, 160), cv2.FONT_HERSHEY_DUPLEX, 1.2, (255, 255, 255), 2)
        cv2.rectangle(image, (index * 100, 260), (index * 100 + 60, 330), (0, 120, 240), -1)
        samples.append(image)
    monkeypatch.setattr(visual_suitability, "_decode_frame_bgr", lambda _p, t: samples[min(2, int(t // 3))])
    assert visual_suitability.segment_shows_interruption_notice(source, [(0, 9)])
    # A notice spoken over actual footage is not enough to identify a slate.
    monkeypatch.setattr(visual_suitability, "_decode_frame_bgr", lambda *_: np.full((360, 640, 3), 150, np.uint8))
    assert not visual_suitability.segment_shows_interruption_notice(source, [(0, 9)])


def test_export_with_fractional_fps_and_stock_has_no_extra_tail_frames(tmp_path):
    from src.editor_document import render_document
    source, stock = tmp_path / "clean.mp4", tmp_path / "stock.mp4"
    for output, rate, duration in [(source, "30000/1001", "9"), (stock, "25", "4")]:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=blue:size=160x90:rate={rate}:duration={duration}", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(output)], check=True, capture_output=True)
    footage = {"id": "1", "fileUrl": "https://videos.pexels.com/video-files/1/1-hd_1080_1920_25fps.mp4", "width": 1080, "height": 1920, "duration": 4}
    doc = EditDocument(segments=[{"id": "s", "start": 0, "end": 8}], framing={"aspect": "original"}, broll=[{"id": "b", "start": 4.97, "end": 7.97, "offset": 0, "query": "blue", "footage": footage}])
    render_document(tmp_path, "fractional", doc, "tiktok", {doc.broll[0].footage.key: stock})
    info = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams", "-of", "json", str(tmp_path / "export-fractional.mp4")]))["streams"][0]
    assert int(info["nb_frames"]) == 240
    assert float(info["duration"]) == pytest.approx(8, abs=.035)
