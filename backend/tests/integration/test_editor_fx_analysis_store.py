"""``editor_fx_analysis.merge_session_asset`` against PostgreSQL: the analysis lands only on the preparation it was
computed for, and merges into the asset without touching the draft."""

import uuid

from src.editor_state import EditorStore, editor_key
from src.services.editor_fx_analysis import merge_session_asset

DOCUMENT = {"version": 1, "segments": [{"id": "s", "start": 0, "end": 1}]}


async def test_merge_is_compare_and_set_on_the_media_version(initialized_database):
    store = EditorStore()
    task_id, clip_id, filename = str(uuid.uuid4()), str(uuid.uuid4()), "clip.mp4"
    key = editor_key(task_id, clip_id, filename)
    asset = {"status": "ready", "duration": 1, "width": 320, "height": 180, "hasLayout": True, "mediaVersion": "v1"}
    await store.finish_prepare(key, task_id, clip_id, filename, asset, DOCUMENT)

    track = {"base": "layout", "track": {"version": 1, "space": "picture", "fps": 6, "samples": []}}
    assert await merge_session_asset(key, "v1", {"faceTrack": track, "matteVersion": "v1.abc"})
    loaded = await store.load(key)
    assert loaded["asset"] == {**asset, "faceTrack": track, "matteVersion": "v1.abc"}
    assert loaded["draft"]["document"] == DOCUMENT

    # A rebuilt session (another media version) or a missing one never takes stale results.
    assert not await merge_session_asset(key, "v0", {"matteVersion": "stale"})
    assert not await merge_session_asset(editor_key(task_id, clip_id, "other.mp4"), "v1", {"matteVersion": "x"})
    assert (await store.load(key))["asset"]["matteVersion"] == "v1.abc"

    # Sessions prepared before media versions existed merge on an empty version.
    legacy_key = editor_key(task_id, str(uuid.uuid4()), filename)
    await store.finish_prepare(legacy_key, task_id, clip_id, filename, {"status": "ready"}, DOCUMENT)
    assert await merge_session_asset(legacy_key, None, {"fxAnalysis": {"status": "done"}})
