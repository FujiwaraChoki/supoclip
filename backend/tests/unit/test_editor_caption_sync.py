"""Caption timings follow speech when the section file has no sidecar."""

import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.clip_editor import _caption_words_with_timings, resolve_speech_words
from src.config import Config
from src.editor_document import atomic_json, editor_dir
from src.services.editor_service import prepare_editor, read_state
from src.youtube_source import build_time_map, save_time_map


VIDEO_ID = "abcdefghijk"
SOURCE_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"


def _words(pairs):
    return [
        {"text": text, "start": start, "end": end, "confidence": 0.99}
        for text, start, end in pairs
    ]


def _cache(path: Path, pairs):
    path.with_suffix(".transcript_cache.json").write_text(
        json.dumps(
            {
                "words": [
                    {"text": text, "start": int(start * 1000), "end": int(end * 1000)}
                    for text, start, end in pairs
                ]
            }
        ),
        encoding="utf-8",
    )


def _config(tmp_path):
    config = Config()
    config.temp_dir = str(tmp_path)
    config.media_storage = "local"
    config.assembly_ai_api_key = ""
    return config


def _patch_temp(monkeypatch, tmp_path):
    config = _config(tmp_path)
    monkeypatch.setattr("src.config.get_config", lambda: config)
    monkeypatch.setattr("src.media_storage.get_config", lambda: config)
    monkeypatch.setattr("src.youtube_source.get_config", lambda: config)
    monkeypatch.setattr("src.services.video_service.get_config", lambda: config)
    return config


def test_even_spread_is_flagged_and_speech_timings_are_not(caplog):
    with caplog.at_level("WARNING"):
        estimated = _caption_words_with_timings(["alpha", "beta"], [], 4)
    assert estimated.estimated is True
    assert [word["start"] for word in estimated.words] == pytest.approx([0.0, 2.0])
    assert "evenly" in caplog.text

    spoken = _caption_words_with_timings(
        ["alpha", "beta"], _words([("a", 0.2, 0.5), ("b", 1.1, 1.6)]), 2
    )
    assert spoken.estimated is False
    assert [word["start"] for word in spoken.words] == pytest.approx([0.2, 1.1])


def test_section_sidecar_is_used_when_present(tmp_path):
    section = tmp_path / "render.mp4"
    section.write_bytes(b"section")
    _cache(section, [("hello", 0.4, 0.9), ("there", 1.2, 1.8)])

    words, missing = resolve_speech_words(
        source=section, original_ranges=[(0.0, 4.0)], duration=4
    )

    assert missing is False
    assert [word["start"] for word in words] == pytest.approx([0.4, 1.2])
    assert [word["end"] for word in words] == pytest.approx([0.9, 1.8])


def test_section_file_offsets_an_original_transcript_to_the_clip(tmp_path):
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


def test_full_transcript_is_shifted_and_clamped_to_the_clip(tmp_path, monkeypatch):
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
    _cache(
        cache / "full.mp4",
        [("hello", 10.0, 10.4), ("there", 11.0, 11.6), ("late", 11.5, 13.0)],
    )
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
    assert [word["text"] for word in words] == ["hello", "there", "late"]
    assert [word["start"] for word in words] == pytest.approx([0.0, 1.0, 1.5])
    assert words[-1]["end"] == pytest.approx(2.0)


def test_canonical_transcript_object_is_shifted_to_the_clip(tmp_path, monkeypatch):
    _patch_temp(monkeypatch, tmp_path)
    cache = tmp_path / "youtube-cache" / VIDEO_ID
    cache.mkdir(parents=True)
    _cache(cache / f"{VIDEO_ID}.mp4", [("hello", 10.0, 10.4), ("there", 11.0, 11.6)])
    section = tmp_path / "render.mp4"
    section.write_bytes(b"section")

    words, missing = resolve_speech_words(
        source=section,
        original_ranges=[(10.0, 12.0)],
        source_url=SOURCE_URL,
        source_type="youtube",
        duration=2,
    )

    assert missing is False
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


