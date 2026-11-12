"""The subtitle translation: the model, the draft and the rendered captions."""

import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from src.api.routes import editor
from src.database import get_db
from src.editor_document import (
    EditDocument,
    caption_chunk_starts,
    mapped_words,
    write_ass,
)

SOURCE_WORDS = [
    {"id": "w1", "start": 0.1, "end": 0.188, "text": "I"},
    {"id": "w2", "start": 2.993, "end": 3.138, "text": "got"},
    {"id": "w3", "start": 3.138, "end": 3.394, "text": "in"},
    {"id": "w4", "start": 3.394, "end": 3.715, "text": "some"},
]

TRANSLATED_WORDS = [
    {"id": "t1", "start": 0.1, "end": 0.188, "text": "Я"},
    {"id": "t2", "start": 2.993, "end": 3.2, "text": "Попал"},
    {"id": "t3", "start": 3.2, "end": 3.4, "text": "в"},
    {"id": "t4", "start": 3.4, "end": 3.715, "text": "беду"},
]

TRANSLATION = {
    "sourceLanguage": "en",
    "targetLanguage": "ru",
    "mode": "auto",
    "sourceWords": SOURCE_WORDS,
    "phrases": [
        {
            "id": "p1",
            "start": 0.1,
            "end": 0.188,
            "sourceText": "I",
            "targetText": "Я",
            "wordIds": ["t1"],
        },
        {
            "id": "p2",
            "start": 2.993,
            "end": 3.715,
            "sourceText": "got in some",
            "targetText": "Попал в беду",
            "wordIds": ["t2", "t3", "t4"],
        },
    ],
    "updatedAt": "2026-11-12T10:00:00.000Z",
}


def document(**changes):
    return EditDocument.model_validate(
        {
            "segments": [{"id": "s", "start": 0, "end": 4}],
            "words": SOURCE_WORDS,
            **changes,
        }
    )


def ass_seconds(timestamp: str) -> float:
    hours, minutes, rest = timestamp.split(":")
    seconds, centiseconds = rest.split(".")
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(centiseconds) / 100
    )


def caption_events(path):
    events = []
    for line in path.read_text().splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",")
        events.append((ass_seconds(parts[1]), ass_seconds(parts[2])))
    return events


def test_translation_keeps_the_english_transcription_as_the_basis():
    doc = document(words=TRANSLATED_WORDS, translation=TRANSLATION)
    assert doc.translation is not None
    assert [word.text for word in doc.translation.sourceWords] == ["I", "got", "in", "some"]
    assert [word.text for word in doc.words] == ["Я", "Попал", "в", "беду"]
    # The draft keeps the translation when it is saved and loaded again.
    assert EditDocument.model_validate(doc.model_dump()).translation is not None


def test_translation_bounds_are_validated():
    doc = document(words=TRANSLATED_WORDS, translation=TRANSLATION)
    with pytest.raises(ValueError):
        doc.validate_duration(1)
    late = EditDocument.model_validate(
        {
            "segments": [{"id": "s", "start": 0, "end": 4}],
            "words": TRANSLATED_WORDS,
            "translation": {
                **TRANSLATION,
                "phrases": [{**TRANSLATION["phrases"][0], "end": 40}],
            },
        }
    )
    with pytest.raises(ValueError):
        late.validate_duration(4)


def test_translated_draft_breaks_its_caption_lines_on_the_pauses():
    doc = document(words=TRANSLATED_WORDS, translation=TRANSLATION)
    starts = caption_chunk_starts(mapped_words(doc), 4, doc.translation)
    assert starts == [0, 1]
    # Without a translation the same words share one line.
    plain = document()
    assert caption_chunk_starts(mapped_words(plain), 4, plain.translation) == [0]


def test_rendered_captions_leave_the_pause_empty(tmp_path):
    doc = document(words=TRANSLATED_WORDS, translation=TRANSLATION)
    path = tmp_path / "captions.ass"
    write_ass(path, doc, 1080, 1920)
    events = caption_events(path)
    assert len(events) == 2
    assert events[0][1] <= 0.19
    # The captions are off the screen while the speaker pauses.
    assert events[1][0] >= 2.98
    assert events[1][0] - events[0][1] > 2.5


def test_captions_of_an_english_draft_are_unchanged(tmp_path):
    doc = document()
    path = tmp_path / "captions.ass"
    write_ass(path, doc, 1080, 1920)
    events = caption_events(path)
    assert len(events) == 1
    assert events[0][0] == 0.1
    assert events[0][1] == 3.71


@pytest.fixture
async def client(tmp_path, monkeypatch, editor_store):
    task = {"id": "task", "user_id": "owner", "status": "completed"}
    clip = {
        "id": "clip",
        "task_id": "task",
        "filename": "clip.mp4",
        "file_path": str(tmp_path / "clip.mp4"),
    }
    service = SimpleNamespace(
        task_repo=SimpleNamespace(get_task_by_id=AsyncMock(return_value=task)),
        clip_repo=SimpleNamespace(get_clip_by_id=AsyncMock(return_value=clip)),
        config=SimpleNamespace(temp_dir=str(tmp_path)),
    )
    monkeypatch.setattr(editor, "TaskService", lambda db: service)
    monkeypatch.setattr(
        "src.api.routes.tasks._get_user_id_from_headers",
        AsyncMock(return_value="owner"),
    )
    db = SimpleNamespace(close=AsyncMock())
    app = FastAPI()
    app.include_router(editor.router)
    app.dependency_overrides[get_db] = lambda: db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


