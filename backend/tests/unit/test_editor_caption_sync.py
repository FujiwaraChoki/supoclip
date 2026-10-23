"""Caption timings follow speech, including section downloads and stored drafts."""

import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.clip_editor import (
    _caption_words_with_timings,
    apply_repaired_words,
    is_synthetic_caption_timing,
    resolve_speech_words,
)
from src.config import Config
from src.editor_document import EditDocument, atomic_json, editor_dir
from src.media_storage import MediaStorage
from src.services.editor_service import apply_saved_caption_repair, prepare_editor, read_state
from src.youtube_source import build_time_map, save_time_map
from tests.unit.test_media_storage import MemoryS3


VIDEO_ID = "abcdefghijk"
SOURCE_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"


def _words(pairs):
    return [
        {"text": text, "start": start, "end": end, "confidence": 0.99}
        for text, start, end in pairs
    ]


def _cache(path: Path, pairs):
    path.with_suffix(".transcript_cache.json").write_text(
        json.dumps({"words": [{"text": text, "start": int(start * 1000), "end": int(end * 1000)} for text, start, end in pairs]}),
        encoding="utf-8",
    )


def _even(texts, duration=2.0):
    step = duration / len(texts)
    return [
        {
            "id": f"w{index}",
            "start": index * step,
            "end": (index + 1) * step,
            "text": text,
            "highlight": index == 0,
        }
        for index, text in enumerate(texts)
    ]


def _document(words, duration=2.0):
    return (
        EditDocument.model_validate(
            {
                "segments": [{"id": "keep", "start": 0.25, "end": min(duration, 1.8)}],
                "words": words,
                "hookTitle": "Keep this",
                "volume": 0.4,
                "captions": {"y": 0.3, "template": "hormozi"},
            }
        )
        .validate_duration(duration)
        .model_dump()
    )


def _config(tmp_path):
    config = Config()
    config.temp_dir = str(tmp_path)
    return config


def _patch_temp(monkeypatch, tmp_path):
    config = _config(tmp_path)
    # These tests must not read or write the shared media bucket. The editor
    # folder hash is only the task, clip, and filename, so a real object for
    # the same ids would be restored over the fixture.
    config.media_storage = "local"
    monkeypatch.setattr("src.config.get_config", lambda: config)
    monkeypatch.setattr("src.media_storage.get_config", lambda: config)
    monkeypatch.setattr("src.youtube_source.get_config", lambda: config)
    monkeypatch.setattr("src.services.video_service.get_config", lambda: config)
    return config


def test_even_spread_is_flagged_and_speech_timings_are_not(caplog):
    with caplog.at_level("WARNING"):
        estimated = _caption_words_with_timings(["alpha", "beta"], [], 2)
    assert estimated.estimated is True
    assert estimated.words[0]["end"] - estimated.words[0]["start"] == pytest.approx(
        estimated.words[1]["end"] - estimated.words[1]["start"]
    )
    assert "evenly" in caplog.text

    spoken = _caption_words_with_timings(["alpha", "beta"], _words([("a", 0.2, 0.5), ("b", 1.1, 1.6)]), 2)
    assert spoken.estimated is False
    assert [word["start"] for word in spoken.words] == pytest.approx([0.2, 1.1])

    proportional = _caption_words_with_timings(
        ["one", "two", "three"],
        _words([("a", 0.4, 0.6), ("b", 1.0, 1.5)]),
        3,
    )
    assert proportional.estimated is False
    assert proportional.words[0]["start"] == pytest.approx(0.4)
    assert proportional.words[-1]["end"] == pytest.approx(1.5)


def test_section_file_uses_the_original_transcript_and_offsets_to_the_clip(tmp_path, monkeypatch):
    _patch_temp(monkeypatch, tmp_path)
    section = tmp_path / "render.mp4"
    section.write_bytes(b"section")
    save_time_map(section, build_time_map([(8.0, 20.0)]))
    _cache(section, [("hello", 10.0, 10.4), ("there", 11.0, 11.6)])

    words, missing = resolve_speech_words(
        source=section, original_ranges=[(10.0, 12.0)], duration=2
    )

    assert missing is False
    assert [word["start"] for word in words] == pytest.approx([0.0, 1.0])
    assert [word["end"] for word in words] == pytest.approx([0.4, 1.6])