@pytest.mark.parametrize(
    ("source_type", "source_url"),
    [
        ("twitch", "https://www.twitch.tv/example"),
        ("kick", "https://kick.com/example"),
    ],
)
def test_stream_segment_is_transcribed_once(tmp_path, monkeypatch, source_type, source_url):
    config = _patch_temp(monkeypatch, tmp_path)
    config.assembly_ai_api_key = "test-key"
    config.transcription_provider = "assemblyai"
    segment = tmp_path / "clean.mp4"
    segment.write_bytes(b"clip")
    calls = []

    def transcribe(path, *_args, **_kwargs):
        calls.append(path)
        _cache(path, [("hi", 0.2, 0.6)])
        return "ok"

    monkeypatch.setattr("src.media.transcription.get_video_transcript", transcribe)

    kwargs = dict(
        source=tmp_path / "gone.mp4",
        original_ranges=[(60.0, 70.0)],
        source_url=source_url,
        source_type=source_type,
        segment_path=segment,
        allow_transcribe=True,
        duration=4,
    )
    words, missing = resolve_speech_words(**kwargs)
    again, still_missing = resolve_speech_words(**kwargs)

    assert calls == [segment]
    assert missing is False and still_missing is False
    assert words[0]["start"] == pytest.approx(0.2)
    assert again[0]["start"] == pytest.approx(0.2)
    assert segment.with_suffix(".transcript_cache.json").exists()


def test_stream_without_speech_stays_estimated(tmp_path, monkeypatch):
    segment = tmp_path / "clean.mp4"
    segment.write_bytes(b"clip")

    def transcribe(_path, _duration):
        return []

    monkeypatch.setattr("src.clip_editor.transcribe_clip_segment", transcribe)
    words, missing = resolve_speech_words(
        source=tmp_path / "gone.mp4",
        original_ranges=[(60.0, 70.0)],
        source_url="https://kick.com/example",
        source_type="kick",
        segment_path=segment,
        allow_transcribe=True,
        duration=4,
    )

    assert words == [] and missing is True
    fallback = _caption_words_with_timings(["alpha", "beta"], words, 4)
    assert fallback.estimated is True
    assert [word["start"] for word in fallback.words] == pytest.approx([0.0, 2.0])


def _editor_service(clip, task, ranges, source):
    return SimpleNamespace(
        clip_repo=SimpleNamespace(get_clip_by_id=AsyncMock(return_value=clip)),
        task_repo=SimpleNamespace(get_task_by_id=AsyncMock(return_value=task)),
        cache_repo=SimpleNamespace(get_cache=AsyncMock(return_value=None)),
        video_service=SimpleNamespace(
            download_video=AsyncMock(return_value=source),
            prepare_source=AsyncMock(side_effect=lambda path: path),
            resolve_local_video_path=lambda _url: source,
        ),
        config=SimpleNamespace(default_processing_mode="fast"),
        _build_cache_key=lambda *_args: "cache-key",
        _media_limits=AsyncMock(return_value=(None, None)),
        _get_clip_source_ranges=lambda _clip: ranges,
        _load_task_source_settings=AsyncMock(
            return_value={"output_format": "original", "add_subtitles": True}
        ),
        _cleanup_source_video=lambda *_args, **_kwargs: None,
    )


def _install_prepare(monkeypatch, tmp_path, service, *, duration=2.0):
    @asynccontextmanager
    async def sessions():
        yield SimpleNamespace(close=AsyncMock())

    def render(*args, **_kwargs):
        args[3].write_bytes(b"clean clip")
        return True

    monkeypatch.setattr("src.database.AsyncSessionLocal", sessions)
    monkeypatch.setattr("src.services.task_service.TaskService", lambda _db: service)
    monkeypatch.setattr("src.video_utils.create_optimized_clip", render)
    monkeypatch.setattr(
        "src.services.editor_service.make_assets",
        lambda _directory: {
            "status": "ready",
            "width": 320,
            "height": 180,
            "fps": 30,
            "duration": duration,
            "hasAudio": True,
            "waveform": [],
        },
    )


