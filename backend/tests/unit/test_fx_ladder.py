"""WP14: the FX fallback ladder inside ``create_optimized_clip`` (FINAL §6.6), with the renderers mocked.

full → full without B-roll (P1 failure with cutaways) → identity-warp retry (A/V mismatch) → safe (ASS fallback,
original hook title, audio FX) → none (the legacy render with Ken Burns). Real renders live in test_fx_generation.py.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src import video_utils
from src.caption_templates import get_template
from src.edit_fx.contracts import EditStyle, FxPlan
from src.media.fx_chain import FxRenderInput, FxResult

PLAIN_CHAIN = "crop=608:1080:656:0,scale=1080:1920:flags=lanczos,setsar=1"
KB_CHAIN = "crop=608:1080:656:0,scale=1620:2880:flags=lanczos,fps=30,zoompan=z='1.05':d=1:s=1080x1920:fps=30,setsar=1"
WORDS = [
    {"id": "w0", "text": "We", "start": 0.2, "end": 0.4},
    {"id": "w1", "text": "made", "start": 0.45, "end": 0.7},
    {"id": "w2", "text": "money", "start": 0.75, "end": 1.1},
]


def fx_plan(*, template="marker_circle", intensity="bold", duration=8.0, cues=(), **extra) -> FxPlan:
    return FxPlan(style=EditStyle(intensity=intensity), template=template, seed=11, edit_duration=duration,
                  cues=tuple(cues), **extra)


def result(ok=True, *, error=None, level="full", warp=(), face=None) -> FxResult:
    return FxResult(ok=ok, width=1080, height=1920, level=level, out_duration=8.0, face_track=face,
                    applied_warp=list(warp), timings={"p1": 1.0, "p2": 2.0, "p3": 3.0}, error=error)


@pytest.fixture
def ladder(monkeypatch, tmp_path):
    """Run ``create_optimized_clip`` with every renderer mocked; ``chain_results`` scripts the fx chain."""
    state = {
        "probe": 8.0, "eligible": True, "chain_results": [], "av": [], "legacy_ok": [True],
        "src": (1920, 1080),
    }
    calls: dict[str, list] = {"plan": [], "ass": [], "render": [], "events": []}

    def build_plan(_path, _w, _h, _turns, _words, report=None, **kwargs):
        calls["plan"].append(dict(kwargs))
        allow = kwargs.get("allow_kenburns", True)
        if report is not None:
            report["kind"] = "crop"
            if not allow:
                report["kenburns_eligible"] = state["eligible"]
        return (PLAIN_CHAIN if not allow else KB_CHAIN), "vf", 0.72

    def build_ass(*args, **kwargs):
        calls["ass"].append({"args": args, "kwargs": kwargs})
        Path(args[5]).write_text("[Script Info]\n")
        return True

    def render(source, final_path, fmt, **kwargs):
        calls["render"].append({"format": fmt, **kwargs})
        if kwargs.get("fx_request") is not None:
            outcome = state["chain_results"].pop(0)
            kwargs["fx_report"]["result"] = outcome
            if outcome.ok:
                Path(final_path).write_bytes(b"fx")
            return outcome.ok, 1080, 1920
        ok = state["legacy_ok"].pop(0) if state["legacy_ok"] else True
        if ok:
            Path(final_path).write_bytes(b"legacy")
        return ok, 1080, 1920

    def source_render(_video, _ranges, out, **_kwargs):
        Path(out).write_bytes(b"source")
        return True

    monkeypatch.setattr(video_utils, "get_config", lambda: SimpleNamespace(speaker_framing=False))
    monkeypatch.setattr(video_utils, "render_source_ranges_ffmpeg", source_render)
    monkeypatch.setattr(video_utils, "ffprobe_video_size", lambda _path: state["src"])
    monkeypatch.setattr(video_utils, "ffprobe_duration", lambda _path: state["probe"])
    monkeypatch.setattr(video_utils, "probe_video_geometry", lambda _path: SimpleNamespace(
        display_width=state["src"][0], display_height=state["src"][1], fps=30.0))
    monkeypatch.setattr(video_utils, "build_vertical_filter_plan", build_plan)
    monkeypatch.setattr(video_utils, "detect_burned_caption_band", lambda _path: None)
    monkeypatch.setattr(video_utils, "build_assemblyai_ass_subtitles", build_ass)
    monkeypatch.setattr(video_utils, "render_reframed_clip_ffmpeg", render)
    monkeypatch.setattr(video_utils, "_av_mismatch", lambda _path: state["av"].pop(0) if state["av"] else False)
    monkeypatch.setattr(video_utils, "_capture_fx_event", lambda event, props: calls["events"].append((event, props)))
    monkeypatch.delenv("FX_RUNTIME", raising=False)
    monkeypatch.delenv("FX_WARP", raising=False)

    footage = tmp_path / "stock.mp4"
    footage.write_bytes(b"stock")

    def run(plan: FxPlan, *, template="marker_circle", hook="The craziest day", broll=False, fmt="vertical",
            add_subtitles=True, **kwargs):
        output = tmp_path / "out.mp4"
        output.unlink(missing_ok=True)
        report: dict = {}
        overlays = [{"path": str(footage), "start": 2.0, "duration": 2.0, "offset": 0.0}] if broll else None
        ok = video_utils.create_optimized_clip(
            tmp_path / "video.mp4", 10.0, 18.0, output, add_subtitles, None, None, "#FFFFFF", template, fmt,
            [(10.0, 18.0)], hook, extend_to_sentence=False, layout_report=report, broll_overlays=overlays,
            fx=FxRenderInput(plan=plan, words=list(WORDS)), **kwargs,
        )
        return {"ok": ok, "report": report, "written": output.exists()}

    return SimpleNamespace(run=run, state=state, calls=calls)


def fx_renders(calls):
    return [c for c in calls["render"] if c.get("fx_request") is not None]


def test_full_level_draws_fx_captions_and_records_the_render(ladder):
    ladder.state["chain_results"] = [result(face={"version": 1, "space": "picture", "fps": 6, "samples": []})]
    out = ladder.run(fx_plan())
    assert out["ok"] and out["written"]
    report = out["report"]
    assert report["fx_level"] == "full"
    assert report["applied_warp"] == []
    assert report["face_track"]["space"] == "picture"
    assert report["broll_applied"] is False
    # One render, through the chain, in full mode; the FX template's captions are fx-core's, not libass's.
    (render,) = fx_renders(ladder.calls)
    assert render["fx_report"]["mode"] == "full"
    request = render["fx_request"]
    assert request.caption is not None and request.caption.template == "marker_circle"
    assert request.hook_window == (0.12, 4.0)
    assert [w["text"] for w in request.words] == ["We", "made", "money"]
    assert all("highlight" in w for w in request.words)
    ass = ladder.calls["ass"][-1]["kwargs"]
    assert ass["include_captions"] is False and ass["hook_title"] == "The craziest day"
    assert "time_map" not in ass and "template_override" not in ass
    # FX path: never Ken Burns in the plan.
    assert ladder.calls["plan"] == [{"allow_kenburns": False}]
    assert ladder.calls["events"][-1][0] == "fx_render"


def test_hook_title_suppressed_only_at_full(ladder):
    plan = fx_plan(template="slam", hook_title_suppressed=True)
    ladder.state["chain_results"] = [result(False, error="p2: exit 1"), result(True, level="safe")]
    out = ladder.run(plan, template="slam")
    assert out["report"]["fx_level"] == "safe"
    # Full: no hook title and fx-core's captions, so nothing for libass to burn.
    assert len(ladder.calls["ass"]) == 1
    assert fx_renders(ladder.calls)[0]["subtitle_ass_path"] is None
    safe_ass = ladder.calls["ass"][0]["kwargs"]
    # Safe rebuilds the hook title and burns the template's ASS fallback captions (AR-M12).
    assert safe_ass["hook_title"] == "The craziest day"
    assert safe_ass["include_captions"] is True
    assert safe_ass["template_override"] == get_template(get_template("slam")["ass_fallback"])
    renders = fx_renders(ladder.calls)
    assert [r["fx_report"]["mode"] for r in renders] == ["full", "safe"]
    assert ladder.calls["events"][0][0] == "fx_render_failed" and ladder.calls["events"][0][1]["stage"] == "p2"


def test_p1_failure_with_broll_retries_full_without_broll(ladder):
    ladder.state["chain_results"] = [result(False, error="p1: exit 1: broken input"), result(True)]
    out = ladder.run(fx_plan(), broll=True)
    assert out["report"]["fx_level"] == "full"
    assert out["report"]["broll_applied"] is False
    first, second = fx_renders(ladder.calls)
    assert first["broll_overlays"] and second["broll_overlays"] is None
    assert first["fx_request"].broll_windows == ((2.0, 4.0),)
    assert second["fx_request"].broll_windows == ()


def test_a_p2_failure_with_broll_goes_to_safe_keeping_broll(ladder):
    ladder.state["chain_results"] = [result(False, error="p2: exit 1"), result(True, level="safe")]
    out = ladder.run(fx_plan(), broll=True)
    assert out["report"]["fx_level"] == "safe"
    assert out["report"]["broll_applied"] is True
    assert [bool(r["broll_overlays"]) for r in fx_renders(ladder.calls)] == [True, True]


def test_fx_runtime_off_goes_straight_to_safe(ladder, monkeypatch):
    monkeypatch.setenv("FX_RUNTIME", "off")
    ladder.state["chain_results"] = [result(True, level="safe")]
    out = ladder.run(fx_plan())
    assert out["report"]["fx_level"] == "safe"
    assert [r["fx_report"]["mode"] for r in fx_renders(ladder.calls)] == ["safe"]


def test_failing_safe_falls_back_to_the_legacy_render_with_ken_burns(ladder):
    ladder.state["chain_results"] = [result(False, error="p2: crash"), result(False, error="p3: exit 1")]
    out = ladder.run(fx_plan())
    assert out["ok"] and out["report"]["fx_level"] == "none"
    assert out["report"]["applied_warp"] == []
    legacy = ladder.calls["render"][-1]
    assert legacy.get("fx_request") is None and "fx_report" not in legacy
    # The vertical plan is rebuilt with Ken Burns allowed (the planner's default).
    assert ladder.calls["plan"] == [{"allow_kenburns": False}, {}]
    assert legacy["vertical_plan"] == (KB_CHAIN, "vf")
    assert out["report"]["vertical_layout"]["video_filter"] == KB_CHAIN
    legacy_ass = ladder.calls["ass"][-1]["kwargs"]
    assert legacy_ass["hook_title"] == "The craziest day"
    assert "time_map" not in legacy_ass and "censor_spans" not in legacy_ass


def test_none_level_keeps_the_broll_retry(ladder):
    ladder.state["chain_results"] = [result(False, error="p2: x"), result(False, error="p3: x")]
    ladder.state["legacy_ok"] = [False, True]
    out = ladder.run(fx_plan(), broll=True)
    assert out["report"]["fx_level"] == "none"
    assert out["report"]["broll_applied"] is False
    legacy = [c for c in ladder.calls["render"] if c.get("fx_request") is None]
    assert [bool(c["broll_overlays"]) for c in legacy] == [True, False]


def test_everything_failing_fails_the_clip(ladder):
    ladder.state["chain_results"] = [result(False, error="p2: x"), result(False, error="p3: x")]
    ladder.state["legacy_ok"] = [False]
    out = ladder.run(fx_plan())
    assert out["ok"] is False and not out["written"]


def test_cancelled_chain_does_not_fall_down_the_ladder(ladder):
    ladder.state["chain_results"] = [result(False, error="cancelled")]
    out = ladder.run(fx_plan())
    assert out["ok"] is False
    assert len(ladder.calls["render"]) == 1


WARP_CUES = (
    {"kind": "freeze", "id": "b2.freeze", "start": 3.0, "end": 3.0, "hold": 0.6},
    {"kind": "speed", "id": "b3.fast", "start": 5.0, "end": 6.0, "rate": 2.0},
    {"kind": "accent", "id": "b1.accent", "start": 1.0, "end": 2.0, "variant": "circle"},
)


def test_warped_render_maps_ass_times_and_records_the_applied_warp(ladder):
    warp = [{"e0": 0.0, "e1": 3.0, "o0": 0.0, "rate": 1.0, "hold": 0.0}]
    ladder.state["chain_results"] = [result(True, warp=warp)]
    out = ladder.run(fx_plan(cues=WARP_CUES), template="default")
    assert out["report"]["applied_warp"] == warp
    ass = ladder.calls["ass"][-1]["kwargs"]
    assert callable(ass["time_map"])
    assert ass["time_map"](4.0) == pytest.approx(4.6)          # after the 0.6 s hold
    assert ass["include_captions"] is True                     # a legacy template keeps its ASS captions


def test_av_mismatch_retries_full_with_an_identity_warp(ladder):
    warp = [{"e0": 0.0, "e1": 3.0, "o0": 0.0, "rate": 1.0, "hold": 0.0}]
    ladder.state["chain_results"] = [result(True, warp=warp), result(True, warp=())]
    ladder.state["av"] = [True]
    out = ladder.run(fx_plan(cues=WARP_CUES))
    assert out["report"]["fx_level"] == "full"
    assert out["report"]["applied_warp"] == []
    first, second = fx_renders(ladder.calls)
    assert {c.kind for c in first["fx_request"].plan.cues} >= {"freeze", "speed"}
    assert not {c.kind for c in second["fx_request"].plan.cues} & {"freeze", "speed"}
    assert "time_map" not in ladder.calls["ass"][-1]["kwargs"]
    # The sidecar plan keeps the warp cues as intent; applied_warp [] says they did not render.
    assert {c.kind for c in out["report"]["fx_plan"].cues} >= {"freeze", "speed"}


def test_fx_warp_off_renders_without_time_map(ladder, monkeypatch):
    monkeypatch.setenv("FX_WARP", "off")
    ladder.state["chain_results"] = [result(True)]
    ladder.run(fx_plan(cues=WARP_CUES), template="default")
    assert "time_map" not in ladder.calls["ass"][-1]["kwargs"]


def test_bleeps_censor_the_ass_only_with_word_timing(ladder):
    bleep = {"kind": "bleep", "id": "b4.bleep", "start": 0.45, "end": 0.7}
    ladder.state["chain_results"] = [result(True)]
    ladder.run(fx_plan(cues=(bleep,)), template="hormozi")
    assert ladder.calls["ass"][-1]["kwargs"]["censor_spans"] == [(0.45, 0.7)]
    ladder.state["chain_results"] = [result(True)]
    ladder.run(fx_plan(cues=(bleep,), timing="approx"), template="hormozi")
    assert "censor_spans" not in ladder.calls["ass"][-1]["kwargs"]


@pytest.mark.parametrize("eligible", [True, False])
def test_baseline_pushes_iff_kenburns_eligible(ladder, eligible):
    ladder.state["eligible"] = eligible
    ladder.state["chain_results"] = [result(True)]
    out = ladder.run(fx_plan(intensity="off"))       # style only: a 8 s clip with no zoom → one push if eligible
    pushes = [c for c in out["report"]["fx_plan"].cues if c.id.startswith("kb")]
    assert bool(pushes) is eligible
    assert out["report"]["kenburns_eligible"] is eligible
    if eligible:
        assert pushes[0].kind == "zoom" and pushes[0].mode == "push"


def test_no_baseline_pushes_when_zooms_are_off(ladder):
    ladder.state["chain_results"] = [result(True)]
    plan = FxPlan(style=EditStyle(intensity="subtle", zooms=False), template="marker_circle", seed=1,
                  edit_duration=8.0)
    out = ladder.run(plan)
    assert not [c for c in out["report"]["fx_plan"].cues if c.id.startswith("kb")]


def test_plan_is_clamped_to_the_probed_stage_one_length(ladder):
    ladder.state["probe"] = 6.0
    cues = (
        {"kind": "accent", "id": "b1.accent", "start": 5.5, "end": 7.0, "variant": "circle"},
        {"kind": "sfx", "id": "b2.sfx", "start": 6.5, "end": 6.5, "sound": "pop"},
        {"kind": "accent", "id": "b3.accent", "start": 6.2, "end": 7.5, "variant": "underline"},
    )
    ladder.state["chain_results"] = [result(True)]
    out = ladder.run(fx_plan(cues=cues, duration=8.0))
    plan = out["report"]["fx_plan"]
    assert plan.edit_duration == 6.0
    assert [(c.id, c.end) for c in plan.cues if not c.id.startswith("kb")] == [("b1.accent", 6.0)]


def test_original_format_has_no_ken_burns_and_no_vertical_planning(ladder):
    ladder.state["chain_results"] = [result(True)]
    out = ladder.run(fx_plan(intensity="off"), fmt="original")
    assert ladder.calls["plan"] == []
    assert out["report"]["kenburns_eligible"] is False
    assert not [c for c in out["report"]["fx_plan"].cues if c.id.startswith("kb")]
    (render,) = fx_renders(ladder.calls)
    assert render["format"] == "original"
    assert render["fx_request"].caption.size_px > 0


def test_caption_override_replaces_words_position_and_size(ladder):
    ladder.state["chain_results"] = [result(True)]
    override = {"words": [{"id": "w0", "text": "Edited", "start": 0.2, "end": 0.6}], "highlight_words": ["Edited"],
                "position_y": 0.5, "font_size": 40}
    ladder.run(fx_plan(), template="default", caption_override=override)
    ass = ladder.calls["ass"][-1]
    assert ass["kwargs"]["caption_words"] == override["words"]
    assert ass["kwargs"]["highlight_words"] == ["Edited"]
    assert ass["kwargs"]["position_y_override"] == 0.5
    assert ass["args"][7] == 40                                # the ASS font size
    request = fx_renders(ladder.calls)[0]["fx_request"]
    assert [(w["text"], w["highlight"]) for w in request.words] == [("Edited", True)]
    ladder.state["chain_results"] = [result(True)]
    ladder.run(fx_plan(), caption_override=override)
    caption = fx_renders(ladder.calls)[-1]["fx_request"].caption
    assert caption.size_px == video_utils.get_scaled_font_size(40, 1080)


def test_dark_text_templates_keep_their_dark_text_over_the_create_default(ladder):
    dark = {**get_template("marker_circle"), "font_color": "#111111", "background": True}
    assert video_utils.fx_font_color(dark, "#FFFFFF") is None
    assert video_utils.fx_font_color(dark, "#FF0000") == "#FF0000"
    assert video_utils.fx_font_color(get_template("marker_circle"), "#FFFFFF") == "#FFFFFF"
    # The highlighter's intensity look is white text on a dark pill (the look owns font, size and colours).
    ladder.state["chain_results"] = [result(True)]
    ladder.run(fx_plan(template="highlighter_swipe"), template="highlighter_swipe")
    caption = fx_renders(ladder.calls)[0]["fx_request"].caption
    assert caption.color == "#FFFFFF" and caption.look in ("subtle", "bold", "max")


def test_fx_junctions_are_crossfade_centres_and_passage_breaks():
    ranges = [(10.0, 14.0), (14.5, 18.0), (25.0, 30.0)]
    offsets = [0.0, 3.8, 7.1]                     # 0.2 s fades at both junctions
    cuts, breaks = video_utils.fx_junctions(ranges, offsets)
    assert cuts == [3.9, 7.2]
    assert breaks == [7.2]                         # the 7 s source gap starts a new passage
    cuts, breaks = video_utils.fx_junctions(ranges, offsets, group_output_starts=[0.0, 3.8, None])
    assert breaks == [3.9, 7.2]
    assert video_utils.fx_junctions([(0.0, 5.0)]) == ([], [])
    # A cold-open teaser jumps backwards: a passage break even without a gap.
    _, breaks = video_utils.fx_junctions([(30.0, 33.0), (10.0, 20.0)], [0.0, 3.0])
    assert breaks == [3.0]


def test_a_raising_chain_drops_one_level_not_the_clip(ladder, monkeypatch):
    """An exception inside the chain (not a reported failure) is a failed level, never a lost clip (§0.4.7)."""
    def explode_once():
        raise RuntimeError("unexpected")

    real = video_utils.render_reframed_clip_ffmpeg

    def render(source, final_path, fmt, **kwargs):
        if kwargs.get("fx_request") is not None and kwargs["fx_report"]["mode"] == "full":
            explode_once()
        return real(source, final_path, fmt, **kwargs)

    monkeypatch.setattr(video_utils, "render_reframed_clip_ffmpeg", render)
    ladder.state["chain_results"] = [result(level="safe")]
    out = ladder.run(fx_plan())
    assert out["ok"] and out["report"]["fx_level"] == "safe"
    failed = [props for event, props in ladder.calls["events"] if event == "fx_render_failed"]
    assert failed and failed[0]["stage"] == "setup"


def test_a_setup_error_goes_straight_to_the_legacy_render(ladder, monkeypatch):
    def broken(*_args, **_kwargs):
        raise ValueError("bad caption style")

    monkeypatch.setattr("src.edit_fx.caption_style.fx_caption_style", broken)
    out = ladder.run(fx_plan())
    assert out["ok"] and out["report"]["fx_level"] == "none"
    assert not fx_renders(ladder.calls)
    legacy_ass = ladder.calls["ass"][-1]["kwargs"]
    # The FX template's legacy fallback burns the captions.
    assert legacy_ass["template_override"] == get_template(get_template("marker_circle")["ass_fallback"])



@pytest.mark.parametrize("template", ["marker_circle", "hormozi"])
def test_without_fx_only_fx_templates_burn_their_ass_fallback(ladder, tmp_path, template):
    """FX planning failed upstream: an FX template still gets libass captions from its fallback; a legacy
    template's call is the legacy call (no template_override)."""
    ok = video_utils.create_optimized_clip(
        tmp_path / "video.mp4", 10.0, 18.0, tmp_path / "plain.mp4", True, None, None, "#FFFFFF", template,
        "vertical", [(10.0, 18.0)], "Hook", extend_to_sentence=False, layout_report={},
    )
    assert ok
    ass = ladder.calls["ass"][-1]["kwargs"]
    if template == "marker_circle":
        assert ass["template_override"] == get_template(get_template(template)["ass_fallback"])
    else:
        assert "template_override" not in ass
    assert ladder.calls["plan"] == [{}]
