"""WP12a: the P1 → P2 → P3 chain (FINAL §6.2-§6.4, §16 chain bullets): command shapes, the scene, frame counts with
and without a warp, A/V alignment, safe mode, file mode, the 720p cap and that every visual cue kind draws."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

import numpy as np
import pytest

from src.edit_fx.contracts import FxCaptionStyle, MatteWindow
from src.edit_fx.warp import build_warp, identity_warp
from src.media import fx_chain
from src.media.ffmpeg import cap_video_short_side
from tests.unit.fx_media.fx_chain_kit import (  # noqa: F401 - chain_deps is a fixture
    FPS,
    chain_deps,
    cue,
    decode_audio,
    frames_rgb,
    make_source,
    plan,
    probe,
    requires_media,
    rms_db,
    run_chain,
    stream_durations,
)

LOSSLESS = ["-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-pix_fmt", "yuv420p", "-r", "30"]
FRAME = 1.0 / FPS


def request(p, **kwargs) -> fx_chain.FxRequest:
    params = {"plan": p, "words": [], "caption": None}
    params.update(kwargs)
    return fx_chain.FxRequest(**params)


# ----------------------------------------------------------------------------- pure: scene and commands

def test_build_scene_censors_words_under_bleeps_only_with_word_timing():
    words = [{"id": "w0", "start": 0.0, "end": 0.4, "text": "hello"},
             {"id": "w1", "start": 0.5, "end": 0.9, "text": "shit!"}]
    bleep = cue("bleep", 0.5, 0.9, tone=True, cover="none")
    scene = fx_chain.build_scene(request(plan(duration=2.0, cues=[bleep]), words=words), out_w=1080, out_h=1920,
                                 warp=identity_warp(2.0), face_track=None, matte_windows=[])
    assert [w.text for w in scene.words] == ["hello", "s***!"]
    assert [w.run for w in scene.words] == ["clip", "clip"]
    approx = fx_chain.build_scene(request(plan(duration=2.0, cues=[bleep], timing="approx"), words=words),
                                  out_w=1080, out_h=1920, warp=identity_warp(2.0), face_track=None, matte_windows=[])
    assert [w.text for w in approx.words] == ["hello", "shit!"]


def test_build_scene_places_a_picture_face_track_in_the_output_frame():
    row = [0.5, 0.5, 0.2, 0.1, 0.9, 0.4, 0.5, 0.6, 0.5, 0.5, 0.55, 0.45, 0.58, 0.55, 0.58]
    track = {"version": 1, "space": "picture", "fps": 6, "samples": [[0.0, [row]], [1.0, []]]}
    scene = fx_chain.build_scene(request(plan(duration=2.0), picture_rect=(0.0, 0.25, 1.0, 0.5)), out_w=1080,
                                 out_h=1920, warp=identity_warp(2.0), face_track=track, matte_windows=[])
    assert scene.face_track is not None and scene.face_track.space == "output"
    placed = scene.face_track.samples[0][1][0]
    assert placed[0] == pytest.approx(0.5) and placed[1] == pytest.approx(0.25 + 0.5 * 0.5)
    assert placed[2] == pytest.approx(0.2) and placed[3] == pytest.approx(0.05)
    assert placed[4] == pytest.approx(0.9)                         # score untouched
    assert placed[5] == pytest.approx(0.4) and placed[6] == pytest.approx(0.25 + 0.5 * 0.5)   # landmark x, y
    assert scene.face_track.samples[1][1] == ()
    # Identity rect: the same rows, re-tagged as output space.
    same = fx_chain.build_scene(request(plan(duration=2.0)), out_w=1080, out_h=1920, warp=identity_warp(2.0),
                                face_track=track, matte_windows=[])
    assert same.face_track.space == "output" and same.face_track.samples[0][1][0][1] == pytest.approx(0.5)
    # Unreadable track: no faces, no failure.
    broken = fx_chain.build_scene(request(plan(duration=2.0)), out_w=1080, out_h=1920, warp=identity_warp(2.0),
                                  face_track={"samples": "nope"}, matte_windows=[])
    assert broken.face_track is None


def test_build_scene_keeps_warp_cues_only_when_the_warp_is_applied(tmp_path):
    gif = tmp_path / "r.mp4"
    gif.write_bytes(b"x")
    cues = [cue("freeze", 1.0, hold=0.5, label="WAIT"), cue("speed", 0.2, 0.6, rate=2.0),
            cue("reaction", 0.2, 1.0, query="wow", id="rx"), cue("reaction", 1.2, 1.8, query="lol", id="ry"),
            cue("flash", 0.1, 0.3)]
    p = plan(duration=2.0, cues=cues)
    req = request(p, reaction_files={"rx": gif}, hook_window=(0.12, 1.9), broll_windows=((1.0, 1.5),))
    warped = fx_chain.build_scene(req, out_w=360, out_h=640, warp=build_warp(p.cues, 2.0), face_track=None,
                                  matte_windows=[MatteWindow(start=1.0, end=1.4, person=True),
                                                 MatteWindow(start=0.1, end=0.3, person=False)])
    kinds = [c.id for c in warped.cues]
    assert "rx" in kinds and "ry" not in kinds                      # no file → no PiP → no frame
    assert any(c.kind == "freeze" for c in warped.cues) and any(c.kind == "speed" for c in warped.cues)
    assert [c.start for c in warped.cues] == sorted(c.start for c in warped.cues)
    assert warped.warp and warped.out_duration == pytest.approx(build_warp(p.cues, 2.0).out_duration)
    assert [m.start for m in warped.matte_windows] == [0.1, 1.0]
    assert warped.hook_window == (0.12, 1.9) and warped.broll_windows == ((1.0, 1.5),)
    assert warped.progress_color == "#FF7A1A" and warped.seed == p.seed
    flat = fx_chain.build_scene(req, out_w=360, out_h=640, warp=identity_warp(2.0), face_track=None, matte_windows=[])
    assert not any(c.kind in ("freeze", "speed") for c in flat.cues)
    assert flat.warp == () and flat.out_duration == 2.0


def test_p1_command_labels_colour_and_matte(tmp_path):
    cmd = fx_chain.build_p1_command(
        input_path=Path("in.mp4"), head="[0:v]scale=360:640,setsar=1[base]", head_label="[base]", out_w=360,
        out_h=640, broll_overlays=[], broll_inputs=[], tail="setsar=1", color=("smpte170m", "pc"),
        reaction_args=[], reaction_graph="", reaction_label=None, matte_input=Path("m.mkv"),
        matte_filter="trim=start=1:end=2,setpts=PTS-STARTPTS", in_frames=61,
    )
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "[base]setsar=1,scale=in_color_matrix=smpte170m:in_range=pc,fps=30,format=rgba[fxp_rgb]" in graph
    assert "[1:v]trim=start=1:end=2,setpts=PTS-STARTPTS,scale=360:640:flags=bilinear,fps=30,format=gray[fxp_mk]" in graph
    assert graph.endswith("[fxp_rgb][fxp_mk]alphamerge[fxp_v]")
    assert cmd[-5:] == ["-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"]
    assert cmd[cmd.index("-frames:v") + 1] == "61" and "-an" in cmd
    no_matte = fx_chain.build_p1_command(
        input_path=Path("in.mp4"), head="[0:v]null[base]", head_label="[base]", out_w=360, out_h=640,
        broll_overlays=[], broll_inputs=[], tail="", color=("bt709", "tv"), reaction_args=[], reaction_graph="",
        reaction_label=None, matte_input=None, matte_filter=None, in_frames=10,
    )
    assert no_matte[no_matte.index("-filter_complex") + 1].endswith(
        "[base]scale=in_color_matrix=bt709:in_range=tv,fps=30,format=rgba[fxp_v]")


def test_reaction_pip_covers_reaction_rect_after_broll(tmp_path):
    gif = tmp_path / "r.mp4"
    gif.write_bytes(b"x")
    p = plan(duration=3.0, cues=[cue("reaction", 0.5, 1.5, query="wow", id="rx")])
    args, graph, label = fx_chain.reaction_pip(request(p, reaction_files={"rx": gif}), p.cues, base_label="[withbroll0]",
                                               first_input=2, out_w=1080, out_h=1920)
    assert args == ["-stream_loop", "-1", "-t", "1.500", "-i", str(gif)]
    assert "[2:v]settb=AVTB,setpts=PTS-STARTPTS+0.500/TB,scale=756:566:force_original_aspect_ratio=increase" in graph
    assert "[withbroll0][fxp_r0]overlay=162:384:eof_action=pass:enable='between(t,0.500,1.500)'[fxp_rx0]" in graph
    assert label == "[fxp_rx0]"


def test_p2_command_uses_the_working_deno_argv(monkeypatch, tmp_path):
    monkeypatch.setenv("DENO_DIR", "/cache/deno")
    monkeypatch.setenv("FX_DENO_BIN", "/opt/deno")
    cmd = fx_chain.build_p2_command(tmp_path / "job.json", [tmp_path, Path("/app/fonts"), tmp_path])
    assert cmd[:5] == ["/opt/deno", "run", "--cached-only", "--frozen", "--no-prompt"]
    read = next(a for a in cmd if a.startswith("--allow-read="))
    assert read == f"--allow-read={tmp_path},/app/fonts,/usr/share/fonts,/usr/bin/ldd,/cache/deno"
    assert "--allow-env=DENO_DIR,FX_DEBUG,NAPI_RS_NATIVE_LIBRARY_PATH,NAPI_RS_FORCE_WASI,DISABLE_SYSTEM_FONTS_LOAD" in cmd
    assert "--allow-sys=homedir" in cmd and "--allow-ffi" in cmd
    assert cmd[-3:] == [str(fx_chain.FX_MAIN_TS), "render", str(tmp_path / "job.json")]


def test_p3_command_tags_colour_and_bounds_frames(tmp_path):
    cmd = fx_chain.build_p3_command(
        video_input=["-f", "rawvideo", "-i", "pipe:0"], input_path=Path("in.mp4"), bus_path=Path("bus.wav"),
        subs="subtitles=filename=a.ass", audio_graph="[1:a:0]anull[fxa_voice0];[fxa_voice0]anull[fxa_out]",
        encode_args=None, out_frames=75, output_path=tmp_path / "o.mp4",
    )
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert graph.startswith(
        "[0:v]subtitles=filename=a.ass,setsar=1,scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,"
        "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709[fxv_out];")
    for flag, value in (("-colorspace", "bt709"), ("-color_primaries", "bt709"), ("-color_trc", "bt709"),
                        ("-color_range", "tv"), ("-frames:v", "75"), ("-c:a", "aac"), ("-b:a", "192k"),
                        ("-ar", "48000"), ("-movflags", "+faststart")):
        assert cmd[cmd.index(flag) + 1] == value
    assert cmd.count("-map_chapters") == 1
    assert cmd[cmd.index("in.mp4") - 1] == "-i" and cmd[cmd.index("bus.wav") - 1] == "-i"
    assert "[fxa_out]" in cmd and "-t" not in cmd
    silent = fx_chain.build_p3_command(
        video_input=["-i", "pipe:0"], input_path=Path("in.mp4"), bus_path=None, subs=None, audio_graph=None,
        encode_args=["-c:v", "libx264"], out_frames=10, output_path=tmp_path / "o.mp4",
    )
    assert "-an" in silent and "[fxa_out]" not in silent and "-c:a" not in silent
    assert silent.count("-map_chapters") == 1


def test_p3_forces_the_chain_rate_over_a_source_rate_encode(tmp_path):
    """`original` output of a 60 fps upload encodes with -r 60; the chain is 30 fps end to end, so P3 must not
    duplicate frames (with -frames:v outFrames that would cut the clip at half its length)."""
    from src.media.ffmpeg import build_final_video_encode_args

    cmd = fx_chain.build_p3_command(
        video_input=["-i", "pipe:0"], input_path=Path("in.mp4"), bus_path=None, subs=None, audio_graph=None,
        encode_args=build_final_video_encode_args(fps="60000/1001", level="4.2"), out_frames=10,
        output_path=tmp_path / "o.mp4",
    )
    rates = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "-r"]
    assert rates == ["30"]
    assert cmd[cmd.index("-level") + 1] == "4.2"


@requires_media
def test_safe_mode_never_loops_the_voice(tmp_path, chain_deps, monkeypatch):
    """At level safe P2 does not draw the frame0 crossfade, so the audio graph gets no loop crossfade either."""
    seen: list[float] = []
    real_graph = fx_chain._audio_module().audio_graph

    def spy(**kwargs):
        seen.append(kwargs["loop_crossfade"])
        return real_graph(**kwargs)

    audio = type("Audio", (), {})()
    for name in ("build_bus", "measure_voice_lufs"):
        setattr(audio, name, getattr(fx_chain._audio_module(), name))
    audio.audio_graph = spy
    monkeypatch.setattr(fx_chain, "_audio_module", lambda: audio)
    source = make_source(tmp_path / "src.mp4", dur=1.0)
    p = plan(duration=1.0, loop_ending="crossfade")
    assert run_chain(source, tmp_path / "safe.mp4", request(p), mode="safe").ok
    assert run_chain(source, tmp_path / "full.mp4", request(p)).ok
    assert seen == [0.0, fx_chain.LOOP_CROSSFADE_SECONDS]


@requires_media
def test_unrounded_edit_duration_keeps_audio_exactly_as_long_as_the_video(tmp_path, chain_deps):
    source = make_source(tmp_path / "src.mp4", dur=2.0)
    p = plan(duration=1.0 + 0.4 / FPS, cues=[cue("sfx", 0.3, sound="pop")])     # 30.4 frames → 30 frames
    result = run_chain(source, tmp_path / "out.mp4", request(p))
    assert result.ok, result.error
    assert probe(tmp_path / "out.mp4")["frames"] == 30
    video, audio = stream_durations(tmp_path / "out.mp4")
    assert abs(video - audio) < 0.005


def test_font_paths_cover_drawer_stems_and_the_caption_font():
    caption = FxCaptionStyle(template="slam", font="Anton-Regular", size_px=80, color="#FFFFFF", accent="#FFD400",
                             y=0.7)
    fonts = fx_chain._font_paths(caption, None)
    for stem in (*fx_chain.DRAWER_FONT_STEMS, "Anton-Regular"):
        assert stem in fonts and Path(fonts[stem]).is_file()


# ----------------------------------------------------------------------------- real chain

@requires_media
def test_identity_render_frame_count_av_and_progress(tmp_path, chain_deps):
    source = make_source(tmp_path / "src.mp4", dur=2.0)
    seen: list[float] = []
    p = plan(duration=2.0, cues=[cue("sfx", 0.5, sound="whoosh")])
    result = run_chain(source, tmp_path / "out.mp4", request(p), on_progress=seen.append)
    assert result.ok, result.error
    assert result.level == "full" and result.applied_warp == [] and result.out_duration == pytest.approx(2.0)
    assert set(result.timings) >= {"p1", "p2", "p3", "bus", "total"}
    info = probe(tmp_path / "out.mp4")
    assert info["frames"] == 60
    video, audio = stream_durations(tmp_path / "out.mp4")
    assert audio is not None and abs(video - audio) < FRAME
    assert seen and seen[-1] == 1.0
    assert not list((tmp_path / "scratch").glob("fxchain-*"))       # per-render files are removed


@requires_media
def test_freeze_adds_the_hold_and_repeats_one_frame(tmp_path, chain_deps):
    source = make_source(tmp_path / "src.mp4", dur=2.0)
    p = plan(duration=2.0, cues=[cue("freeze", 1.0, hold=0.5, label="")])
    result = run_chain(source, tmp_path / "out.mp4", request(p), encode_args=LOSSLESS)
    assert result.ok, result.error
    assert result.applied_warp and result.out_duration == pytest.approx(2.5)
    frames = frames_rgb(tmp_path / "out.mp4", 360, 640)
    assert abs(len(frames) - 75) <= 1
    hashes = [hashlib.sha1(f.tobytes()).hexdigest() for f in frames]
    # F 1.0 .. 1.5 holds E 1.0 (input frame 30), and output frame 45 (F 1.5, E 1.0) shows it once more. The freeze
    # drawer flashes a white rim over the first two hold frames, so the frame is constant from F 1.0 + 2 frames.
    frozen = hashes[32:46]
    assert len(set(frozen)) == 1
    assert hashes[29] != frozen[0] and hashes[46] != frozen[0]        # testsrc2 moves before and after the hold
    video, audio = stream_durations(tmp_path / "out.mp4")
    assert abs(video - audio) < FRAME


@requires_media
def test_speed_span_shortens_the_output(tmp_path, chain_deps):
    source = make_source(tmp_path / "src.mp4", dur=2.0)
    p = plan(duration=2.0, cues=[cue("speed", 0.5, 1.5, rate=2.0)])
    result = run_chain(source, tmp_path / "out.mp4", request(p))
    assert result.ok, result.error
    assert probe(tmp_path / "out.mp4")["frames"] == 45                # 0.5 + 1.0/2 + 0.5 seconds
    video, audio = stream_durations(tmp_path / "out.mp4")
    assert abs(video - audio) < FRAME


@requires_media
def test_warp_off_renders_identity(tmp_path, chain_deps, monkeypatch):
    monkeypatch.setenv("FX_WARP", "off")
    source = make_source(tmp_path / "src.mp4", dur=1.0)
    p = plan(duration=1.0, cues=[cue("freeze", 0.5, hold=0.5)])
    result = run_chain(source, tmp_path / "out.mp4", request(p))
    assert result.ok and result.applied_warp == [] and probe(tmp_path / "out.mp4")["frames"] == 30


@requires_media
def test_p1_shorter_than_the_plan_keeps_av_within_a_frame(tmp_path, chain_deps):
    source = make_source(tmp_path / "src.mp4", dur=2.0)
    p = plan(duration=2.0 + 2 * FRAME, cues=[cue("sfx", 1.0, sound="pop")])
    result = run_chain(source, tmp_path / "out.mp4", request(p))
    assert result.ok, result.error
    assert probe(tmp_path / "out.mp4")["frames"] == 62                 # P2 repeats the last frame (AR-M13)
    video, audio = stream_durations(tmp_path / "out.mp4")
    assert abs(video - audio) < FRAME


@requires_media
def test_safe_mode_runs_without_deno_and_keeps_the_bus(tmp_path, chain_deps, monkeypatch):
    monkeypatch.setenv("FX_DENO_BIN", str(tmp_path / "no-such-deno"))
    source = make_source(tmp_path / "src.mp4", dur=2.0, audio="silence")
    p = plan(duration=2.0, cues=[cue("sfx", 1.0, sound="impact"), cue("freeze", 0.5, hold=0.5), cue("flash", 0.2, 0.4)])
    result = run_chain(source, tmp_path / "out.mp4", request(p), mode="safe")
    assert result.ok, result.error
    assert result.level == "safe" and result.applied_warp == []
    assert probe(tmp_path / "out.mp4")["frames"] == 60                 # no warp in safe mode
    samples = decode_audio(tmp_path / "out.mp4")
    assert rms_db(samples[int(1.0 * 48000): int(1.1 * 48000)]) > -40  # the SFX is in the mix
    video, audio = stream_durations(tmp_path / "out.mp4")
    assert abs(video - audio) < FRAME


@requires_media
def test_silent_source_without_bus_keeps_an(tmp_path, chain_deps):
    source = make_source(tmp_path / "src.mp4", dur=1.0, audio=None)
    result = run_chain(source, tmp_path / "out.mp4", request(plan(duration=1.0, cues=[cue("flash", 0.2, 0.4)])))
    assert result.ok, result.error
    assert probe(tmp_path / "out.mp4")["audio"] is None


@requires_media
def test_silent_source_with_bus_gets_audio(tmp_path, chain_deps):
    source = make_source(tmp_path / "src.mp4", dur=1.0, audio=None)
    result = run_chain(source, tmp_path / "out.mp4", request(plan(duration=1.0, cues=[cue("sfx", 0.5, sound="pop")])),
                       has_audio=False)
    assert result.ok, result.error
    samples = decode_audio(tmp_path / "out.mp4")
    assert rms_db(samples[int(0.5 * 48000): int(0.6 * 48000)]) > -40
    video, audio = stream_durations(tmp_path / "out.mp4")
    assert abs(video - audio) < FRAME


@requires_media
def test_file_chain_mode_produces_the_same_frames(tmp_path, chain_deps, monkeypatch):
    source = make_source(tmp_path / "src.mp4", dur=1.0)
    p = plan(duration=1.0, cues=[cue("freeze", 0.5, hold=0.3)])
    monkeypatch.setenv("FX_CHAIN_MODE", "file")
    result = run_chain(source, tmp_path / "out.mp4", request(p))
    assert result.ok, result.error
    assert probe(tmp_path / "out.mp4")["frames"] == 39
    assert "q" in result.timings or result.timings.get("p3")


@requires_media
def test_720p_cap_after_fx_keeps_audio(tmp_path, chain_deps):
    source = make_source(tmp_path / "src.mp4", w=1080, h=1920, dur=1.0)
    p = plan(duration=1.0, cues=[cue("sfx", 0.3, sound="pop")])
    result = run_chain(source, tmp_path / "out.mp4", request(p), w=1080, h=1920)
    assert result.ok, result.error
    assert cap_video_short_side(tmp_path / "out.mp4", 720)
    info = probe(tmp_path / "out.mp4")
    assert (int(info["video"]["width"]), int(info["video"]["height"])) == (720, 1280)
    assert info["audio"] is not None
    assert rms_db(decode_audio(tmp_path / "out.mp4")) > -60


# ----------------------------------------------------------------------------- every cue kind draws

DRAWN = [
    # (label, cue, window) — windows are disjoint so one render covers them all
    ("flash", cue("flash", 0.10, 0.40, color="#FFFFFF", peak=0.9), (0.15, 0.30)),
    ("zoom", cue("zoom", 0.50, 0.90, mode="punch", scale=1.3, anchor="center"), (0.55, 0.85)),
    ("shake", cue("shake", 1.00, 1.40, amp=30, freq=12), (1.05, 1.30)),
    ("glitch", cue("glitch", 1.50, 1.80, amount=1.0), (1.52, 1.75)),
    ("desaturate", cue("desaturate", 1.90, 2.30, amount=1.0, dim=0.3), (1.95, 2.25)),
    ("transition", cue("transition", 2.60, style="whip"), (2.55, 2.65)),
    ("sticker", cue("sticker", 2.90, 3.40, sticker="wait_for_it"), (3.05, 3.35)),
    ("accent_slam", cue("accent", 3.50, 3.95, variant="slam", text="BOOM"), (3.60, 3.90)),
    ("accent_counter", cue("accent", 4.05, 4.50, variant="counter", text="1200", value=1200.0, prefix="$"),
     (4.20, 4.45)),
    ("bleep_cover", cue("bleep", 4.60, 4.95, tone=False, cover="bar"), (4.65, 4.90)),
    ("reaction", cue("reaction", 5.05, 5.50, query="wow", id="rx"), (5.15, 5.45)),
]


def _face_track(duration: float) -> dict:
    # A centred face with YuNet landmarks (right eye, left eye, nose, right mouth, left mouth), output space.
    row = [0.35, 0.30, 0.30, 0.18, 0.95, 0.43, 0.36, 0.57, 0.36, 0.50, 0.40, 0.44, 0.44, 0.56, 0.44]
    return {"version": 1, "space": "picture", "fps": 6,
            "samples": [[round(i / 6, 4), [row]] for i in range(int(duration * 6) + 2)]}


@requires_media
def test_each_cue_kind_draws_in_its_window(tmp_path, chain_deps):
    """§16: each cue kind renders FX pixels in its window (vs the same source rendered with no cues). Audio-only and
    timing kinds are covered elsewhere (sfx: bus energy; speed/freeze: frame counts)."""
    duration = 5.7
    w, h = 360, 640
    source = make_source(tmp_path / "src.mp4", w=w, h=h, dur=duration)
    reaction = make_source(tmp_path / "gif.mp4", w=200, h=150, dur=1.0, video="0xFF00FF", audio=None)
    face = _face_track(duration)

    base = run_chain(source, tmp_path / "base.mp4",
                     request(plan(duration=duration), face_track=face), encode_args=LOSSLESS)
    assert base.ok, base.error
    p = plan(duration=duration, cues=[c for _, c, _ in DRAWN])
    result = run_chain(source, tmp_path / "fx.mp4",
                       request(p, face_track=face, reaction_files={"rx": reaction}), encode_args=LOSSLESS)
    assert result.ok, result.error

    plain = frames_rgb(tmp_path / "base.mp4", w, h).astype(np.int16)
    drawn = frames_rgb(tmp_path / "fx.mp4", w, h).astype(np.int16)
    assert plain.shape == drawn.shape
    quiet = [n for n in range(len(plain)) if not any(a - 0.25 <= n / FPS <= b + 0.6 for _, c, (a, b) in DRAWN)]
    missing = []
    for label, _, (a, b) in DRAWN:
        frames = range(int(math.ceil(a * FPS)), int(b * FPS) + 1)
        # Share of pixels visibly changed (summed RGB difference > 48): a mouth bar covers well under 1 % of the
        # frame, a flash all of it.
        changed = max(float((np.abs(drawn[n] - plain[n]).sum(axis=2) > 48).mean()) for n in frames)
        if changed < 0.001:
            missing.append(f"{label} ({changed:.4%} changed)")
    assert not missing, f"cue kinds that drew nothing: {missing}"
    for n in quiet:                                                    # idle frames are passed through untouched
        assert float(np.abs(drawn[n] - plain[n]).mean()) < 0.5, n
