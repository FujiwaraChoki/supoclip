import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from src.api.routes import editor
from src.broll import editor_piece_to_placement, placement_to_editor_clip
from src.database import get_db
from src.editor_document import EditDocument, atomic_json, editor_dir, mapped_broll, render_document
from src.services.editor_service import editor_broll_from_placements, upgrade_document

FOOTAGE = {
    "id": 42,
    "width": 1080,
    "height": 1920,
    "duration": 10,
    "fileUrl": "https://videos.pexels.com/video-files/42/42-hd_1080_1920_25fps.mp4",
    "pageUrl": "https://www.pexels.com/video/42/",
    "thumbnail": "https://images.pexels.com/videos/42/pictures/preview-0.jpg",
    "credit": "Some Filmmaker",
}


def doc(segments=((0, 6),), broll=(), **extra):
    return EditDocument.model_validate(
        {
            "segments": [{"id": f"s{i}", "start": a, "end": b} for i, (a, b) in enumerate(segments)],
            "broll": [
                {"id": f"b{i}", "start": a, "end": b, "offset": o, "footage": FOOTAGE}
                for i, (a, b, o) in enumerate(broll)
            ],
            **extra,
        }
    )


# --- document model ----------------------------------------------------------


def test_footage_can_only_come_from_the_pexels_video_cdn():
    for url in ("http://169.254.169.254/latest", "https://evil.example/a.mp4", "file:///etc/passwd"):
        with pytest.raises(ValidationError):
            EditDocument.model_validate(
                {
                    "segments": [{"id": "s", "start": 0, "end": 2}],
                    "broll": [{"id": "b", "start": 0, "end": 1, "footage": {**FOOTAGE, "fileUrl": url}}],
                }
            )


def test_broll_is_validated_against_the_clip():
    with pytest.raises(ValueError, match="overlap"):
        doc(broll=((1, 3, 0), (2, 4, 0))).validate_duration(6)
    with pytest.raises(ValueError, match="past the original"):
        doc(broll=((5, 7, 0),)).validate_duration(6)
    with pytest.raises(ValidationError):
        doc(broll=((1, 1.1, 0),))
    # Touching clips are fine.
    doc(broll=((1, 3, 0), (3, 5, 0))).validate_duration(6)


def test_old_drafts_without_broll_still_load():
    loaded = EditDocument.model_validate({"segments": [{"id": "s", "start": 0, "end": 2}]})
    assert loaded.broll == []


# --- timeline mapping --------------------------------------------------------


def test_broll_follows_cuts_and_keeps_its_footage_position():
    # Cut out 2-3 s; the cutaway spans the cut.
    pieces = mapped_broll(doc(segments=((0, 2), (3, 6)), broll=((1, 4, 0.5),)))

    assert [(p["start"], p["duration"], p["offset"]) for p in pieces] == [(1, 1, 0.5), (2, 1, 2.5)]
    # One dissolve in at its start and one out at its end, none at the cut.
    assert [(p["fade_in"], p["fade_out"]) for p in pieces] == [(True, False), (False, True)]


def test_broll_follows_reordered_and_duplicated_segments():
    pieces = mapped_broll(doc(segments=((3, 6), (0, 3), (0, 3)), broll=((1, 2, 0),)))

    assert [p["start"] for p in pieces] == [4, 7]


def test_cut_out_broll_disappears():
    assert mapped_broll(doc(segments=((0, 2), (4, 6)), broll=((2.5, 3.5, 0),))) == []


# --- generated clips <-> editor ----------------------------------------------


def _placement(start=4.0, duration=3.0, **extra):
    return {
        "start": start,
        "duration": duration,
        "query": "mountain summit",
        "provider": "pexels",
        "pexels_id": 42,
        "pexels_url": FOOTAGE["pageUrl"],
        "file_url": FOOTAGE["fileUrl"],
        "width": 1080,
        "height": 1920,
        "footage_duration": 10.0,
        "thumbnail": FOOTAGE["thumbnail"],
        "credit": "Some Filmmaker",
        **extra,
    }


def test_generated_broll_opens_as_editable_clips():
    clips = editor_broll_from_placements(
        [_placement(), _placement(start=5.0), _placement(start=12.0, footage_offset=1.5)], 20.0
    )

    # The overlapping second placement is dropped; the rest map one to one.
    assert [(c["start"], c["end"], c["offset"]) for c in clips] == [(4.0, 7.0, 0.0), (12.0, 15.0, 1.5)]
    assert clips[0]["footage"]["fileUrl"] == FOOTAGE["fileUrl"]
    doc_with = EditDocument.model_validate({"segments": [{"id": "s", "start": 0, "end": 20}], "broll": clips})
    assert len(doc_with.broll) == 2


