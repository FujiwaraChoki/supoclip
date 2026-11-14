"""Combining drafts with dynamic edits (§14.1, AI-L4, AR-M14): rebased beats and cues, globals from the first draft
that has edits, no freeze budget on rebased output, and the combined clip's own sidecar."""

import shutil
import subprocess
import sys
import types
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.edit_fx.contracts import FX_KINDS
from src.editor_document import DocFx, EditDocument, editor_dir, mapped_fx
from src.services.editor_service import combine_editor, combined_fx, rebased_fx
from tests.fixtures.editor_store import stored_state

# What the render applied: play to 0.5 s, hold 1.6 s, play the rest (F = E + 1.6).
APPLIED_WARP = [
    {"e0": 0.0, "e1": 0.5, "o0": 0.0, "rate": 1.0, "hold": 0.0},
    {"e0": 0.5, "e1": 0.5, "o0": 0.5, "rate": 0.0, "hold": 1.6},
    {"e0": 0.5, "e1": 1.6, "o0": 2.1, "rate": 1.0, "hold": 0.0},
]


def entry_doc(name: str, fx: dict | None = None, **extra) -> EditDocument:
    data = {
        "segments": [{"id": "a", "start": 0.1, "end": 0.9}],
        "words": [{"id": "w", "start": 0.2, "end": 0.8, "text": name}],
        "framing": {"aspect": "original"},
        **extra,
    }
    if fx is not None:
        data["fx"] = fx
    return EditDocument.model_validate(data)


def freeze_fx(**fx) -> dict:
    return DocFx.model_validate({
        "style": {"intensity": "bold"},
        "seed": 11,
        "mood": "funny",
        "beats": [{"id": "b0", "kind": "punchline", "start": 0.2, "end": 0.8}],
        "cues": [
            {"kind": "freeze", "id": "b0.freeze", "start": 0.5, "end": 0.5, "hold": 1.6, "beat": "b0"},
            {"kind": "sfx", "id": "b0.sfx", "start": 0.3, "end": 0.3, "sound": "pop", "beat": "b0"},
            {"kind": "zoom", "id": "u0", "start": 0.2, "end": 0.6, "mode": "punch", "scale": 1.2, "origin": "user"},
        ],
        "suppressed": ["b0.flash"],
        "grade": "warm",
        **fx,
    }).model_dump(mode="json")


def test_rebased_fx_moves_mapped_cues_and_prefixes_ids():
    document = entry_doc("x", freeze_fx(disabled=["zoom"]), segments=[
        {"id": "a", "start": 0.0, "end": 0.4}, {"id": "b", "start": 0.45, "end": 0.9}])
    part = rebased_fx(document, 10.0, 2)
    assert [(c["id"], c["start"]) for c in part["cues"]] == [("e2:b0.sfx", 10.3), ("e2:b0.freeze", 10.45)]
    assert all(c["beat"] == "e2:b0" for c in part["cues"])
    # The beat is cut by the junction: one piece per segment, in combined time.
    assert [(b["id"], b["start"], b["end"]) for b in part["beats"]] == [
        ("e2:b0", 10.2, 10.4), ("e2:b0@1", 10.4, 10.75)]
    assert part["suppressed"] == ["e2:b0.flash"]
    assert rebased_fx(entry_doc("legacy"), 0.0, 0) == {"beats": [], "cues": [], "suppressed": []}


def test_globals_come_from_the_first_draft_with_edits():
    drafts = [entry_doc("legacy"), entry_doc("a", freeze_fx()), entry_doc("b", freeze_fx(grade="noir", seed=3))]
    parts = [rebased_fx(d, i * 0.8, i) for i, d in enumerate(drafts)]
    fx = DocFx.model_validate(combined_fx(drafts, parts))
    assert fx.grade == "warm" and fx.seed == 11 and fx.disabled == [] and not fx.hookTitleSuppressed
    assert [c.id for c in fx.cues if c.kind == "freeze"] == ["e1:b0.freeze", "e2:b0.freeze"]
    assert combined_fx([entry_doc("a"), entry_doc("b")], [rebased_fx(entry_doc("a"), 0, 0)] * 2) is None