def test_shifted_section_transcript_stays_on_the_local_timeline(tmp_path):
    section = tmp_path / "render.mp4"
    section.write_bytes(b"section")
    save_time_map(section, build_time_map([(8.0, 20.0)]))
    # Original 10.0s is local 2.0s on this section file.
    _cache(section, [("hello", 2.0, 2.4), ("there", 3.0, 3.6)])

    words, missing = resolve_speech_words(
        source=section, original_ranges=[(10.0, 12.0)], duration=2
    )

    assert missing is False
    assert [word["start"] for word in words] == pytest.approx([0.0, 1.0])
    assert [word["end"] for word in words] == pytest.approx([0.4, 1.6])


def test_youtube_cache_supplies_words_when_the_section_has_no_sidecar(tmp_path, monkeypatch):
    _patch_temp(monkeypatch, tmp_path)
    cache = tmp_path / "youtube-cache" / VIDEO_ID
    cache.mkdir(parents=True)
    (cache / "manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "video_id": VIDEO_ID,
                "audio_policy": "original-v1",
                "audio": None,
                "full": "full.mp4",
                "ranges": [],
            }
        ),
        encoding="utf-8",
    )
    _cache(cache / "full.mp4", [("hello", 10.0, 10.4), ("there", 11.0, 11.6)])
    section = tmp_path / "render.mp4"
    section.write_bytes(b"section")
    save_time_map(section, build_time_map([(8.0, 20.0)]))

    words, missing = resolve_speech_words(
        source=section,
        original_ranges=[(10.0, 12.0)],
        source_url=SOURCE_URL,
        source_type="youtube",
        duration=2,
    )

    assert missing is False
    assert not (cache / "full.mp4").exists()
    assert [word["start"] for word in words] == pytest.approx([0.0, 1.0])


def test_upload_sidecar_is_offset_to_the_clip(tmp_path, monkeypatch):
    _patch_temp(monkeypatch, tmp_path)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    _cache(uploads / "clip.mp4", [("hello", 5.0, 5.4), ("there", 6.0, 6.6)])

    words, missing = resolve_speech_words(
        source=None,
        original_ranges=[(5.0, 8.0)],
        source_url="upload://clip.mp4",
        source_type="video_url",
        duration=3,
    )

    assert missing is False
    assert [word["start"] for word in words] == pytest.approx([0.0, 1.0])
    assert [word["end"] for word in words] == pytest.approx([0.4, 1.6])


def test_stream_segment_is_transcribed_instead_of_guessing(tmp_path, monkeypatch):
    segment = tmp_path / "clean.mp4"
    segment.write_bytes(b"clip")
    seen = []

    def transcribe(path, duration):
        seen.append((path, duration))
        return _words([("hi", 0.2, 0.6)])

    monkeypatch.setattr("src.clip_editor.transcribe_clip_segment", transcribe)
    words, missing = resolve_speech_words(
        source=tmp_path / "gone.mp4",
        original_ranges=[(60.0, 70.0)],
        source_url="https://www.twitch.tv/example",
        source_type="twitch",
        segment_path=segment,
        allow_transcribe=True,
        duration=4,
    )

    assert seen == [(segment, 4)]
    assert missing is False
    assert words[0]["start"] == pytest.approx(0.2)

    monkeypatch.setattr("src.clip_editor.transcribe_clip_segment", lambda *_args, **_kwargs: [])
    empty, still_missing = resolve_speech_words(
        source=tmp_path / "gone.mp4",
        original_ranges=[(60.0, 70.0)],
        source_type="kick",
        source_url="https://kick.com/example",
        segment_path=segment,
        allow_transcribe=True,
        duration=4,
    )
    assert empty == [] and still_missing is True
    fallback = _caption_words_with_timings(["a", "b"], empty, 4)
    assert fallback.estimated is True