def test_placements_with_foreign_urls_are_not_imported():
    assert placement_to_editor_clip(_placement(file_url="https://evil.example/x.mp4"), 0, 20.0) is None


def test_editor_pieces_round_trip_to_placements():
    piece = mapped_broll(doc(broll=((1, 4, 0.5),)))[0]
    placement = editor_piece_to_placement(piece)

    assert placement["start"] == 1 and placement["duration"] == 3 and placement["footage_offset"] == 0.5
    clip = placement_to_editor_clip(placement, 0, 6.0)
    assert (clip["start"], clip["end"], clip["offset"]) == (1, 4, 0.5)


def test_upgrade_seeds_broll_only_for_drafts_without_it():
    old = doc().model_dump()
    old.pop("broll")
    upgraded = upgrade_document(old, {"hook_title": ""}, {}, [_placement()], 20.0)
    assert len(upgraded["broll"]) == 1

    # A draft whose editor removed all B-roll keeps that choice.
    kept = upgrade_document(doc().model_dump(), {"hook_title": ""}, {}, [_placement()], 20.0)
    assert kept["broll"] == []


# --- server export -----------------------------------------------------------


def _solid(path, color, seconds, size):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s={size}:r=24:d={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )


def _pixel(path, at):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{at}", "-i", str(path), "-frames:v", "1",
         "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True,
    ).stdout
    return raw[0], raw[1], raw[2]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_export_composites_broll_across_a_cut(tmp_path):
    _solid(tmp_path / "clean.mp4", "red", 6, "320x180")
    footage = tmp_path / "footage.mp4"
    _solid(footage, "blue", 8, "180x320")
    draft = doc(
        segments=((0, 2), (3, 6)),
        broll=((1, 4, 0),),
        framing={"aspect": "original"},
        captions={"enabled": False},
    )

    render_document(tmp_path, "b" * 32, draft, "tiktok", {"42/1080x1920": footage})

    output = tmp_path / f"export-{'b' * 32}.mp4"
    before, first, second, after = (_pixel(output, t) for t in (0.5, 1.5, 2.5, 4.0))
    assert before[0] > 200 and before[2] < 60
    assert first[2] > 200 and first[0] < 60
    assert second[2] > 200 and second[0] < 60  # still covered right after the cut
    assert after[0] > 200 and after[2] < 60


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_export_without_footage_files_renders_the_clip_alone(tmp_path):
    _solid(tmp_path / "clean.mp4", "red", 3, "320x180")
    draft = doc(segments=((0, 3),), broll=((1, 2, 0),), framing={"aspect": "original"}, captions={"enabled": False})

    render_document(tmp_path, "c" * 32, draft, "tiktok", {})

    assert _pixel(tmp_path / f"export-{'c' * 32}.mp4", 1.5)[0] > 200


# --- routes --------------------------------------------------------------------


@pytest.fixture
async def client(tmp_path, monkeypatch):
    task = {"id": "task", "user_id": "owner", "status": "completed"}
    clip = {"id": "clip", "task_id": "task", "filename": "clip.mp4", "file_path": str(tmp_path / "clip.mp4")}
    service = SimpleNamespace(
        task_repo=SimpleNamespace(get_task_by_id=AsyncMock(return_value=task)),
        clip_repo=SimpleNamespace(get_clip_by_id=AsyncMock(return_value=clip)),
        config=SimpleNamespace(temp_dir=str(tmp_path)),
    )
    monkeypatch.setattr(editor, "TaskService", lambda db: service)
    monkeypatch.setattr("src.api.routes.tasks._get_user_id_from_headers", AsyncMock(return_value="owner"))
    monkeypatch.setattr("src.broll.broll_configured", lambda: True)
    app = FastAPI()
    app.include_router(editor.router)
    app.dependency_overrides[get_db] = lambda: SimpleNamespace(close=AsyncMock())
    directory = editor_dir(str(tmp_path), "task", "clip", "clip.mp4")
    draft = doc(
        segments=((0, 20),),
        words=[{"id": f"w{i}", "start": i * 0.5, "end": i * 0.5 + 0.4, "text": "word"} for i in range(38)],
        broll=((4, 7, 0),),
    ).model_dump()
    atomic_json(directory / "draft.json", {"revision": 0, "document": draft})
    atomic_json(directory / "original.json", draft)
    atomic_json(directory / "asset.json", {"status": "ready", "duration": 20, "width": 1920, "height": 1080})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http, draft, tmp_path


