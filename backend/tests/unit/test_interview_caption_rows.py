"""Per-shot caption rows on interview clips (``edit_fx.caption_rows``): the shots → rows rules, the stored layout, the
studio draft (seeding, gating, mapping through cuts) and combining drafts. The generation path (``_fx_ladder_setup``)
is in test_fx_ladder.py, fx-core's placement of groups on rows in the shared fixtures (shared/fx/fixtures/rows)."""

import json
import re
import shutil
import subprocess
import sys
import types
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.caption_templates import get_template
from src.clip_source_map import load_clip_vertical_layout, save_clip_vertical_layout
from src.edit_fx.caption_rows import (
    FLASH_SHOT_SECONDS,
    ROW_GAP_FRAC,
    TWO_LINE_HALF_EM,
    fit_picture_band,
    group_rows,
    interview_caption_rows,
    mapped_caption_rows,
    shot_runs,
)
from src.edit_fx.caption_groups import CUT_LEAD
from src.edit_fx.contracts import CaptionRow, caption_row_y
from src.editor_document import EditDocument, editor_dir, studio_caption_rows
from src.services.editor_service import (
    combine_editor,
    combined_caption_rows,
    editor_caption_rows,
    rebased_caption_rows,
    stored_editor_layout,
)
from tests.fixtures.editor_store import stored_state


def shots(*spec):
    return [{"start": a, "end": b, "kind": k} for a, b, k in spec]


def rows_of(rows):
    return [(r.start, r.end, r.y) for r in rows]


# ----------------------------------------------------------------------------------------------- shots and flashes

def test_a_flash_in_the_middle_keeps_the_row_before_it():
    runs = shot_runs(shots((0, 3, "pair"), (3, 3.6, "single"), (3.6, 6, "pair"), (6, 9, "single")))
    assert runs == [(0, 6, "pair"), (6, 9, "single")]


def test_a_clip_opening_on_a_flash_takes_the_next_shot_and_one_ending_on_a_flash_the_last():
    assert shot_runs(shots((0, 0.3, "single"), (0.3, 4, "pair"), (4, 4.5, "fit"))) == [(0, 4.5, "pair")]
    # Several flashes in a row before the first real shot.
    assert shot_runs(shots((0, 0.2, "fit"), (0.2, 0.9, "single"), (0.9, 5, "pair"))) == [(0, 5, "pair")]


def test_a_clip_made_only_of_flashes_keeps_its_first_shot():
    assert shot_runs(shots((0, 0.5, "single"), (0.5, 0.9, "pair"))) == [(0, 0.9, "single")]


def test_a_shot_of_exactly_the_flash_length_counts():
    assert shot_runs(shots((0, 3, "pair"), (3, 3 + FLASH_SHOT_SECONDS, "single"))) == [
        (0, 3, "pair"), (3, 3 + FLASH_SHOT_SECONDS, "single")]


def test_unreadable_shots_are_ignored():
    raw = [{"start": 0, "end": 2, "kind": "pair"}, {"start": 2, "end": 2, "kind": "single"}, {"start": "x"},
           {"start": 2, "end": 5, "kind": "crowd"}, "nope", {"start": 3, "end": 6, "kind": "single"}]
    assert shot_runs(raw) == [(0, 2, "pair"), (3, 6, "single")]
    assert interview_caption_rows([], text_px=72, width=1080, height=1920) == ()


# ------------------------------------------------------------------------------------------------- rows per shot

@pytest.mark.parametrize("kind,y", [("pair", 0.5), ("single", 0.65), ("fit", 0.76)])
def test_one_row_for_a_clip_of_one_kind(kind, y):
    rows = interview_caption_rows(shots((0, 9, kind)), text_px=72, width=1080, height=1920,
                                  source_size=(1920, 1080), duration=9)
    assert rows_of(rows) == [(0.0, 9.0, y)]


def test_pair_single_pair_alternation_rows_change_exactly_at_the_cuts():
    segments = shots((0, 2.5, "pair"), (2.5, 5.0, "single"), (5.0, 7.5, "pair"), (7.5, 10, "single"))
    rows = interview_caption_rows(segments, text_px=72, width=1080, height=1920, duration=10)
    assert rows_of(rows) == [(0, 2.5, 0.5), (2.5, 5.0, 0.65), (5.0, 7.5, 0.5), (7.5, 10.0, 0.65)]


