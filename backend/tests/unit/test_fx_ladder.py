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
from src.edit_fx.contracts import EditStyle, FxPlan, ZoomCue
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
            report["kind"] = state.get("kind", "crop")
            if state.get("panel_windows"):
                report["panel_windows"] = state["panel_windows"]
            for key in ("interview_segments", "hook_center_y"):
                if state.get(key) is not None:
                    report[key] = state[key]
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
    monkeypatch.setattr(
        video_utils, "_resolve_source_captions",
        lambda *_a, **_k: (state.get("band"), "avoid" if state.get("band") else "none"),
    )
    monkeypatch.setattr("src.media.source_captions.detect_rendered_caption_band",
                        lambda *_args: state.get("band"), raising=False)
    monkeypatch.setattr("src.media.reframing.caption_probe_plan", lambda *_args: ("null", "vf"), raising=False)
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
    # fx-core draws the title, so libass has nothing to burn.
    assert ladder.calls["ass"] == [] and render["subtitle_ass_path"] is None
    hook = request.hook
    assert (hook.text, hook.font, hook.look, hook.start, hook.end) == ("The craziest day", "TikTokSans-Bold", "bold", 0.12, 4.0)
    assert hook.template == "marker_circle"
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


def test_fx_runtime_legacy_renders_every_clip_through_the_legacy_render(ladder, monkeypatch):
    """The operator brake: no FX chain at all (not even the safe level's audio bus), Ken Burns restored, the FX
    template's classic fallback burned, and the clip marked level none."""
    monkeypatch.setenv("FX_RUNTIME", "legacy")
    out = ladder.run(fx_plan())
    assert out["ok"] and out["written"] and out["report"]["fx_level"] == "none"
    assert not fx_renders(ladder.calls)
    assert [r["vertical_plan"] for r in ladder.calls["render"]] == [(KB_CHAIN, "vf")]
    legacy_ass = ladder.calls["ass"][-1]["kwargs"]
    assert legacy_ass["template_override"] == get_template(get_template("marker_circle")["ass_fallback"])
    assert not [event for event, _props in ladder.calls["events"] if event == "fx_render_failed"]


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
    assert ladder.calls["ass"] == []                     # captions and the title are fx-core's on both renders
    assert first["fx_request"].hook == second["fx_request"].hook is not None
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


def test_interview_reframes_stay_off_the_stacked_two_shots(ladder):
    ladder.state.update(kind="interview", panel_windows=[(0.0, 3.0), (6.0, 8.0)])
    ladder.state["chain_results"] = [result()]
    zooms = [ZoomCue(id=f"z{i}", start=t, end=t + 1.0, mode="punch", scale=1.15) for i, t in enumerate((1.0, 4.0, 6.5))]
    ladder.run(fx_plan(cues=zooms))
    sent = [c.id for c in fx_renders(ladder.calls)[0]["fx_request"].plan.cues if c.kind == "zoom"]
    assert sent == ["z1"]


@pytest.mark.parametrize("kind", ["reaction", "stack", "overlap", "gaming"])
def test_panel_layouts_get_no_automatic_reframes(ladder, kind):
    """A camera over gameplay (``gaming``) is panels for the whole clip, like the others: a zoom of the frame
    would cross the seam. Zooms made by hand and everything else stay."""
    ladder.state.update(kind=kind)
    ladder.state["chain_results"] = [result()]
    cues = [ZoomCue(id="z0", start=1.0, end=2.0, mode="punch", scale=1.15),
            ZoomCue(id="u0", start=4.0, end=5.0, mode="punch", scale=1.15, origin="user"),
            {"kind": "sfx", "id": "e0.sfx", "start": 1.0, "end": 1.0, "sound": "pop"}]
    ladder.run(fx_plan(intensity="bold", cues=cues))
    sent = fx_renders(ladder.calls)[0]["fx_request"].plan.cues
    assert [c.id for c in sent if c.kind == "zoom"] == ["u0"]
    assert [c.id for c in sent if c.kind == "sfx"] == ["e0.sfx"]


def test_the_libass_hook_title_of_a_stacked_opening_sits_on_the_seam():
    assert video_utils._interview_hook_kwargs({}) == {}
    assert video_utils._interview_hook_kwargs({"hook_center_y": 0.5}) == {"hook_center_y": 0.5}


