"""WP12a failure handling (§6.3 success rule, §6.6 inputs): deno killed mid-stream, timeouts and cancels kill every
process with no zombies, P2's exit 4 after P3 succeeded is success, and failures name the stage that failed."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from src.media import fx_chain
from tests.unit.fx_media.fx_chain_kit import (  # noqa: F401 - chain_deps is a fixture
    chain_deps,
    cue,
    decode_audio,
    make_source,
    plan,
    probe,
    requires_media,
    rms_db,
    run_chain,
    stream_durations,
)

FRAME = 1.0 / 30


def fake_deno(tmp_path: Path, body: str) -> Path:
    """An executable standing in for deno: argv is the real P2 argv, the job file is the last argument."""
    script = tmp_path / "fake-deno"
    script.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body))
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def real_deno_killed_after(tmp_path: Path, seconds: float) -> Path:
    """Runs the real deno with the chain's argv and SIGKILLs it after ``seconds`` (deno killed mid-stream)."""
    from src.edit_fx import env as fx_env
    import shutil

    real = shutil.which(fx_env.fx_deno_bin())
    return fake_deno(tmp_path, f"""
        import subprocess, sys, time
        child = subprocess.Popen([{real!r}, *sys.argv[1:]])
        time.sleep({seconds})
        child.kill()
        child.wait()
        sys.exit(137)
    """)


@pytest.fixture
def spawned(monkeypatch):
    """Records every process the chain starts, to check none survives (no zombies, nothing left running)."""
    procs: list[subprocess.Popen] = []
    real = subprocess.Popen

    class Recording(real):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            procs.append(self)

    monkeypatch.setattr(fx_chain.subprocess, "Popen", Recording)
    return procs


def assert_all_reaped(procs: list[subprocess.Popen]) -> None:
    assert procs
    for proc in procs:
        assert proc.returncode is not None, proc.args[:2]
        with pytest.raises(ProcessLookupError):
            os.kill(proc.pid, 0)            # reaped: not even a zombie


def sfx_plan(duration: float):
    return plan(duration=duration, cues=[cue("sfx", 1.0, sound="impact"), cue("flash", 0.2, 0.4)])


@requires_media
def test_deno_killed_mid_stream_fails_full_and_safe_keeps_audio_fx(tmp_path, chain_deps, monkeypatch, spawned):
    source = make_source(tmp_path / "src.mp4", dur=8.0, audio="silence")
    request = fx_chain.FxRequest(plan=sfx_plan(8.0), words=[], caption=None)
    monkeypatch.setenv("FX_DENO_BIN", str(real_deno_killed_after(tmp_path, 0.6)))
    started = time.monotonic()
    full = run_chain(source, tmp_path / "full.mp4", request, encode_args=[
        "-c:v", "libx264", "-preset", "veryslow", "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30"])
    assert not full.ok and full.error.startswith("p2:"), full.error
    assert time.monotonic() - started < 60
    assert_all_reaped(spawned)
    # The ladder's next step: safe (no deno) still produces the clip and keeps the SFX in the mix.
    safe = run_chain(source, tmp_path / "safe.mp4", request, mode="safe")
    assert safe.ok, safe.error
    assert safe.level == "safe"
    samples = decode_audio(tmp_path / "safe.mp4")
    assert rms_db(samples[int(1.0 * 48000): int(1.1 * 48000)]) > -40
    video, audio = stream_durations(tmp_path / "safe.mp4")
    assert abs(video - audio) < FRAME


@requires_media
def test_timeout_kills_all_three_with_no_zombies(tmp_path, chain_deps, monkeypatch, spawned):
    source = make_source(tmp_path / "src.mp4", dur=2.0)
    sleeper = fake_deno(tmp_path, "import time\ntime.sleep(600)\n")
    monkeypatch.setenv("FX_DENO_BIN", str(sleeper))
    monkeypatch.setenv("FX_RENDER_TIMEOUT_SECONDS", "1.5")
    started = time.monotonic()
    result = run_chain(source, tmp_path / "out.mp4", fx_chain.FxRequest(plan=sfx_plan(2.0), words=[], caption=None))
    assert not result.ok and result.error == "timeout"
    assert time.monotonic() - started < 20
    chain = [p for p in spawned if "rawvideo" in p.args or p.args[0] == str(sleeper)]
    assert len(chain) == 3                                             # P1, P2, P3
    assert all(p.returncode == -9 for p in chain)                      # all three were killed
    assert_all_reaped(spawned)
    assert not list((tmp_path / "scratch").glob("fxchain-*"))