def test_the_fit_row_clears_the_picture_band_for_any_aspect():
    assert fit_picture_band((1920, 1080)) == pytest.approx((0.3418, 0.6582), abs=1e-4)
    for size in ((1920, 1080), (1440, 1080), (2560, 1080)):
        top, bottom = fit_picture_band(size)
        for px in (52, 72, 84):
            (row,) = interview_caption_rows(shots((0, 5, "fit")), text_px=px, width=1080, height=1920,
                                            source_size=size)
            half = TWO_LINE_HALF_EM * px / 1920       # two caption lines
            assert row.y - half >= bottom + ROW_GAP_FRAC - 1e-4, (size, px, row.y)   # rows round to 1e-4
            assert row.y >= 0.76
    # A picture as tall as the frame: nothing to clear, the row stays put.
    assert interview_caption_rows(shots((0, 5, "fit")), text_px=72, width=1080, height=1920,
                                  source_size=(1080, 1920))[0].y == 0.76


def test_burned_in_source_captions_push_each_row_clear():
    band = (0.60, 0.70)
    rows = interview_caption_rows(shots((0, 3, "pair"), (3, 6, "single"), (6, 9, "fit")), text_px=72, width=1080,
                                  height=1920, source_size=(1920, 1080), band=band)
    text = 72 * 1.5 / 1920
    for row in rows:
        assert row.y + text / 2 <= band[0] or row.y - text / 2 >= band[1], row
    assert rows[0].y == 0.5 and rows[1].y < 0.6 and rows[2].y == 0.76


def test_rows_are_fractions_at_720p():
    segments = shots((0, 3, "pair"), (3, 6, "single"), (6, 9, "fit"))
    full = interview_caption_rows(segments, text_px=72, width=1080, height=1920, source_size=(1920, 1080),
                                  band=(0.62, 0.7))
    small = interview_caption_rows(segments, text_px=48, width=720, height=1280, source_size=(1920, 1080),
                                   band=(0.62, 0.7))
    assert [r.y for r in full] == pytest.approx([r.y for r in small], abs=2e-3)


def test_rows_cover_the_clip_from_zero_to_its_duration():
    rows = interview_caption_rows(shots((0.04, 3, "pair"), (3, 6.2, "single")), text_px=72, width=1080,
                                  height=1920, duration=6.0)
    assert rows[0].start == 0.0 and rows[-1].end == 6.0


# ----------------------------------------------------------------------------------------------- the hook title

# --------------------------------------------------------------------------------------- groups and the edit clock

def words(*spec):
    return [{"start": a, "end": b, "text": t} for a, b, t in spec]


def test_groups_without_timings_have_no_rows_to_take():
    rows = [CaptionRow(start=0, end=2, y=0.5), CaptionRow(start=2, end=9, y=0.65)]
    assert group_rows([], 3, rows, 0.5) == []


def _table(groups, talk):
    return [(" ".join(w["text"] for w in talk[g["first"]:g["last"] + 1]), g["y"], g["cut"], g["until"]) for g in groups]


def test_a_cut_ends_the_group_on_screen_whichever_way_it_goes():
    talk = words((0.2, 0.5, "We"), (0.55, 0.9, "went"), (0.95, 1.4, "there."), (2.2, 2.5, "It"), (2.55, 3.0, "was"))
    for old, new in ((0.5, 0.65), (0.65, 0.5), (0.76, 0.5), (0.5, 0.76)):
        rows = [CaptionRow(start=0, end=0.7, y=old), CaptionRow(start=0.7, end=9, y=new)]
        assert _table(group_rows(talk, 3, rows, 0.5), talk) == [
            ("We", old, None, 0.7), ("went there.", new, 0.7, None), ("It was", new, None, None)]


def test_a_word_within_the_cut_lead_goes_with_the_next_shot_and_shows_from_the_cut():
    talk = words((0.2, 0.5, "We"), (0.55, 0.9, "went"), (0.95, 1.4, "there."))
    cut = 0.55 + CUT_LEAD - 0.05                     # "went" starts 0.1 s before the cut
    rows = [CaptionRow(start=0, end=cut, y=0.5), CaptionRow(start=cut, end=9, y=0.65)]
    assert _table(group_rows(talk, 3, rows, 0.5), talk) == [("We", 0.5, None, cut), ("went there.", 0.65, cut, None)]
    # The whole group within the lead: no stub left behind, it all belongs to the new shot.
    rows = [CaptionRow(start=0, end=0.3, y=0.5), CaptionRow(start=0.3, end=9, y=0.65)]
    assert _table(group_rows(talk, 3, rows, 0.5), talk) == [("We went there.", 0.65, 0.3, None)]