def test_repair_keeps_trims_style_and_edited_text():
    duration = 2.0
    original = _document(_even(["Hello", "friend"], duration), duration)
    repaired = apply_repaired_words(original, _words([("a", 0.15, 0.42), ("b", 0.9, 1.3)]), duration)
    assert repaired is not None
    assert repaired["segments"] == original["segments"]
    assert repaired["captions"] == original["captions"]
    assert repaired["hookTitle"] == "Keep this"
    assert repaired["volume"] == 0.4
    assert [word["text"] for word in repaired["words"]] == ["Hello", "friend"]
    assert [word["id"] for word in repaired["words"]] == ["w0", "w1"]
    assert [word["highlight"] for word in repaired["words"]] == [True, False]
    assert [word["start"] for word in repaired["words"]] == pytest.approx([0.15, 0.9])

    varied = _document(
        [
            {"id": "w0", "start": 0.2, "end": 0.5, "text": "Hello", "highlight": False},
            {"id": "w1", "start": 1.1, "end": 1.4, "text": "friend", "highlight": True},
        ],
        duration,
    )
    assert apply_repaired_words(varied, _words([("a", 0.15, 0.42), ("b", 0.9, 1.3)]), duration) is None
    drifted = _even(["Hello", "friend"], 2.0)
    assert is_synthetic_caption_timing(drifted, 2.3) is True


def _storage(tmp_path):
    config = _config(tmp_path)
    config.media_storage = "s3"
    config.media_s3_bucket = "private-media"
    return MediaStorage(config, MemoryS3())


def test_repaired_draft_is_what_s3_restores_and_what_a_stale_machine_adopts(tmp_path):
    storage = _storage(tmp_path)
    directory = editor_dir(str(tmp_path), "task", "clip", "clip.mp4")
    duration = 2.0
    broken = _document(_even(["Hello", "friend"], duration), duration)
    atomic_json(directory / "asset.json", {"status": "ready", "duration": duration})
    atomic_json(directory / "draft.json", {"revision": 0, "document": broken})
    atomic_json(directory / "original.json", broken)
    storage.save_editor(directory)

    timing = apply_saved_caption_repair(
        storage, directory, _words([("a", 0.15, 0.42), ("b", 0.9, 1.3)]), duration
    )
    assert timing == "speech"
    for name in ("draft.json", "original.json", "asset.json"):
        (directory / name).unlink()
    storage.restore_editor(directory)
    restored = json.loads((directory / "draft.json").read_text())
    assert restored["revision"] == 1
    assert restored["document"]["words"][0]["text"] == "Hello"
    assert restored["document"]["words"][0]["start"] == pytest.approx(0.15)
    assert restored["document"]["segments"][0]["id"] == "keep"
    assert json.loads((directory / "asset.json").read_text())["captionTiming"] == "speech"

    atomic_json(directory / "draft.json", {"revision": 0, "document": broken})
    atomic_json(directory / "original.json", broken)
    assert storage.adopt_remote_caption_repair(directory) is True
    adopted = json.loads((directory / "draft.json").read_text())
    assert adopted["revision"] == 1
    assert adopted["document"]["words"][1]["start"] == pytest.approx(0.9)
    assert json.loads((directory / "original.json").read_text())["words"][0]["start"] == pytest.approx(0.15)

    varied = _document(
        [
            {"id": "w0", "start": 0.2, "end": 0.5, "text": "Mine", "highlight": False},
            {"id": "w1", "start": 1.1, "end": 1.4, "text": "edit", "highlight": True},
        ],
        duration,
    )
    atomic_json(directory / "draft.json", {"revision": 8, "document": varied})
    assert storage.adopt_remote_caption_repair(directory) is False
    assert json.loads((directory / "draft.json").read_text())["revision"] == 8

    atomic_json(directory / "draft.json", {"revision": 0, "document": broken})
    storage.save_editor(directory)
    assert storage.adopt_remote_caption_repair(directory) is False
    assert json.loads((directory / "draft.json").read_text())["revision"] == 0