def test_the_drawn_hook_title_follows_the_template_and_the_intensity(ladder):
    def hook_of(plan, template, **kwargs):
        ladder.state["chain_results"] = [result()]
        ladder.calls["render"].clear()
        ladder.run(plan, template=template, **kwargs)
        return fx_renders(ladder.calls)[0]["fx_request"].hook

    # Listed styles: Poppins ExtraBold in sentence case whatever the look, the look's keyword colour as the marker.
    bold = hook_of(fx_plan(template="highlighter_swipe"), "highlighter_swipe")
    assert (bold.font, bold.look, bold.accent, bold.text) == ("Poppins-ExtraBold", "bold", "#34FD87", "The craziest day")
    loud = hook_of(fx_plan(template="emoji_drop", intensity="max"), "emoji_drop")
    assert (loud.font, loud.look, loud.text) == ("Poppins-ExtraBold", "max", "The craziest day")
    assert hook_of(fx_plan(template="emoji_drop", intensity="max", sensitivity="serious"), "emoji_drop").look == "subtle"
    assert hook_of(fx_plan(template="highlighter_swipe", intensity="off"), "highlighter_swipe").look == "bold"
    # An unlisted all-caps template keeps its font and case.
    slam = hook_of(fx_plan(template="slam", intensity="subtle"), "slam")
    assert (slam.font, slam.look, slam.text) == (get_template("slam")["font_family"], "subtle",
                                                 "THE CRAZIEST DAY" if get_template("slam").get("uppercase") else "The craziest day")
    # No title, or a suppressed one: nothing to draw.
    assert hook_of(fx_plan(template="highlighter_swipe"), "highlighter_swipe", hook=None) is None
    assert hook_of(fx_plan(template="slam", hook_title_suppressed=True), "slam") is None


def test_classic_captions_are_burned_by_libass_under_the_drawn_hook_title(ladder):
    """A classic template with edits: libass burns the captions after the drawing stage, fx-core draws the title."""
    from src.edit_fx.contracts import ZoomCue

    ladder.state["chain_results"] = [result()]
    zoom = ZoomCue(id="z1", start=1.0, end=2.0, mode="punch", scale=1.15)

    def build_ass(*args, **kwargs):
        ladder.calls["ass"].append({"args": args, "kwargs": kwargs})
        Path(args[5]).write_text("[Script Info]\n")
        if kwargs.get("caption_layout_report") is not None:
            kwargs["caption_layout_report"]["caption_y"] = 0.78
        return True

    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as patch:
        patch.setattr(video_utils, "build_assemblyai_ass_subtitles", build_ass)
        ladder.run(fx_plan(template="default", cues=[zoom]), template="default")
    (render,) = fx_renders(ladder.calls)
    assert render["fx_report"]["mode"] == "full"
    ass = ladder.calls["ass"][-1]["kwargs"]
    assert ass["include_captions"] is True and ass["hook_title"] is None
    hook = render["fx_request"].hook
    assert hook.font == get_template("default")["font_family"]


def test_a_plan_that_only_adds_sound_still_gets_the_drawn_title(ladder):
    ladder.state["chain_results"] = [result()]
    ladder.run(fx_plan(template="default"), template="default")
    (render,) = fx_renders(ladder.calls)
    assert render["fx_report"]["mode"] == "full"
    assert render["fx_request"].hook.text == "The craziest day"
    assert ladder.calls["ass"][-1]["kwargs"]["hook_title"] is None
    # Without a title there is nothing to draw: P1 → P3.
    ladder.state["chain_results"] = [result(level="safe")]
    ladder.calls["render"].clear()
    ladder.run(fx_plan(template="default"), template="default", hook=None)
    (render,) = fx_renders(ladder.calls)
    assert render["fx_report"]["mode"] == "safe" and render["fx_request"].hook is None


def test_without_the_drawing_stage_libass_burns_a_heavy_hook_title(ladder, monkeypatch):
    # The kill switch: the safe level, libass's title (heavy outlined type, no boxes).
    monkeypatch.setenv("FX_RUNTIME", "off")
    ladder.state["chain_results"] = [result(level="safe")]
    ladder.run(fx_plan(template="highlighter_swipe"), template="highlighter_swipe")
    (render,) = fx_renders(ladder.calls)
    assert render["fx_request"].hook is None
    ass = ladder.calls["ass"][-1]["kwargs"]
    assert ass["hook_title"] == "The craziest day" and ass["hook_heavy"] is True and ass["include_captions"] is True