def test_cuts_never_change_grouping_without_a_change_of_row():
    talk = words((0.2, 0.5, "We"), (0.55, 0.9, "went"), (0.95, 1.4, "there."), (2.2, 2.5, "It"), (2.55, 3.0, "was"))
    from src.edit_fx.caption_groups import phrase_groups

    same = [CaptionRow(start=0, end=0.7, y=0.65), CaptionRow(start=0.7, end=9, y=0.65)]
    assert [(g["first"], g["last"], g["end"]) for g in group_rows(talk, 3, same, 0.5)] == [
        (g.first, g.last, g.end) for g in phrase_groups(talk, 3)]


def test_studio_rows_map_through_trims_reorders_and_repeats():
    stored = [CaptionRow(start=0, end=2, y=0.5), CaptionRow(start=2, end=4, y=0.65),
              CaptionRow(start=4, end=6, y=0.76)]
    assert rows_of(mapped_caption_rows([("a", 1.0, 5.0)], stored)) == [(0, 1, 0.5), (1, 3, 0.65), (3, 4, 0.76)]
    assert rows_of(mapped_caption_rows([("b", 4.5, 6.0), ("a", 0.0, 1.0)], stored)) == [(0, 1.5, 0.76), (1.5, 2.5, 0.5)]
    assert rows_of(mapped_caption_rows([("a", 1.0, 3.0), ("b", 1.0, 3.0)], stored)) == [
        (0, 1, 0.5), (1, 2, 0.65), (2, 3, 0.5), (3, 4, 0.65)]


# ------------------------------------------------------------------------------------------------ stored layout

def test_the_vertical_layout_keeps_its_shots_and_caption_band(tmp_path):
    clip = tmp_path / "clip.mp4"
    layout = {"kind": "interview", "video_filter": "[0:v]null[vout]", "mode": "complex", "width": 1920,
              "height": 1080, "duration": 9.0, "segments": shots((0, 4.5, "pair"), (4.5, 9.0, "single")),
              "caption_band": (0.61234, 0.7)}
    save_clip_vertical_layout(clip, layout)
    loaded = load_clip_vertical_layout(clip)
    assert loaded["segments"] == shots((0.0, 4.5, "pair"), (4.5, 9.0, "single"))
    assert loaded["caption_band"] == [0.6123, 0.7]
    # Older layouts (no shots) and unreadable shots still replay; only the shots are dropped.
    for bad in ([{"start": 0, "end": 1, "kind": "crowd"}], "x", [{"start": 2, "end": 1, "kind": "pair"}]):
        path = tmp_path / "sidecar.json"
        payload = json.loads(clip.with_suffix(".mp4.source_map.json").read_text()) \
            if clip.with_suffix(".mp4.source_map.json").exists() else None
        assert payload is None or "vertical_layout" in payload
        from src.clip_source_map import clip_source_map_path

        data = json.loads(clip_source_map_path(clip).read_text())
        data["vertical_layout"]["segments"] = bad
        clip_source_map_path(clip).write_text(json.dumps(data))
        replay = load_clip_vertical_layout(clip)
        assert replay is not None and "segments" not in replay
    # A filter that reads another file is still refused, shots or not.
    save_clip_vertical_layout(clip, {**layout, "video_filter": "movie=/etc/passwd[a];[a]null[vout]"})
    assert load_clip_vertical_layout(clip) is None


def test_the_editor_replays_the_stored_shots(tmp_path, monkeypatch):
    clean = tmp_path / "clean.mp4"
    stored = {"kind": "interview", "video_filter": "null", "mode": "complex", "width": 1920, "height": 1080,
              "duration": 9.0, "segments": shots((0, 9, "pair")), "caption_band": [0.6, 0.7]}
    monkeypatch.setattr("src.media.reframing.ffprobe_video_size", lambda _p: (1920, 1080))
    monkeypatch.setattr("src.media.ffmpeg.ffprobe_duration", lambda _p: 9.0)
    layout = stored_editor_layout(clean, stored)
    assert layout["segments"] == stored["segments"] and layout["source_size"] == [1920, 1080]
    assert layout["caption_band"] == [0.6, 0.7]
    assert "segments" not in stored_editor_layout(clean, {**stored, "segments": None})