async def test_prepare_shifts_a_full_transcript_without_transcribing(tmp_path, monkeypatch):
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
    source = tmp_path / "section.mp4"
    source.write_bytes(b"section")
    clip = {
        "id": "clip",
        "task_id": "task",
        "filename": "clip.mp4",
        "file_path": str(tmp_path / "clip.mp4"),
        "text": "hello there",
        "hook_title": "",
    }
    task = {
        "source_url": SOURCE_URL,
        "source_type": "youtube",
        "processing_mode": "fast",
        "user_id": "user",
    }
    service = _editor_service(clip, task, [(10.0, 12.0)], source)
    _install_prepare(monkeypatch, tmp_path, service)
    monkeypatch.setattr(
        "src.clip_editor.transcribe_clip_segment",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("transcribe")),
    )

    await prepare_editor({}, "task", "clip", "clip.mp4")

    state = read_state(editor_dir(str(tmp_path), "task", "clip", "clip.mp4"))
    assert state["status"] == "ready"
    assert state["captionTiming"] == "speech"
    words = state["draft"]["document"]["words"]
    assert [word["text"] for word in words] == ["hello", "there"]
    assert [word["start"] for word in words] == pytest.approx([0.0, 1.0])
    service.video_service.download_video.assert_awaited()


async def test_prepare_transcribes_a_stream_once_and_keeps_the_draft(tmp_path, monkeypatch):
    _patch_temp(monkeypatch, tmp_path)
    source = tmp_path / "section.mp4"
    source.write_bytes(b"section")
    calls = []

    def transcribe(path, duration):
        calls.append((path.name, duration))
        return _words([("hi", 0.2, 0.6)])

    monkeypatch.setattr("src.clip_editor.transcribe_clip_segment", transcribe)
    monkeypatch.setattr(
        "src.services.editor_service.download_stream_sections", lambda *_args, **_kwargs: source
    )
    clip = {
        "id": "clip",
        "task_id": "task",
        "filename": "clip.mp4",
        "file_path": str(tmp_path / "clip.mp4"),
        "text": "hi",
        "hook_title": "",
    }
    task = {
        "source_url": "https://www.twitch.tv/example",
        "source_type": "twitch",
        "processing_mode": "fast",
        "user_id": "user",
        "source_duration_seconds": 600,
    }
    service = _editor_service(clip, task, [(60.0, 70.0)], source)
    _install_prepare(monkeypatch, tmp_path, service, duration=4.0)

    await prepare_editor({}, "task", "clip", "clip.mp4")

    directory = editor_dir(str(tmp_path), "task", "clip", "clip.mp4")
    state = read_state(directory)
    assert state["captionTiming"] == "speech"
    assert state["draft"]["document"]["words"][0]["start"] == pytest.approx(0.2)
    assert calls == [("clean.mp4", 4.0)]

    draft_path = directory / "draft.json"
    saved = json.loads(draft_path.read_text())
    saved["revision"] = 4
    saved["document"]["words"][0]["text"] = "Kept"
    saved["document"]["words"][0]["start"] = 0.33
    atomic_json(draft_path, saved)

    await prepare_editor({}, "task", "clip", "clip.mp4")

    again = json.loads(draft_path.read_text())
    assert again["revision"] == 4
    assert again["document"]["words"][0]["text"] == "Kept"
    assert again["document"]["words"][0]["start"] == 0.33
    assert calls == [("clean.mp4", 4.0)]


async def test_prepare_marks_estimated_timings(tmp_path, monkeypatch):
    _patch_temp(monkeypatch, tmp_path)
    source = tmp_path / "section.mp4"
    source.write_bytes(b"section")
    monkeypatch.setattr("src.clip_editor.transcribe_clip_segment", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "src.services.editor_service.download_stream_sections", lambda *_args, **_kwargs: source
    )
    clip = {
        "id": "clip",
        "task_id": "task",
        "filename": "clip.mp4",
        "file_path": str(tmp_path / "clip.mp4"),
        "text": "alpha beta",
        "hook_title": "",
    }
    task = {
        "source_url": "https://kick.com/example",
        "source_type": "kick",
        "processing_mode": "fast",
        "user_id": "user",
        "source_duration_seconds": 600,
    }
    service = _editor_service(clip, task, [(60.0, 70.0)], source)
    _install_prepare(monkeypatch, tmp_path, service, duration=4.0)

    await prepare_editor({}, "task", "clip", "clip.mp4")

    state = read_state(editor_dir(str(tmp_path), "task", "clip", "clip.mp4"))
    assert state["captionTiming"] == "estimated"
    assert [word["start"] for word in state["draft"]["document"]["words"]] == pytest.approx([0.0, 2.0])