@pytest.fixture
def fake_fx_renderer(monkeypatch):
    rendered = []
    module = types.ModuleType("src.editor_fx_render")

    def render_document_fx(document, ctx):
        rendered.append(ctx)
        shutil.copy2(ctx.input_path, ctx.output_path)
        return SimpleNamespace(level="full", applied_warp=APPLIED_WARP)

    @asynccontextmanager
    async def studio_fx_inputs(document, asset, directory, *, storage=None):
        rendered.append(("inputs", asset))
        yield None

    module.render_document_fx = render_document_fx
    module.studio_fx_inputs = studio_fx_inputs
    monkeypatch.setitem(sys.modules, "src.editor_fx_render", module)
    monkeypatch.setattr(sys.modules["src"], "editor_fx_render", module, raising=False)
    return rendered


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
@pytest.mark.parametrize("first_has_fx", [False, True])
async def test_combining_drafts_with_dynamic_edits(tmp_path, monkeypatch, editor_store, fake_fx_renderer, first_has_fx):
    from src.clip_source_map import load_clip_edit_fx
    from src.config import Config

    config = Config()
    config.temp_dir = str(tmp_path)
    monkeypatch.setattr("src.config.get_config", lambda: config)
    clips = [{"id": f"clip{i}", "filename": f"clip{i}.mp4", "clip_order": i} for i in (1, 2)]
    documents = [entry_doc("one", freeze_fx() if first_has_fx else None), entry_doc("two", freeze_fx(grade="noir"))]
    entries = []
    for clip, document in zip(clips, documents):
        directory = editor_dir(str(tmp_path), "task", clip["id"], clip["filename"])
        directory.mkdir(parents=True)
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=blue:size=320x180:rate=30:duration=1",
             "-c:v", "libx264", str(directory / "clean.mp4")],
            capture_output=True, check=True,
        )
        entries.append({**clip, "document": document.model_dump(mode="json"),
                        "state": {"width": 320, "height": 180, "hasAudio": False}, "ranges": [[0, 1]]})
    key = editor_store.put_ready("task", "clip1", "clip1.mp4", {"duration": 1}, entries[0]["document"])
    job = "c" * 32
    assert await editor_store.create_job(key, job, "combine", {"inputs": entries})
    created = []

    async def create(_db, *args, **kwargs):
        created.append((args, kwargs))
        return kwargs["clip_id"]

    repository = SimpleNamespace(get_clips_by_task=AsyncMock(return_value=clips), create_clip=create,
                                 reorder_task_clips=AsyncMock())
    service = SimpleNamespace(clip_repo=repository, _seconds_to_mmss=lambda s: f"00:{int(s):02d}")

    @asynccontextmanager
    async def sessions():
        yield object()

    @asynccontextmanager
    async def transaction(*args):
        yield

    monkeypatch.setattr("src.database.AsyncSessionLocal", sessions)
    monkeypatch.setattr("src.services.task_service.TaskService", lambda _db: service)
    monkeypatch.setattr("src.repositories.edit_transaction.task_edit_transaction", transaction)
    await combine_editor({}, "task", "clip1", "clip1.mp4", job)

    state = await editor_store.job(key, job)
    assert state["status"] == "completed", state
    combined = await stored_state("task", state["clip_id"], created[0][0][1])
    fx = combined["draft"]["document"]["fx"]
    # Globals from the first draft that has edits; a container even when entry 0 had none (AI-L4).
    assert fx["grade"] == ("warm" if first_has_fx else "noir")
    freezes = [c for c in fx["cues"] if c["kind"] == "freeze"]
    # 1.6 s of freezes per clip, both kept: the hold budget is not applied to rebased output (AR-M14).
    assert [c["hold"] for c in freezes] == ([1.6, 1.6] if first_has_fx else [1.6])
    assert all(c["id"].startswith(("e0:", "e1:")) for c in fx["cues"])
    # The FX inputs are bound around the render; the combined source has none of its own.
    assert fake_fx_renderer[0] == ("inputs", {}) and len(fake_fx_renderer) == 2

    # The combined clip's sidecar carries its plan and the warp the render applied (AR-M14).
    clip_path = Path(tmp_path) / "clips" / created[0][0][1]
    sidecar = load_clip_edit_fx(clip_path)
    assert sidecar is not None and sidecar["applied_warp"] == APPLIED_WARP and sidecar["fx_level"] == "full"
    document = EditDocument.model_validate(combined["draft"]["document"])
    assert [c["id"] for c in sidecar["plan"]["cues"]] == [c.id for c in mapped_fx(document)]
    assert sidecar["plan"]["edit_duration"] == pytest.approx(1.6, abs=1e-6)
    assert created[0][1]["fx_meta"] == {"fx_level": "full", "timing": "word", "intensity": "bold",
                                        "template": "default"}


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
async def test_combining_drafts_without_edits_is_unchanged(tmp_path, monkeypatch, editor_store, fake_fx_renderer):
    from src.clip_source_map import load_clip_edit_fx
    from src.config import Config

    config = Config()
    config.temp_dir = str(tmp_path)
    monkeypatch.setattr("src.config.get_config", lambda: config)
    clips = [{"id": f"clip{i}", "filename": f"clip{i}.mp4", "clip_order": i} for i in (1, 2)]
    inert = DocFx(disabled=list(FX_KINDS)).model_dump(mode="json")
    entries = []
    for clip, fx in zip(clips, (None, inert)):
        directory = editor_dir(str(tmp_path), "task", clip["id"], clip["filename"])
        directory.mkdir(parents=True)
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=red:size=320x180:rate=30:duration=1",
             "-c:v", "libx264", str(directory / "clean.mp4")],
            capture_output=True, check=True,
        )
        entries.append({**clip, "document": entry_doc(clip["id"], fx).model_dump(mode="json"),
                        "state": {"width": 320, "height": 180, "hasAudio": False}, "ranges": [[0, 1]]})
    key = editor_store.put_ready("task", "clip1", "clip1.mp4", {"duration": 1}, entries[0]["document"])
    job = "d" * 32
    assert await editor_store.create_job(key, job, "combine", {"inputs": entries})
    created = []

    async def create(_db, *args, **kwargs):
        created.append((args, kwargs))
        return kwargs["clip_id"]

    repository = SimpleNamespace(get_clips_by_task=AsyncMock(return_value=clips), create_clip=create,
                                 reorder_task_clips=AsyncMock())
    service = SimpleNamespace(clip_repo=repository, _seconds_to_mmss=lambda s: f"00:{int(s):02d}")

    @asynccontextmanager
    async def sessions():
        yield object()

    @asynccontextmanager
    async def transaction(*args):
        yield

    monkeypatch.setattr("src.database.AsyncSessionLocal", sessions)
    monkeypatch.setattr("src.services.task_service.TaskService", lambda _db: service)
    monkeypatch.setattr("src.repositories.edit_transaction.task_edit_transaction", transaction)
    await combine_editor({}, "task", "clip1", "clip1.mp4", job)
    state = await editor_store.job(key, job)
    assert state["status"] == "completed", state
    combined = await stored_state("task", state["clip_id"], created[0][0][1])
    # An inert container rebases to nothing worth rendering: legacy render, no sidecar keys, no fx_meta.
    assert not fake_fx_renderer and "fx_meta" not in created[0][1]
    assert load_clip_edit_fx(Path(tmp_path) / "clips" / created[0][0][1]) is None
    assert combined["draft"]["document"]["fx"]["cues"] == []