def test_the_drawn_hook_title_ignores_the_layout(ladder):
    """Python hands fx-core no layout hints: a letterboxed or stacked opening changes nothing about the request."""
    ladder.run(fx_plan(template="highlighter_swipe"), template="highlighter_swipe")
    plain = fx_renders(ladder.calls)[0]["fx_request"].hook
    segments = [{"start": 0.0, "end": 2.0, "kind": "fit"}, {"start": 2.0, "end": 8.0, "kind": "pair"}]
    _interview(ladder, segments=segments, hook_center=0.5)
    ladder.run(fx_plan(template="highlighter_swipe"), template="highlighter_swipe")
    assert fx_renders(ladder.calls)[0]["fx_request"].hook == plain


# ----------------------------------------------------------------------------------------- interview caption rows

INTERVIEW = [  # pair → (flash) → single → pair → fit, stage-1 seconds
    {"start": 0.0, "end": 2.5, "kind": "pair"}, {"start": 2.5, "end": 2.9, "kind": "single"},
    {"start": 2.9, "end": 5.0, "kind": "single"}, {"start": 5.0, "end": 6.4, "kind": "pair"},
    {"start": 6.4, "end": 8.0, "kind": "fit"},
]


def _interview(ladder, *, segments=INTERVIEW, hook_center=None, band=None):
    ladder.state.update(kind="interview", interview_segments=segments, hook_center_y=hook_center, band=band,
                        panel_windows=[(s["start"], s["end"]) for s in segments if s["kind"] == "pair"])
    ladder.state["chain_results"] = [result()]
    ladder.calls["render"].clear()


def _caption(ladder):
    (render,) = fx_renders(ladder.calls)
    return render["fx_request"].caption


def test_interview_look_captions_get_a_row_per_shot(ladder):
    _interview(ladder)
    out = ladder.run(fx_plan(template="highlighter_swipe"), template="highlighter_swipe", hook=None)
    caption = _caption(ladder)
    assert caption.y == 0.5 and caption.look == "bold"
    # The 0.4 s close-up flash keeps the two-shot's row; the fit row clears the 16:9 picture band (0.34-0.66).
    assert [(r.start, r.end, r.y) for r in caption.rows] == [
        (0.0, 2.9, 0.5), (2.9, 5.0, 0.65), (5.0, 6.4, 0.5), (6.4, 8.0, 0.76)]
    assert out["report"]["caption_rows"][1] == {"start": 2.9, "end": 5.0, "y": 0.65}
    # The studio replays the shots: they are stored with the vertical layout.
    assert out["report"]["vertical_layout"]["segments"] == INTERVIEW
    assert out["report"]["caption_y"] == 0.5


@pytest.mark.parametrize("kind,y", [("pair", 0.5), ("single", 0.65), ("fit", 0.76)])
def test_a_clip_of_one_shot_kind_has_one_row(ladder, kind, y):
    _interview(ladder, segments=[{"start": 0.0, "end": 8.0, "kind": kind}])
    ladder.run(fx_plan(template="emoji_drop", intensity="max"), template="emoji_drop", hook=None)
    assert [(r.start, r.end, r.y) for r in _caption(ladder).rows] == [(0.0, 8.0, y)]


def test_rows_stay_clear_of_burned_in_source_captions(ladder):
    _interview(ladder, band=(0.62, 0.70))
    ladder.run(fx_plan(template="highlighter_swipe"), template="highlighter_swipe", hook=None)
    rows = _caption(ladder).rows
    px = 72 * 1.5  # the bold look's text height at 1080 wide
    for row in rows:
        top, bottom = row.y * 1920 - px / 2, row.y * 1920 + px / 2
        assert bottom <= 0.62 * 1920 or top >= 0.70 * 1920, row
    assert rows[1].y < 0.62                     # the close-up row moved above the band