@requires_media
def test_cancel_kills_the_chain(tmp_path, chain_deps, monkeypatch, spawned):
    source = make_source(tmp_path / "src.mp4", dur=2.0)
    monkeypatch.setenv("FX_DENO_BIN", str(fake_deno(tmp_path, "import time\ntime.sleep(600)\n")))
    deadline = time.monotonic() + 1.0
    result = run_chain(source, tmp_path / "out.mp4", fx_chain.FxRequest(plan=sfx_plan(2.0), words=[], caption=None),
                       should_cancel=lambda: time.monotonic() > deadline)
    assert not result.ok and result.error == "cancelled"
    assert_all_reaped(spawned)


@requires_media
def test_p2_exit_4_after_p3_success_is_success(tmp_path, chain_deps, monkeypatch, spawned):
    """P3 stops reading after -frames:v; a P2 still writing then exits 4 (stdout closed). That is a success (AR-m5)."""
    script = fake_deno(tmp_path, """
        import json, sys
        job = json.load(open(sys.argv[-1]))
        size = job["width"] * job["height"] * 4
        frame = None
        out = sys.stdout.buffer
        try:
            while True:                       # pass input frames through, then keep repeating the last one
                chunk = sys.stdin.buffer.read(size)
                if len(chunk) == size:
                    frame = chunk
                elif frame is None:
                    sys.exit(3)
                out.write(frame)
                out.flush()
        except BrokenPipeError:
            sys.exit(4)
    """)
    monkeypatch.setenv("FX_DENO_BIN", str(script))
    source = make_source(tmp_path / "src.mp4", dur=1.0)
    result = run_chain(source, tmp_path / "out.mp4", fx_chain.FxRequest(plan=sfx_plan(1.0), words=[], caption=None))
    assert result.ok, result.error
    assert probe(tmp_path / "out.mp4")["frames"] == 30
    p2 = next(p for p in spawned if p.args[0] == str(script))
    assert p2.returncode == 4
    assert_all_reaped(spawned)


@requires_media
def test_failures_name_their_stage(tmp_path, chain_deps, monkeypatch, spawned):
    source = make_source(tmp_path / "src.mp4", dur=1.0)
    request = fx_chain.FxRequest(plan=sfx_plan(1.0), words=[], caption=None)

    # P1: a broken picture graph (the ladder retries without B-roll only for this stage).
    bad_head = run_chain(source, tmp_path / "a.mp4", request, head="[0:v]nosuchfilter[base]", head_label="[base]")
    assert not bad_head.ok and bad_head.error.startswith("p1:"), bad_head.error

    # P2: a job deno refuses (exit 2) and a missing binary.
    monkeypatch.setenv("FX_DENO_BIN", str(fake_deno(tmp_path, "import sys\nsys.stdin.buffer.read()\nsys.exit(2)\n")))
    bad_job = run_chain(source, tmp_path / "b.mp4", request)
    assert not bad_job.ok and bad_job.error.startswith("p2: exit 2"), bad_job.error
    monkeypatch.setenv("FX_DENO_BIN", str(tmp_path / "missing-deno"))
    missing = run_chain(source, tmp_path / "c.mp4", request)
    assert not missing.ok and missing.error.startswith("p2: could not start"), missing.error
    monkeypatch.delenv("FX_DENO_BIN")

    # P3: an encoder that does not exist.
    bad_encode = run_chain(source, tmp_path / "d.mp4", request, encode_args=["-c:v", "nosuchencoder"])
    assert not bad_encode.ok and bad_encode.error.startswith("p3:"), bad_encode.error
    assert_all_reaped(spawned)
    assert not list((tmp_path / "scratch").glob("fxchain-*"))


@requires_media
def test_analysis_and_bus_failures_are_never_fatal(tmp_path, chain_deps, monkeypatch):
    source = make_source(tmp_path / "src.mp4", dur=1.0)

    class Broken:
        def analyse(self, *args, **kwargs):
            raise RuntimeError("model missing")

    import tests.unit.fx_media.fx_chain_kit as kit

    def broken_bus(*args, **kwargs):
        raise RuntimeError("decode failed")

    monkeypatch.setattr(fx_chain, "_analysis_module", lambda: Broken())
    audio = kit.STUB_AUDIO.__class__(**{**vars(kit.STUB_AUDIO), "build_bus": broken_bus})
    monkeypatch.setattr(fx_chain, "_audio_module", lambda: audio)
    p = plan(duration=1.0, cues=[cue("zoom", 0.2, 0.6, mode="punch", scale=1.2, anchor="face"),
                                 cue("accent", 0.3, 0.8, variant="behind", text="BIG"),
                                 cue("sfx", 0.5, sound="pop")])
    result = run_chain(source, tmp_path / "out.mp4", fx_chain.FxRequest(plan=p, words=[], caption=None))
    assert result.ok, result.error
    assert result.face_track is None and result.library_fingerprint == ""
    assert probe(tmp_path / "out.mp4")["audio"] is not None             # the voice still renders