async def test_search_sizes_footage_for_the_frame(client, monkeypatch):
    http, _, _ = client
    calls = []

    async def fake_search(query, orientation, short_side, page):
        calls.append((query, orientation, short_side, page))
        return [FOOTAGE]

    monkeypatch.setattr("src.broll.search_footage", fake_search)

    vertical = await http.get("/tasks/task/clips/clip/editor/broll/search", params={"q": " coffee  shop "})
    original = await http.get(
        "/tasks/task/clips/clip/editor/broll/search", params={"q": "coffee", "aspect": "original", "page": 2}
    )

    assert vertical.status_code == 200 and vertical.json()["results"][0]["id"] == 42
    assert calls == [("coffee shop", "portrait", 1080, 1), ("coffee", "landscape", 1080, 2)]
    assert (await http.get("/tasks/task/clips/clip/editor/broll/search", params={"q": ""})).status_code == 400


async def test_search_requires_ownership_and_a_provider(client, monkeypatch):
    http, _, _ = client
    monkeypatch.setattr("src.broll.broll_configured", lambda: False)
    assert (await http.get("/tasks/task/clips/clip/editor/broll/search", params={"q": "x"})).status_code == 503
    monkeypatch.setattr("src.broll.broll_configured", lambda: True)
    monkeypatch.setattr("src.api.routes.tasks._get_user_id_from_headers", AsyncMock(return_value="outsider"))
    assert (await http.get("/tasks/task/clips/clip/editor/broll/search", params={"q": "x"})).status_code == 403


async def test_suggestions_avoid_existing_broll_and_cut_words(client, monkeypatch):
    http, draft, _ = client
    seen = {}

    async def fake_plan(words, duration, orientation, short_side, session):
        seen.update(words=words, used=set(session.used_ids), orientation=orientation)
        return [_placement(start=5.0), _placement(start=12.0)]

    monkeypatch.setattr("src.broll.plan_clip_broll", fake_plan)
    draft = {**draft, "segments": [{"id": "s0", "start": 0, "end": 10}]}

    response = await http.post("/tasks/task/clips/clip/editor/broll/suggest", json={"document": draft})

    assert response.status_code == 200
    suggestions = response.json()["broll"]
    # 5 s overlaps the existing 4-7 s clip; only 12 s comes back.
    assert [(s["start"], s["end"]) for s in suggestions] == [(12.0, 15.0)]
    assert seen["used"] == {42}
    assert all(w["start"] < 10 for w in seen["words"])


async def test_footage_is_streamed_for_the_owner_only(client, monkeypatch):
    http, _, tmp_path = client
    file = tmp_path / "cached.mp4"
    file.write_bytes(b"footage")
    requested = []

    async def fake_ensure(footage):
        requested.append(footage)
        return file

    monkeypatch.setattr("src.broll.ensure_footage_file", fake_ensure)

    response = await http.get("/tasks/task/clips/clip/editor/broll/footage/42/1080x1920.mp4")
    assert response.status_code == 200 and response.content == b"footage"
    assert requested[0]["id"] == 42 and (requested[0]["width"], requested[0]["height"]) == (1080, 1920)
    assert (await http.get("/tasks/task/clips/clip/editor/broll/footage/42/../../etc.mp4")).status_code == 404
    monkeypatch.setattr("src.api.routes.tasks._get_user_id_from_headers", AsyncMock(return_value="outsider"))
    assert (await http.get("/tasks/task/clips/clip/editor/broll/footage/42/1080x1920.mp4")).status_code == 403


async def test_editor_state_seeds_generated_broll_into_old_drafts(client, monkeypatch):
    http, draft, _ = client
    old = {k: v for k, v in draft.items() if k != "broll"}
    directory = editor_dir(str(client[2]), "task", "clip", "clip.mp4")
    atomic_json(directory / "draft.json", {"revision": 2, "document": old})
    monkeypatch.setattr(editor, "_clip_broll", lambda clip: [_placement(start=8.0)])

    state = (await http.get("/tasks/task/clips/clip/editor")).json()

    assert [(b["start"], b["end"]) for b in state["draft"]["document"]["broll"]] == [(8.0, 11.0)]
    # The original already had B-roll and is left as it was.
    assert [(b["start"], b["end"]) for b in state["original"]["broll"]] == [(4.0, 7.0)]
