"""WP14: ``allow_kenburns`` on the vertical planners and the FX routing of ``render_reframed_clip_ffmpeg``.

The FX path always plans without Ken Burns (fx-core draws an editable push instead, and the chain's analysis needs a
head without zoompan) and reports ``kenburns_eligible``; the default keeps every pinned string and key unchanged.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.media import fx_chain, reframing
from src.media.common import KENBURNS_MIN_SECONDS, VERTICAL_SCALE_CHAIN
from src.media.fx_chain import FxRequest, FxResult
from src.edit_fx.contracts import EditStyle, FxPlan


@pytest.fixture
def narrow(monkeypatch):
    """A portrait source: no horizontal room to crop, the static-fit branch."""
    state = {"duration": 12.0}
    monkeypatch.setattr(reframing, "ffprobe_duration", lambda _p: state["duration"])
    monkeypatch.setattr(reframing, "detect_optimal_crop_region", lambda *_a, **_k: (0, 0, 1080, 1920))
    return state


@pytest.fixture
def static_closeup(monkeypatch):
    """A wide source with a static, well-framed face: the tracked-crop branch that adds Ken Burns."""
    from src.media import reaction_layout, talking_head_layout

    state = {"duration": 12.0}
    monkeypatch.setattr(reframing, "ffprobe_duration", lambda _p: state["duration"])
    monkeypatch.setattr(reframing, "analyze_vertical_clip", lambda *_a, **_k: ([], [], [], []))
    monkeypatch.setattr(reframing, "build_layout_plan",
                        lambda _t, _c, d: [{"kind": "face", "start": 0.0, "end": d}])
    monkeypatch.setattr(reframing, "detect_optimal_crop_region", lambda *_a, **_k: (640, 0, 608, 1080))
    monkeypatch.setattr(reaction_layout, "find_reactor_face", lambda *_a, **_k: False)
    monkeypatch.setattr(reaction_layout, "choose_reaction_layout", lambda *_a, **_k: None)
    monkeypatch.setattr(talking_head_layout, "choose_talking_head_layout", lambda *_a, **_k: None)
    monkeypatch.setattr(talking_head_layout, "dominant_face_bottom", lambda *_a, **_k: None)
    return state


def test_narrow_source_default_keeps_ken_burns_and_its_keys(narrow, tmp_path):
    described = reframing.describe_vertical_layout(tmp_path / "in.mp4", 1080, 1920)
    assert "zoompan" in described["video_filter"]
    assert set(described) == {"kind", "video_filter", "mode", "caption_y"}


def test_narrow_source_without_ken_burns_reports_eligibility(narrow, tmp_path):
    described = reframing.describe_vertical_layout(tmp_path / "in.mp4", 1080, 1920, allow_kenburns=False)
    assert "zoompan" not in described["video_filter"]
    assert described["video_filter"] == f"crop=1080:1920:0:0,{VERTICAL_SCALE_CHAIN}"
    assert described["kenburns_eligible"] is True
    narrow["duration"] = KENBURNS_MIN_SECONDS - 0.5
    short = reframing.describe_vertical_layout(tmp_path / "in.mp4", 1080, 1920, allow_kenburns=False)
    assert short["kenburns_eligible"] is False
    # Too short for Ken Burns anyway: the same filter as the legacy plan.
    assert short["video_filter"] == reframing.describe_vertical_layout(tmp_path / "in.mp4", 1080, 1920)["video_filter"]


def test_static_closeup_without_ken_burns(static_closeup, tmp_path):
    legacy = reframing.describe_vertical_layout(tmp_path / "in.mp4", 1920, 1080)
    fx = reframing.describe_vertical_layout(tmp_path / "in.mp4", 1920, 1080, allow_kenburns=False)
    assert "zoompan" in legacy["video_filter"] and "kenburns_eligible" not in legacy
    assert "zoompan" not in fx["video_filter"]
    assert fx["video_filter"].startswith("crop=") and fx["video_filter"].endswith(f":0,{VERTICAL_SCALE_CHAIN}")
    assert legacy["video_filter"].split(",")[0] == fx["video_filter"].split(",")[0]
    assert fx["kenburns_eligible"] is True
    assert (fx["kind"], fx["mode"]) == (legacy["kind"], legacy["mode"])


def test_build_vertical_filter_plan_reports_eligibility_only_when_asked(narrow, tmp_path):
    report: dict = {}
    reframing.build_vertical_filter_plan(tmp_path / "in.mp4", 1080, 1920, report=report)
    assert report == {"kind": "crop"}
    report = {}
    video_filter, mode, _ = reframing.build_vertical_filter_plan(
        tmp_path / "in.mp4", 1080, 1920, report=report, allow_kenburns=False
    )
    assert report == {"kind": "crop", "kenburns_eligible": True}
    assert "zoompan" not in video_filter and mode == "vf"


def test_non_ken_burns_layouts_are_not_eligible(monkeypatch, tmp_path):
    from src.media import reaction_layout

    monkeypatch.setattr(reframing, "ffprobe_duration", lambda _p: 12.0)
    monkeypatch.setattr(reframing, "analyze_vertical_clip", lambda *_a, **_k: ([], [], [], []))
    monkeypatch.setattr(reframing, "build_layout_plan", lambda _t, _c, d: [{"kind": "fit", "start": 0.0, "end": d}])
    monkeypatch.setattr(reaction_layout, "find_reactor_face", lambda *_a, **_k: False)
    monkeypatch.setattr(reaction_layout, "choose_reaction_layout", lambda *_a, **_k: None)
    described = reframing.describe_vertical_layout(tmp_path / "in.mp4", 1920, 1080, allow_kenburns=False)
    assert described["kind"] == "fit"
    assert described["kenburns_eligible"] is False


# --------------------------------------------------------------------------- render_reframed_clip_ffmpeg FX routing

VF = "crop=608:1080:656:0,scale=1080:1920:flags=lanczos,setsar=1"
COMPLEX = "[0:v]split=2[a][b];[a]scale=1080:960[t];[b]scale=1080:960[u];[t][u]vstack[vout]"


@pytest.fixture
def reframe(monkeypatch, tmp_path):
    calls: dict[str, list] = {"chain": [], "ffmpeg": [], "plan": []}
    state = {"size": (1920, 1080), "audio": True, "speaker_plan": None, "ok": True}

    def run_chain(**kwargs):
        calls["chain"].append(kwargs)
        Path(kwargs["output_path"]).write_bytes(b"fx")
        return FxResult(ok=state["ok"], width=kwargs["out_w"], height=kwargs["out_h"], level=kwargs["mode"],
                        out_duration=5.0, face_track=None, applied_warp=[], timings={},
                        error=None if state["ok"] else "p2: exit 1")

    def build_plan(*args, **kwargs):
        calls["plan"].append(kwargs)
        return VF, "vf", None

    monkeypatch.setattr(fx_chain, "run_fx_chain", run_chain)
    monkeypatch.setattr(reframing, "run_ffmpeg_command",
                        lambda cmd: calls["ffmpeg"].append(cmd) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(reframing, "ffprobe_video_size", lambda _p: state["size"])
    monkeypatch.setattr(reframing, "ffprobe_has_audio", lambda _p: state["audio"])
    monkeypatch.setattr(reframing, "ffprobe_duration", lambda _p: 5.0)
    monkeypatch.setattr(reframing, "probe_video_geometry", lambda _p: SimpleNamespace(
        display_width=state["size"][0], display_height=state["size"][1], fps=30.0))
    monkeypatch.setattr(reframing, "detect_speaker_reframe_plan", lambda *_a, **_k: state["speaker_plan"])
    monkeypatch.setattr(reframing, "build_vertical_filter_plan", build_plan)
    monkeypatch.setattr(reframing.shutil, "copyfile", lambda *_a: pytest.fail("FX renders never stream-copy"))
    ass = tmp_path / "captions.ass"
    ass.write_text("[Script Info]\n")
    request = FxRequest(plan=FxPlan(style=EditStyle(intensity="bold"), template="default", seed=1,
                                    edit_duration=5.0), words=[], caption=None)

    def run(fmt, **kwargs):
        calls["chain"].clear()
        calls["ffmpeg"].clear()
        report = {"mode": kwargs.pop("mode", "full")}
        out = reframing.render_reframed_clip_ffmpeg(
            tmp_path / "in.mp4", tmp_path / "out.mp4", fmt, subtitle_ass_path=ass, fonts_dir=tmp_path,
            fx_request=request, fx_report=report, **kwargs,
        )
        return out, report

    return SimpleNamespace(run=run, calls=calls, state=state, ass=ass, request=request)


@pytest.mark.parametrize("case", ["original", "split", "pan", "vertical_fit", "vertical_complex", "vertical_vf",
                                  "vertical_self_planned"])
def test_every_branch_renders_through_the_chain(reframe, case):
    kwargs: dict = {}
    fmt = case
    if case == "split":
        fmt = "vertical_split"
        region = {"tile_w": 960, "tile_h": 1080, "tile_x": 0, "tile_y": 0}
        reframe.state["speaker_plan"] = {"mode": "split", "regions": {"left": region, "right": region}}
    elif case == "pan":
        fmt = "vertical_pan"
        reframe.state["speaker_plan"] = {"mode": "pan", "crop_w": 608, "crop_h": 1080, "x_expression": "100"}
    elif case == "vertical_complex":
        fmt, kwargs = "vertical", {"vertical_plan": (COMPLEX, "complex")}
    elif case == "vertical_vf":
        fmt, kwargs = "vertical", {"vertical_plan": (VF, "vf")}
    elif case == "vertical_self_planned":
        fmt = "vertical"
    (ok, width, height), report = reframe.run(fmt, **kwargs)
    assert ok and reframe.calls["ffmpeg"] == []
    (chain,) = reframe.calls["chain"]
    assert chain["request"] is reframe.request
    assert chain["mode"] == "full"
    assert chain["subtitle_ass_path"] == reframe.ass
    assert "subtitles" not in chain["head"] and "ass=" not in chain["head"]      # P3 burns the ASS, not P1
    assert chain["head"].rstrip().endswith(chain["head_label"])
    assert chain["encode_args"] and chain["has_audio"] is True
    assert chain["scratch_dir"] == chain["output_path"].parent
    assert report["result"].ok
    assert (width, height) == (chain["out_w"], chain["out_h"])
    if case == "original":
        assert chain["tail"] == "setsar=1" and (width, height) == (1920, 1080)
    else:
        assert (width, height) == (1080, 1920)
    if case == "vertical_self_planned":
        assert reframe.calls["plan"] == [{"allow_kenburns": False}]


def test_safe_mode_and_failure_are_passed_through(reframe):
    reframe.state["ok"] = False
    (ok, _, _), report = reframe.run("vertical", vertical_plan=(VF, "vf"), mode="safe")
    assert ok is False
    assert reframe.calls["chain"][0]["mode"] == "safe"
    assert report["result"].error == "p2: exit 1"


def test_broll_overlays_go_to_p1(reframe, tmp_path):
    footage = tmp_path / "stock.mp4"
    footage.write_bytes(b"x")
    reframe.run("vertical", vertical_plan=(VF, "vf"),
                broll_overlays=[{"path": str(footage), "start": 1.0, "duration": 2.0}])
    chain = reframe.calls["chain"][0]
    assert [o["start"] for o in chain["broll_overlays"]] == [1.0]
    assert chain["broll_inputs"] and str(footage) in chain["broll_inputs"]


def test_without_fx_request_nothing_changes(reframe, tmp_path):
    out = reframing.render_reframed_clip_ffmpeg(tmp_path / "in.mp4", tmp_path / "out.mp4", "vertical",
                                                vertical_plan=(VF, "vf"))
    assert out == (True, 1080, 1920)
    assert reframe.calls["chain"] == [] and len(reframe.calls["ffmpeg"]) == 1