def test_other_layouts_templates_and_the_off_path_get_no_rows(ladder):
    ladder.state["chain_results"] = [result()]
    ladder.state.update(interview_segments=INTERVIEW)                                     # shots, but not interview
    ladder.run(fx_plan(template="highlighter_swipe"), template="highlighter_swipe")       # a plain crop
    assert _caption(ladder).rows == ()
    _interview(ladder)
    ladder.run(fx_plan(), template="marker_circle")                                       # hidden template look
    assert _caption(ladder).rows == ()
    _interview(ladder)
    ladder.run(fx_plan(template="highlighter_swipe"), template="highlighter_swipe", add_subtitles=False)
    assert _caption(ladder) is None                                                       # captions off
    _interview(ladder)
    out = ladder.run(fx_plan(template="default", intensity="off"), template="default")    # legacy ASS captions
    assert _caption(ladder) is None and "caption_rows" not in out["report"]


def test_a_caption_edit_position_pins_the_captions(ladder):
    _interview(ladder, hook_center=0.5)
    override = {"words": WORDS, "position_y": 0.78}
    ladder.run(fx_plan(template="highlighter_swipe"), template="highlighter_swipe", caption_override=override)
    caption = _caption(ladder)
    assert caption.rows == () and caption.y == pytest.approx(0.78, abs=0.01)
    assert ladder.calls["ass"] == []                                       # the title is fx-core's


@pytest.mark.parametrize("opening", ["pair", "single", "fit"])
def test_rows_do_not_move_for_the_hook_title(ladder, opening):
    """fx-core places the title around the captions; the rows are the shots' own, hook or no hook."""
    segments = [{"start": 0.0, "end": 2.0, "kind": opening}, {"start": 2.0, "end": 3.5, "kind": "pair"},
                {"start": 3.5, "end": 8.0, "kind": "fit" if opening != "fit" else "single"}]
    _interview(ladder, segments=segments, hook_center=0.5)
    ladder.run(fx_plan(template="emoji_drop", intensity="max"), template="emoji_drop")
    with_hook = _caption(ladder).rows
    _interview(ladder, segments=segments, hook_center=0.5)
    ladder.run(fx_plan(template="emoji_drop", intensity="max"), template="emoji_drop", hook=None)
    assert with_hook == _caption(ladder).rows
    assert {round(r.y, 2) for r in with_hook} <= {0.5, 0.65, 0.76}


def test_a_short_clip_shows_the_hook_title_throughout(ladder):
    ladder.state["probe"] = 1.4
    ladder.state["chain_results"] = [result()]
    ladder.run(fx_plan(template="highlighter_swipe", duration=1.4), template="highlighter_swipe")
    request = fx_renders(ladder.calls)[0]["fx_request"]
    assert request.hook_window == (0.0, 1.4) and (request.hook.start, request.hook.end) == (0.0, 1.4)
    # 2.4 s: from 0.12 s until a quarter second before the end.
    ladder.state["probe"] = 2.4
    ladder.state["chain_results"] = [result()]
    ladder.calls["render"].clear()
    ladder.run(fx_plan(template="highlighter_swipe", duration=2.4), template="highlighter_swipe")
    request = fx_renders(ladder.calls)[0]["fx_request"]
    assert (request.hook.start, request.hook.end) == (0.12, pytest.approx(2.15))


def test_rows_are_on_the_edit_clock_with_a_warp(ladder):
    """Speed and freeze cues map E → F at draw time; rows, words and the hook window all stay on E."""
    from src.edit_fx.contracts import FreezeCue

    _interview(ladder, hook_center=0.5)
    freeze = FreezeCue(id="f", start=1.0, end=1.0, hold=1.0, origin="user")
    ladder.run(fx_plan(template="highlighter_swipe", cues=(freeze,)), template="highlighter_swipe")
    request = fx_renders(ladder.calls)[-1]["fx_request"]
    assert [(r.start, r.y) for r in request.caption.rows][:2] == [(0.0, 0.5), (2.9, 0.65)]
    assert request.hook_window == (0.12, 4.0)                              # E, mapped to F by the chain


def test_rows_scale_with_a_720p_output(ladder):
    from src.edit_fx.caption_rows import interview_caption_rows

    full = interview_caption_rows(INTERVIEW, text_px=72, width=1080, height=1920, source_size=(1920, 1080))
    small = interview_caption_rows(INTERVIEW, text_px=48, width=720, height=1280, source_size=(1920, 1080))
    assert [(r.start, r.y) for r in full] == [(r.start, r.y) for r in small]
