"""The studio's FX analysis job (``editor_fx_analysis``): face track and person matte for a session (WP15b).

The matte is published to the bucket before the session records ``matteVersion`` (AI-H7); results land only on the
preparation they were computed for; a session that already has what its draft needs is left alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.editor_document import editor_dir
from src.editor_state import editor_key
from src.media_storage import MATTE_VERSION_MARKER, MediaStorage
from src.services import editor_fx_analysis as analysis

TASK, CLIP, FILE = "task-1", "clip-1", "clip.mp4"
TRACK = {"version": 1, "space": "picture", "fps": 6.0, "samples": [[0.0, [[0.4, 0.3, 0.2, 0.2, 0.9]]]]}


def draft(*, behind=False, framing=None, disabled=()):
    cues = [{"kind": "zoom", "id": "z", "start": 0.2, "end": 0.6, "mode": "punch", "scale": 1.2}]
    if behind:
        cues.append({"kind": "accent", "id": "bh", "start": 0.5, "end": 1.0, "variant": "behind", "text": "NO"})
    return {"version": 1, "segments": [{"id": "s", "start": 0, "end": 2}],
            "framing": framing or {"aspect": "vertical", "fit": "auto"},
            "fx": {"style": {"intensity": "bold"}, "cues": cues, "disabled": list(disabled)}}


def test_needs_base_and_job_id():
    assert analysis.analysis_needs(draft()) == (True, False)
    assert analysis.analysis_needs(draft(behind=True)) == (True, True)
    assert analysis.analysis_needs(draft(behind=True, disabled=["accent"])) == (True, False)
    assert analysis.analysis_needs({"fx": {"cues": ["junk", {"kind": "future"}]}}) == (True, False)
    assert analysis.analysis_needs(None) == (True, False)
    assert analysis.session_base(draft(), {"hasLayout": True}) == "layout"
    assert analysis.session_base(draft(), {"hasLayout": False}) == "clean"
    assert analysis.session_base(draft(framing={"aspect": "square", "fit": "auto"}), {"hasLayout": True}) == "clean"
    assert analysis.session_base(draft(framing={"aspect": "vertical", "fit": "cover"}), {"hasLayout": True}) == "clean"
    assert analysis.analysis_job_id("k", "v1", matte=True) == "fxan:k:v1:matte"
    assert analysis.analysis_job_id("k", None, matte=False) == "fxan:k:v0:face"


@pytest.fixture
def job_env(tmp_path, monkeypatch, editor_store):
    """A ready session with clean.mp4 and layout.mp4 on this machine, recording every side effect in order."""
    from src.config import Config

    config = Config()
    config.temp_dir = str(tmp_path)
    monkeypatch.setattr("src.config.get_config", lambda: config)
    directory = editor_dir(str(tmp_path), TASK, CLIP, FILE)
    directory.mkdir(parents=True)
    (directory / "clean.mp4").write_bytes(b"clean")
    (directory / "layout.mp4").write_bytes(b"layout")
    events: list[tuple] = []
    key = editor_key(TASK, CLIP, FILE)

    async def restore(storage, directory, *, has_layout=False, version=None):
        events.append(("restore", has_layout, version))

    async def merge(session_key, media_version, patch):
        row = editor_store.sessions.get(session_key)
        if not row or row["asset"].get("status") != "ready" or (row["asset"].get("mediaVersion") or "") != (
                media_version or ""):
            events.append(("merge-rejected", dict(patch)))
            return False
        row["asset"].update(patch)
        events.append(("merge", dict(patch)))
        return True

    def face(video, *, fps=6.0):
        events.append(("face", Path(video).name, fps))
        return TRACK

    def matte(video, dest):
        events.append(("matte", Path(video).name))
        Path(dest).write_bytes(b"person matte")
        return True

    real_publish = MediaStorage.publish_editor_matte

    def publish(self, directory, path, version):
        events.append(("publish", Path(path).read_bytes(), (directory / MATTE_VERSION_MARKER).exists()))
        real_publish(self, directory, path, version)

    monkeypatch.setattr("src.services.editor_service.restore_session_media", restore)
    monkeypatch.setattr(analysis, "merge_session_asset", merge)
    monkeypatch.setattr("src.media.fx_analysis.face_track_for_video", face)
    monkeypatch.setattr("src.media.fx_analysis.matte_for_video", matte)
    monkeypatch.setattr("src.media_storage.MediaStorage.publish_editor_matte", publish)

    def ready(document, **asset):
        editor_store.put_ready(TASK, CLIP, FILE, {"status": "ready", "mediaVersion": "v1", "hasLayout": True,
                                                  **asset}, document)

    return {"directory": directory, "events": events, "ready": ready, "store": editor_store, "key": key}


async def run():
    return await analysis.editor_fx_analysis({}, TASK, CLIP, FILE)


async def test_face_and_matte_are_computed_and_the_matte_is_published_before_it_is_recorded(job_env):
    job_env["ready"](draft(behind=True))
    result = await run()
    assert result == {"status": "done", "face": True, "matte": True}
    events = job_env["events"]
    kinds = [event[0] for event in events]
    assert kinds == ["merge", "restore", "face", "matte", "publish", "merge"]
    assert events[1] == ("restore", True, "v1")
    assert events[2] == ("face", "layout.mp4", 6.0) and events[3] == ("matte", "layout.mp4")
    assert events[4] == ("publish", b"person matte", False)
    recorded = events[5][1]
    assert recorded["faceTrack"] == {"base": "layout", "track": TRACK}
    assert recorded["matteBase"] == "layout" and recorded["matteVersion"].startswith("v1.")
    assert recorded["fxAnalysis"] == {"status": "done", "base": "layout", "face": True, "matte": True}
    directory = job_env["directory"]
    assert (directory / "matte.mp4").read_bytes() == b"person matte"
    assert (directory / MATTE_VERSION_MARKER).read_text() == recorded["matteVersion"]
    assert not list(directory.glob(".matte-*.mp4"))
    asset = job_env["store"].sessions[job_env["key"]]["asset"]
    assert asset["matteVersion"] == recorded["matteVersion"] and asset["status"] == "ready"


async def test_face_only_when_nothing_draws_behind_and_idempotent(job_env):
    job_env["ready"](draft())
    assert (await run())["status"] == "done"
    assert [e[0] for e in job_env["events"]] == ["merge", "restore", "face", "merge"]
    job_env["events"].clear()
    assert await run() == {"status": "done", "cached": True}
    assert job_env["events"] == []


async def test_a_track_on_another_base_is_recomputed_for_the_drafts_base(job_env):
    job_env["ready"](draft(framing={"aspect": "vertical", "fit": "cover"}),
                     faceTrack={"base": "layout", "track": TRACK})
    await run()
    face = [e for e in job_env["events"] if e[0] == "face"]
    assert face == [("face", "clean.mp4", 6.0)]
    assert job_env["store"].sessions[job_env["key"]]["asset"]["faceTrack"]["base"] == "clean"


async def test_no_detector_records_an_empty_track(job_env, monkeypatch):
    job_env["ready"](draft())
    monkeypatch.setattr("src.media.fx_analysis.face_track_for_video", lambda video, *, fps=6.0: None)
    assert await run() == {"status": "done", "face": False}
    track = job_env["store"].sessions[job_env["key"]]["asset"]["faceTrack"]["track"]
    assert track["samples"] == [] and track["space"] == "picture"


async def test_a_failed_matte_is_never_published_or_recorded(job_env, monkeypatch):
    job_env["ready"](draft(behind=True), faceTrack={"base": "layout", "track": TRACK})
    monkeypatch.setattr("src.media.fx_analysis.matte_for_video", lambda video, dest: False)
    assert await run() == {"status": "done", "matte": False}
    kinds = [e[0] for e in job_env["events"]]
    assert "publish" not in kinds
    asset = job_env["store"].sessions[job_env["key"]]["asset"]
    assert "matteVersion" not in asset and asset["fxAnalysis"]["matte"] is False


async def test_a_session_rebuilt_meanwhile_discards_the_results(job_env, monkeypatch):
    job_env["ready"](draft())

    def face(video, *, fps=6.0):
        job_env["store"].sessions[job_env["key"]]["asset"]["mediaVersion"] = "v2"   # rebuilt while analysing
        return TRACK

    monkeypatch.setattr("src.media.fx_analysis.face_track_for_video", face)
    assert await run() == {"status": "discarded"}
    assert "faceTrack" not in job_env["store"].sessions[job_env["key"]]["asset"]


async def test_errors_are_recorded_not_raised(job_env, monkeypatch):
    job_env["ready"](draft())

    def broken(video, *, fps=6.0):
        raise RuntimeError("decoder crashed")

    monkeypatch.setattr("src.media.fx_analysis.face_track_for_video", broken)
    assert await run() == {"status": "failed"}
    assert job_env["store"].sessions[job_env["key"]]["asset"]["fxAnalysis"] == {"status": "failed", "base": "layout"}


async def test_sessions_that_are_not_ready_are_skipped(job_env):
    assert await run() == {"status": "skipped"}
    assert job_env["events"] == []


def test_the_job_is_registered_on_the_video_worker():
    from src.workers.tasks import WorkerSettings

    names = [getattr(f, "name", getattr(f, "__name__", None)) for f in WorkerSettings.functions]
    assert "editor_fx_analysis" in names
    job = next(f for f in WorkerSettings.functions if getattr(f, "name", None) == "editor_fx_analysis")
    assert job.timeout_s == analysis.ANALYSIS_JOB_TIMEOUT_SECONDS and job.max_tries == 2