async def test_translate_endpoint_returns_one_translation_per_phrase(client, monkeypatch):
    captured = {}

    async def fake_translate(phrases, target_language="ru"):
        captured["phrases"] = phrases
        captured["target_language"] = target_language
        return SimpleNamespace(
            model_dump=lambda: {
                "translations": ["Я", "Попал в беду"],
                "needsReview": False,
                "model": "google-gla:gemini-3-flash-preview",
            }
        )

    monkeypatch.setattr(
        "src.services.translation_service.translate_phrases", fake_translate
    )
    response = await client.post(
        "/tasks/task/clips/clip/editor/translate",
        json={
            "phrases": [{"text": "I"}, {"text": "got in some"}],
            "targetLanguage": "ru",
        },
    )
    assert response.status_code == 200
    assert response.json()["translations"] == ["Я", "Попал в беду"]
    assert captured["phrases"] == ["I", "got in some"]
    assert captured["target_language"] == "ru"


async def test_translate_endpoint_rejects_an_empty_transcript(client):
    response = await client.post(
        "/tasks/task/clips/clip/editor/translate", json={"phrases": []}
    )
    assert response.status_code == 400


async def test_translate_endpoint_requires_task_ownership(client, monkeypatch):
    monkeypatch.setattr(
        "src.api.routes.tasks._get_user_id_from_headers",
        AsyncMock(return_value="outsider"),
    )
    response = await client.post(
        "/tasks/task/clips/clip/editor/translate",
        json={"phrases": [{"text": "I"}]},
    )
    assert response.status_code == 403


async def test_translation_service_keeps_the_model_order(monkeypatch):
    from src.services import translation_service

    class FakeAgent:
        def __init__(self, *args, **kwargs):
            pass

        def __class_getitem__(cls, item):
            return cls

        async def run(self, prompt):
            captured["prompt"] = prompt
            return SimpleNamespace(
                output=SimpleNamespace(translations=["Я", "Попал в беду"])
            )

    captured = {}
    monkeypatch.setattr(translation_service, "Agent", FakeAgent)
    monkeypatch.setattr(translation_service, "_get_missing_llm_key_error", lambda *a: None)
    result = await translation_service.translate_phrases(["I", "got in some"], "ru")
    assert result.translations == ["Я", "Попал в беду"]
    assert result.needsReview is False
    assert "got in some" in captured["prompt"]


async def test_translation_service_falls_back_to_sentences(monkeypatch):
    from src.services import translation_service

    class FakeAgent:
        def __init__(self, *args, **kwargs):
            pass

        def __class_getitem__(cls, item):
            return cls

        async def run(self, prompt):
            return SimpleNamespace(
                output=SimpleNamespace(
                    translations=["Я. Попал в беду в армии."]
                )
            )

    monkeypatch.setattr(translation_service, "Agent", FakeAgent)
    monkeypatch.setattr(translation_service, "_get_missing_llm_key_error", lambda *a: None)
    result = await translation_service.translate_phrases(["I", "got in some"], "ru")
    assert result.translations == ["Я.", "Попал в беду в армии."]
    assert result.needsReview is True


def test_translation_service_splits_sentences():
    from src.services.translation_service import split_sentences

    assert split_sentences("Я попал в беду. Не ушёл! Хорошо?") == [
        "Я попал в беду.",
        "Не ушёл!",
        "Хорошо?",
    ]


def test_the_translation_prompt_asks_for_one_translation_per_phrase():
    from src.services.translation_service import build_prompt

    prompt = build_prompt(["I", "got in some"], "ru")
    assert "1. I" in prompt
    assert "2. got in some" in prompt


def test_the_translation_budget_grows_with_the_transcript():
    from src.services.translation_service import (
        MAX_TIMEOUT_SECONDS,
        request_timeout,
    )

    assert request_timeout(1) < request_timeout(50)
    assert request_timeout(200) == MAX_TIMEOUT_SECONDS


async def test_translation_service_gives_up_when_the_model_is_slow(monkeypatch):
    """A slow model must not hold the request open for minutes."""
    from src.services import translation_service

    class SlowAgent:
        def __init__(self, *args, **kwargs):
            pass

        def __class_getitem__(cls, item):
            return cls

        async def run(self, prompt):
            await asyncio.sleep(5)
            return SimpleNamespace(output=SimpleNamespace(translations=[]))

    monkeypatch.setattr(translation_service, "Agent", SlowAgent)
    monkeypatch.setattr(translation_service, "_get_missing_llm_key_error", lambda *a: None)
    monkeypatch.setattr(translation_service, "request_timeout", lambda count: 0.05)
    with pytest.raises(translation_service.TranslationError) as error:
        await translation_service.translate_phrases(["I"], "ru")
    assert error.value.code == "translation_timeout"


async def test_translate_endpoint_reports_the_reason_of_a_refusal(client, monkeypatch):
    from src.services.translation_service import TranslationError

    async def fake_translate(phrases, target_language="ru"):
        raise TranslationError(
            "The translation took too long. Try again.", code="translation_timeout"
        )

    monkeypatch.setattr(
        "src.services.translation_service.translate_phrases", fake_translate
    )
    response = await client.post(
        "/tasks/task/clips/clip/editor/translate",
        json={"phrases": [{"text": "I"}]},
    )
    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "translation_timeout"
    # The detail stays a string, so every client can read it the same way.
    assert isinstance(body["detail"], str)
    assert "too long" in body["detail"]
