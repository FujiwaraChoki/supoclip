"""Clip edits keep workflow clips on their planned cuts."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.clip_source_map import save_clip_source_ranges
from src.services import clip_service as clip_service_module
from src.services.clip_service import _narrow_planned_meta, _range_groups
from src.services.task_service import TaskService

PLANNED = [[100.0, 130.0], [400.0, 420.0], [50.0, 60.0]]   # trailer beats, play order
CLEANED = [(100.0, 112.0), (113.0, 130.0), (400.0, 420.0), (50.0, 60.0)]
OFFSETS = [0.0, 11.88, 28.84, 48.8]


def _meta(**extra):
    return {
        "planned": True, "ranges": PLANNED, "junction_fades": [0.04, 0.04], "max_seconds": 49.0,
        "beats": [{"role": "cold_open", "range_index": 0, "output_start": 0.0},
                  {"role": "escalation", "range_index": 1, "output_start": 28.84},
                  {"role": "button", "range_index": 2, "output_start": 48.8}],
        **extra,
    }


def _clip(clip_path, **extra):
    return {
        "id": "clip-1", "task_id": "task-1", "file_path": str(clip_path), "start_time": "00:50",
        "end_time": "07:00", "duration": 58.8, "text": "Trailer", "relevance_score": 1.0, "reasoning": "",
        "virality_score": 0, "hook_score": 0, "engagement_score": 0, "value_score": 0,
        "shareability_score": 0, "hook_type": None, "hook_title": "You won't guess", "clip_order": 1,
        "workflow_role": "trailer", "part_number": None, "workflow_meta": _meta(), **extra,
    }


def _service(monkeypatch, clips, rendered, captured, cleanup):
    service = TaskService(db=None)
    service.task_repo.get_task_by_id = AsyncMock(return_value={
        "id": "task-1", "source_url": "upload://source.mp4", "source_type": "video_url", "user_id": None,
    })
    service.task_repo.update_task_clips = AsyncMock()
    service.clip_repo.get_clips_by_task = AsyncMock(return_value=clips)
    service.clip_repo.delete_clips_by_task = AsyncMock()
    service.clip_repo.create_clip = AsyncMock(return_value="new-clip")

    async def settings(_task_id):
        return {"output_format": "vertical", "add_subtitles": True, "clip_length": "auto", **cleanup}

    monkeypatch.setattr(service, "_load_task_source_settings", settings)

    async def create_video_clips(_video, segments, *_args, **kwargs):
        captured["segments"] = segments
        captured["kwargs"] = kwargs
        return rendered

    service.video_service.create_video_clips = create_video_clips
    service.video_service.prepare_source = AsyncMock(side_effect=lambda path: path)
    service.video_service.resolve_local_video_path = lambda _url: Path("/tmp/source.mp4")
    monkeypatch.setattr(clip_service_module, "resolve_upload_for_edit", AsyncMock(return_value=Path(__file__)))
    return service


CLEANUP_OFF = {"cut_long_pauses": False, "pause_threshold_ms": 900, "remove_filler_words": False, "filtered_words": []}


@pytest.mark.asyncio
async def test_regenerate_replays_stored_ranges_offsets_and_keeps_workflow_fields(
    isolated_clip_edits, monkeypatch, tmp_path
):
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"clip")
    save_clip_source_ranges(clip_path, CLEANED, OFFSETS)
    captured = {}
    rendered = [{"clip_id": 1, "filename": "new.mp4", "path": "new.mp4", "start_time": "01:40",
                 "end_time": "02:10", "duration": 58.8, "group_output_starts": [0.0, 28.8, 48.7]}]
    service = _service(monkeypatch, [_clip(clip_path)], rendered, captured, CLEANUP_OFF)

    await service.regenerate_all_clips_for_task("task-1", None, None, None, "default", cleanup_settings=CLEANUP_OFF)

    (segment,) = captured["segments"]
    assert segment["planned"] is True and segment["required"] is True
    assert segment["keep_ranges"] == CLEANED
    assert segment["output_offsets"] == OFFSETS
    assert segment["range_groups"] == [0, 0, 1, 2]
    assert segment["max_seconds"] == 60.0  # the planned ranges never get cut
    assert (segment["start_time"], segment["end_time"]) == ("01:40", "02:10")
    assert "source_ranges" not in segment
    created = service.clip_repo.create_clip.await_args.kwargs
    assert created["workflow_role"] == "trailer"
    assert [beat["output_start"] for beat in created["workflow_meta"]["beats"]] == [0.0, 28.8, 48.7]
    # Labels cover the planned ranges on the source timeline.
    assert (created["start_time"], created["end_time"]) == ("00:50", "07:00")


@pytest.mark.asyncio
async def test_regenerate_with_new_cleanup_recleans_the_planned_ranges(isolated_clip_edits, monkeypatch, tmp_path):
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"clip")
    save_clip_source_ranges(clip_path, CLEANED, OFFSETS)
    captured = {}
    rendered = [{"clip_id": 1, "filename": "new.mp4", "path": "new.mp4", "start_time": "01:40",
                 "end_time": "02:10", "duration": 58.8}]
    service = _service(monkeypatch, [_clip(clip_path)], rendered, captured, CLEANUP_OFF)

    await service.regenerate_all_clips_for_task(
        "task-1", None, None, None, "default", cleanup_settings={**CLEANUP_OFF, "cut_long_pauses": True},
    )

    (segment,) = captured["segments"]
    assert segment["source_ranges"] == [tuple(item) for item in PLANNED]
    assert segment["junction_fades"] == [0.04, 0.04]
    assert "keep_ranges" not in segment
    # Less cleanup leaves more pause; the planned ranges still play to their end.
    assert segment["max_seconds"] == 60.0


@pytest.mark.asyncio
async def test_regenerate_never_drops_a_series_part(isolated_clip_edits, monkeypatch, tmp_path):
    clips = []
    for number in (1, 2):
        path = tmp_path / f"part{number}.mp4"
        path.write_bytes(b"clip")
        ranges = [[number * 100.0, number * 100.0 + 60.0]]
        save_clip_source_ranges(path, [tuple(ranges[0])])
        clips.append(_clip(path, workflow_role="part", part_number=number,
                           workflow_meta={"planned": True, "ranges": ranges, "junction_fades": [], "max_seconds": 155.0}))
    rendered = [{"clip_id": 1, "filename": "p1.mp4", "path": "p1.mp4", "start_time": "01:40", "end_time": "02:40",
                 "duration": 60.0}]
    service = _service(monkeypatch, clips, rendered, {}, CLEANUP_OFF)

    with pytest.raises(ValueError, match="existing clips are unchanged"):
        await service.regenerate_all_clips_for_task("task-1", None, None, None, "default")

    service.clip_repo.delete_clips_by_task.assert_not_awaited()


def test_lost_source_map_falls_back_to_the_planned_ranges(tmp_path):
    clip = _clip(tmp_path / "missing.mp4")

    assert TaskService._get_clip_source_ranges(clip) == [tuple(item) for item in PLANNED]
    assert TaskService._get_clip_source_ranges({**clip, "workflow_meta": {}}) == [(50.0, 420.0)]


def test_lost_source_map_regenerates_from_the_planned_cuts(tmp_path):
    clip = _clip(tmp_path / "missing.mp4")
    service = TaskService(db=None)

    segment = service._planned_regeneration_segment(clip, [tuple(item) for item in PLANNED], [], False)

    assert segment["source_ranges"] == [tuple(item) for item in PLANNED]
    assert segment["junction_fades"] == [0.04, 0.04]


@pytest.mark.asyncio
async def test_merged_parts_regenerate_as_an_edited_clip_with_their_role(isolated_clip_edits, monkeypatch, tmp_path):
    # Parts 1 and 2 merged: the row keeps part 1's plan, the map holds both parts.
    clip_path = tmp_path / "merged.mp4"
    clip_path.write_bytes(b"clip")
    played = [(100.0, 160.0), (160.0, 215.0)]
    save_clip_source_ranges(clip_path, played, [0.0, 60.0])
    meta = {"planned": True, "ranges": [[100.0, 160.0]], "junction_fades": [], "max_seconds": 155.0,
            "series_title": "The story", "part_count": 4, "title": "Start"}
    clip = _clip(clip_path, workflow_role="part", part_number=1, workflow_meta=meta,
                 start_time="01:40", end_time="03:35")
    captured = {}
    rendered = [{"clip_id": 1, "filename": "new.mp4", "path": "new.mp4", "start_time": "01:40",
                 "end_time": "03:35", "duration": 115.0}]
    service = _service(monkeypatch, [clip], rendered, captured, CLEANUP_OFF)

    await service.regenerate_all_clips_for_task("task-1", None, None, None, "default", cleanup_settings=CLEANUP_OFF)

    (segment,) = captured["segments"]
    # Both parts play again, uncapped by part 1's plan.
    assert segment["keep_ranges"] == played
    assert not segment.get("planned")
    assert segment["required"] is True
    created = service.clip_repo.create_clip.await_args.kwargs
    assert (created["workflow_role"], created["part_number"]) == ("part", 1)
    assert created["workflow_meta"] == {"series_title": "The story", "part_count": 4, "title": "Start"}


def test_merged_clip_with_a_lost_map_falls_back_to_its_labels(tmp_path):
    meta = {"planned": True, "ranges": [[100.0, 160.0]]}
    merged = _clip(tmp_path / "missing.mp4", workflow_meta=meta, start_time="01:40", end_time="03:35")
    untouched = _clip(tmp_path / "missing.mp4", workflow_meta=meta, start_time="01:40", end_time="02:40")

    assert TaskService._get_clip_source_ranges(merged) == [(100.0, 215.0)]
    assert TaskService._get_clip_source_ranges(untouched) == [(100.0, 160.0)]


def test_footage_outside_the_plan_is_not_narrowed_onto_it():
    assert _narrow_planned_meta(_meta(), [(100.0, 112.0), (700.0, 720.0)], [0.0, 12.0]) is None
    # A merged clip whose map was lost replays its labels, which reach past the plan.
    assert _narrow_planned_meta(_meta(), [(100.0, 215.0)], [0.0]) is None


def test_merged_clip_with_a_lost_map_regenerates_from_its_labels(tmp_path):
    meta = {"planned": True, "ranges": [[100.0, 160.0]], "junction_fades": [], "max_seconds": 155.0}
    clip = _clip(tmp_path / "missing.mp4", workflow_role="part", part_number=1, workflow_meta=meta,
                 start_time="01:40", end_time="03:35")

    ranges = TaskService._get_clip_source_ranges(clip)

    assert TaskService(db=None)._planned_regeneration_segment(clip, ranges, [], False) is None


def test_range_groups_follow_containment_in_play_order():
    assert _range_groups(CLEANED, PLANNED) == [0, 0, 1, 2]


def test_narrowed_meta_keeps_what_a_half_still_plays():
    # The second half of a split at 20s: the rest of the cold open, then both other beats.
    pieces = [(108.0, 112.0), (113.0, 130.0), (400.0, 420.0), (50.0, 60.0)]
    offsets = [0.0, 3.88, 20.84, 40.8]

    meta = _narrow_planned_meta(_meta(), pieces, offsets)

    assert meta["ranges"] == [[108.0, 130.0], [400.0, 420.0], [50.0, 60.0]]
    assert meta["junction_fades"] == [0.04, 0.04]
    assert [(beat["role"], beat["range_index"], beat["output_start"]) for beat in meta["beats"]] == [
        ("cold_open", 0, 0.0), ("escalation", 1, 20.84), ("button", 2, 40.8),
    ]

    first = _narrow_planned_meta(_meta(), [(100.0, 108.0)], [0.0])
    assert first["ranges"] == [[100.0, 108.0]]
    assert first["junction_fades"] == []
    assert [beat["role"] for beat in first["beats"]] == ["cold_open"]


@pytest.mark.asyncio
async def test_split_second_half_keeps_role_part_and_narrowed_meta(isolated_clip_edits, monkeypatch, tmp_path):
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"clip")
    save_clip_source_ranges(clip_path, CLEANED, OFFSETS)
    first, second = tmp_path / "first.mp4", tmp_path / "second.mp4"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    monkeypatch.setattr(clip_service_module, "split_clip_file", lambda *_args: (first, second))
    monkeypatch.setattr(clip_service_module, "rendered_clip_duration", lambda *_args: 10.0)
    service = TaskService(db=None)
    clip = _clip(clip_path, workflow_role="part", part_number=3)
    service.clip_repo.get_clip_by_id = AsyncMock(return_value=clip)
    service.clip_repo.update_clip = AsyncMock()
    service.clip_repo.make_room_for_clip = AsyncMock()
    service.clip_repo.create_clip = AsyncMock(return_value="clip-2")
    service.clip_repo.reorder_task_clips = AsyncMock()

    await service.split_clip("task-1", "clip-1", 20.0)

    created = service.clip_repo.create_clip.await_args.kwargs
    assert created["workflow_role"] == "part"
    assert created["part_number"] == 3
    assert created["workflow_meta"]["split"] == 2
    # 20s into the clip is 8.12s into the second cleaned piece of the cold open.
    assert created["workflow_meta"]["ranges"][0] == [pytest.approx(121.12), 130.0]
    assert created["workflow_meta"]["ranges"][1:] == [[400.0, 420.0], [50.0, 60.0]]
    assert created["workflow_meta"]["planned"] is True


@pytest.mark.asyncio
async def test_split_of_a_highlight_adds_no_workflow_fields(isolated_clip_edits, monkeypatch, tmp_path):
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"clip")
    save_clip_source_ranges(clip_path, [(10.0, 40.0)])
    first, second = tmp_path / "first.mp4", tmp_path / "second.mp4"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    monkeypatch.setattr(clip_service_module, "split_clip_file", lambda *_args: (first, second))
    monkeypatch.setattr(clip_service_module, "rendered_clip_duration", lambda *_args: 10.0)
    service = TaskService(db=None)
    service.clip_repo.get_clip_by_id = AsyncMock(return_value=_clip(clip_path, workflow_role=None, workflow_meta={}))
    service.clip_repo.update_clip = AsyncMock()
    service.clip_repo.make_room_for_clip = AsyncMock()
    service.clip_repo.create_clip = AsyncMock(return_value="clip-2")
    service.clip_repo.reorder_task_clips = AsyncMock()

    await service.split_clip("task-1", "clip-1", 10.0)

    assert not {"workflow_role", "part_number", "workflow_meta"} & set(service.clip_repo.create_clip.await_args.kwargs)