# --------------------------------------------------------------------------------------------------- the studio

CAPTIONS = {"template": "highlighter_swipe", "y": 0.5, "size": 72}
LAYOUT = {"baked": True, "kind": "interview", "segments": shots((0, 2, "pair"), (2, 4, "single"), (4, 6, "fit")),
          "source_size": [1920, 1080]}


def test_a_baked_interview_layout_seeds_the_draft_rows():
    seeded = editor_caption_rows(LAYOUT, CAPTIONS, 6.0)
    assert seeded == {"y": 0.5, "rows": [{"start": 0.0, "end": 2.0, "y": 0.5},
                                         {"start": 2.0, "end": 4.0, "y": 0.65},
                                         {"start": 4.0, "end": 6.0, "y": 0.76}]}


def test_no_rows_for_other_layouts_templates_or_old_layouts():
    assert editor_caption_rows({**LAYOUT, "baked": False}, CAPTIONS, 6.0) is None
    assert editor_caption_rows({**LAYOUT, "kind": "stack"}, CAPTIONS, 6.0) is None
    assert editor_caption_rows({**LAYOUT, "segments": None}, CAPTIONS, 6.0) is None      # stored before rows
    assert editor_caption_rows(LAYOUT, {**CAPTIONS, "template": "hormozi"}, 6.0) is None  # legacy ASS captions
    assert editor_caption_rows(LAYOUT, {**CAPTIONS, "template": "marker_circle"}, 6.0) is None


def interview_doc(**over) -> EditDocument:
    data = {
        "segments": [{"id": "all", "start": 0, "end": 6}],
        "words": [{"id": "w0", "start": 0.2, "end": 0.6, "text": "hi"}],
        "captions": {"template": "highlighter_swipe", "y": 0.5},
        "captionRows": editor_caption_rows(LAYOUT, CAPTIONS, 6.0),
        **over,
    }
    return EditDocument.model_validate(data)


def test_studio_rows_apply_only_while_the_captions_keep_their_automatic_position():
    assert rows_of(studio_caption_rows(interview_doc())) == [(0, 2, 0.5), (2, 4, 0.65), (4, 6, 0.76)]
    assert studio_caption_rows(interview_doc(captions={"template": "highlighter_swipe", "y": 0.6})) == []
    assert studio_caption_rows(interview_doc(textTransforms={"captions": {"x": 0, "y": 0.05, "scale": 1}})) == []
    assert studio_caption_rows(interview_doc(textTransforms={"captions": {"x": 0.1, "y": 0, "scale": 1.4}})) != []
    assert studio_caption_rows(interview_doc(framing={"aspect": "square"})) == []
    assert studio_caption_rows(interview_doc(framing={"zoom": 1.5})) == []
    assert studio_caption_rows(interview_doc(captions={"template": "slam", "y": 0.5})) == []
    assert studio_caption_rows(interview_doc(captions={"template": "highlighter_swipe", "y": 0.5,
                                                       "enabled": False})) == []
    assert studio_caption_rows(interview_doc(captionRows=None)) == []


def test_a_draft_without_rows_serialises_as_before():
    plain = interview_doc(captionRows=None)
    assert "captionRows" not in plain.model_dump() and "captionRows" not in plain.model_dump(mode="json")
    assert interview_doc().model_dump(mode="json")["captionRows"]["rows"][1]["y"] == 0.65


def test_studio_caption_style_carries_the_mapped_rows():
    from src.editor_fx_render import studio_caption_style

    doc = interview_doc(segments=[{"id": "late", "start": 3, "end": 6}, {"id": "early", "start": 0, "end": 1}])
    style = studio_caption_style(doc, 1080, 1920)
    assert rows_of(style.rows) == [(0, 1, 0.65), (1, 3, 0.76), (3, 4, 0.5)]
    assert style.y == 0.5 and style.look == "bold"