def _editor_service(tmp_path, clip, task, ranges):
    download = AsyncMock(side_effect=AssertionError("source download"))
    service = SimpleNamespace(
        clip_repo=SimpleNamespace(get_clip_by_id=AsyncMock(return_value=clip)),
        task_repo=SimpleNamespace(get_task_by_id=AsyncMock(return_value=task)),
        cache_repo=SimpleNamespace(get_cache=AsyncMock(return_value=None)),
        video_service=SimpleNamespace(download_video=download, resolve_local_video_path=lambda _url: Path("missing")),
        config=SimpleNamespace(default_processing_mode="fast"),
        _build_cache_key=lambda *_args: "cache-key",
        _media_limits=AsyncMock(return_value=(None, None)),
        _get_clip_source_ranges=lambda _clip: ranges,
        _load_task_source_settings=AsyncMock(return_value={}),
        _cleanup_source_video=lambda *_args, **_kwargs: None,
    )
    return service, download


@pytest.mark.parametrize("synthetic", [True, False])
async def test_prepare_repairs_a_saved_draft_without_downloading_the_source(tmp_path, monkeypatch, synthetic):
    _patch_temp(monkeypatch, tmp_path)
    cache = tmp_path / "youtube-cache" / VIDEO_ID
    cache.mkdir(parents=True)
    (cache / "manifest.json").write_text(
        json.dumps({"version": 1, "video_id": VIDEO_ID, "audio_policy": "original-v1", "full": "full.mp4", "audio": None, "ranges": []}),
        encoding="utf-8",
    )
    _cache(cache / "full.mp4", [("hello", 10.0, 10.4), ("there", 11.0, 11.6)])
    directory = editor_dir(str(tmp_path), "task", "clip", "clip.mp4")
    duration = 2.0
    words = (
        _even(["Hello", "friend"], duration)
        if synthetic
        else [
            {"id": "w0", "start": 0.2, "end": 0.5, "text": "Hello", "highlight": True},
            {"id": "w1", "start": 1.1, "end": 1.4, "text": "friend", "highlight": False},
        ]
    )
    document = _document(words, duration)
    atomic_json(directory / "asset.json", {"status": "ready", "duration": duration, "waveform": [0.1]})
    atomic_json(directory / "draft.json", {"revision": 4, "document": document})
    atomic_json(directory / "original.json", document)
    clip = {"id": "clip", "task_id": "task", "filename": "clip.mp4", "file_path": str(tmp_path / "clip.mp4"), "text": "Hello friend"}
    task = {"source_url": SOURCE_URL, "source_type": "youtube", "processing_mode": "fast", "user_id": "user"}
    service, download = _editor_service(tmp_path, clip, task, [(10.0, 12.0)])

    @asynccontextmanager
    async def sessions():
        yield SimpleNamespace(close=AsyncMock())

    monkeypatch.setattr("src.database.AsyncSessionLocal", sessions)
    monkeypatch.setattr("src.services.task_service.TaskService", lambda _db: service)
    monkeypatch.setattr("src.video_utils.create_optimized_clip", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("render")))
    monkeypatch.setattr("src.clip_editor.transcribe_clip_segment", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("transcribe")))

    await prepare_editor({}, "task", "clip", "clip.mp4")

    download.assert_not_awaited()
    state = read_state(directory)
    assert state["status"] == "ready"
    assert state["waveform"] == [0.1]
    draft = state["draft"]
    assert draft["document"]["segments"][0]["id"] == "keep"
    assert draft["document"]["hookTitle"] == "Keep this"
    if synthetic:
        assert draft["revision"] == 5
        assert [word["start"] for word in draft["document"]["words"]] == pytest.approx([0.0, 1.0])
        assert [word["text"] for word in draft["document"]["words"]] == ["Hello", "friend"]
        assert state["captionTiming"] == "speech"
    else:
        assert draft["revision"] == 4
        assert draft["document"]["words"][0]["start"] == 0.2
        assert state.get("captionTiming") in {None, "speech"}