def test_combined_drafts_rebase_each_parts_rows():
    first = interview_doc(segments=[{"id": "a", "start": 1, "end": 3}])
    pinned = interview_doc(captions={"template": "highlighter_swipe", "y": 0.7})
    second = interview_doc(segments=[{"id": "b", "start": 3, "end": 5}])
    rows = rebased_caption_rows(first, 0.0) + rebased_caption_rows(pinned, 2.0) + rebased_caption_rows(second, 8.0)
    assert [(r["start"], r["end"], r["y"]) for r in rows] == [(0.0, 1.0, 0.5), (1.0, 2.0, 0.65),
                                                              (8.0, 9.0, 0.65), (9.0, 10.0, 0.76)]
    combined = combined_caption_rows([first.model_dump(mode="json")], rows)
    assert combined["y"] == 0.5 and len(combined["rows"]) == 4
    assert combined_caption_rows([first.model_dump(mode="json")], []) is None


@pytest.fixture
def fake_fx_renderer(monkeypatch):
    rendered = []
    module = types.ModuleType("src.editor_fx_render")

    def render_document_fx(document, ctx):
        rendered.append(document)
        shutil.copy2(ctx.input_path, ctx.output_path)
        return SimpleNamespace(level="full", applied_warp=[])

    @asynccontextmanager
    async def studio_fx_inputs(document, asset, directory, *, storage=None):
        yield None

    module.render_document_fx = render_document_fx
    module.studio_fx_inputs = studio_fx_inputs
    monkeypatch.setitem(sys.modules, "src.editor_fx_render", module)
    monkeypatch.setattr(sys.modules["src"], "editor_fx_render", module, raising=False)
    return rendered


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
async def test_combining_interview_drafts_keeps_each_parts_rows(tmp_path, monkeypatch, editor_store,
                                                                 fake_fx_renderer):
    from src.config import Config

    config = Config()
    config.temp_dir = str(tmp_path)
    monkeypatch.setattr("src.config.get_config", lambda: config)
    clips = [{"id": f"clip{i}", "filename": f"clip{i}.mp4", "clip_order": i} for i in (1, 2)]
    documents = [interview_doc(segments=[{"id": "a", "start": 1, "end": 3}]),
                 interview_doc(segments=[{"id": "b", "start": 3.5, "end": 5}])]
    entries = []
    for clip, document in zip(clips, documents):
        directory = editor_dir(str(tmp_path), "task", clip["id"], clip["filename"])
        directory.mkdir(parents=True)
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=blue:size=108x192:rate=30:duration=6",
             "-c:v", "libx264", str(directory / "clean.mp4")],
            capture_output=True, check=True,
        )
        entries.append({**clip, "document": document.model_dump(mode="json"),
                        "state": {"width": 108, "height": 192, "hasAudio": False}, "ranges": [[0, 6]]})
    key = editor_store.put_ready("task", "clip1", "clip1.mp4", {"duration": 6}, entries[0]["document"])
    job = "e" * 32
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
    document = EditDocument.model_validate(combined["draft"]["document"])
    # Part 1 (S 1..3 → combined 0..2) then part 2 (S 3.5..5 → combined 2..3.5), each with its own shots.
    assert [(r.start, r.end, r.y) for r in document.captionRows.rows] == [
        (0.0, 1.0, 0.5), (1.0, 2.0, 0.65), (2.0, 2.5, 0.65), (2.5, 3.5, 0.76)]
    assert rows_of(studio_caption_rows(document)) == [(0, 1, 0.5), (1, 2.5, 0.65), (2.5, 3.5, 0.76)]
    # The render drew the combined draft with those rows.
    assert fake_fx_renderer and fake_fx_renderer[0].captionRows is not None


# ------------------------------------------------------------------------------------ clip edits and re-renders

def test_fx_clip_rerenders_take_fresh_rows_unless_a_caption_edit_pinned_the_position():
    """Trim/split/merge/settings re-renders run create_optimized_clip again (rows from the freshly planned layout):
    the automatic caption settings a render stores are not a pin; only a caption edit's position is."""
    from src.services.clip_service import _stored_caption_override

    automatic = {"position_y": 0.5, "position_is_center": True}
    assert _stored_caption_override(automatic, {"font_size": 40}) is None
    edited = {"position": "bottom", "position_y": 0.78, "highlight_words": [], "font_size": 40}
    assert _stored_caption_override(edited, {})["position_y"] == 0.78
