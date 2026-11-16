"""Source keys and the localized duplicate-submission copy."""

import json
from pathlib import Path

import pytest
from sqlalchemy.exc import ProgrammingError

from src.source_identity import (
    DUPLICATE_SOURCE_MESSAGE,
    source_key_for_url,
    source_key_from_url,
    upload_source_key,
)

ROOT = Path(__file__).resolve().parents[3]
LOCALES = {
    "en": "This video is already being processed.",
    "es": "Este video ya se está procesando.",
    "pt": "Este vídeo já está sendo processado.",
    "id": "Video ini sedang diproses.",
    "de": "Dieses Video wird bereits verarbeitet.",
    "zh": "这个视频正在处理。",
}


def test_link_keys_ignore_spelling():
    video = "abcdefghijk"
    assert source_key_from_url(f"https://www.youtube.com/watch?v={video}") == f"youtube:{video}"
    assert source_key_from_url(f"https://youtu.be/{video}") == f"youtube:{video}"
    assert source_key_from_url(f"https://www.youtube.com/shorts/{video}") == f"youtube:{video}"
    assert source_key_from_url("https://www.twitch.tv/videos/4242?t=12s") == "twitch:vod:4242"
    assert source_key_from_url("https://twitch.tv/videos/4242") == "twitch:vod:4242"
    kick = "12345678-1234-1234-1234-123456789abc"
    assert source_key_from_url(f"https://kick.com/some-channel/videos/{kick}") == f"kick:vod:{kick}"
    assert source_key_from_url("https://kick.com/some-channel?clip=clip_abc") == "kick:clip:clip_abc"
    assert source_key_from_url("not a url") is None
    assert source_key_from_url("upload://videos/a.mp4") is None


class _Row:
    def __init__(self, session_id, digest):
        self.id = session_id
        self.content_sha256 = digest


class _Db:
    def __init__(self, row=None, error=None):
        self.row = row
        self.error = error
        self.rolled_back = False

    async def execute(self, *_args, **_kwargs):
        if self.error:
            raise self.error
        row = self.row

        class _Result:
            def first(_self):
                return row

        return _Result()

    async def rollback(self):
        self.rolled_back = True


@pytest.mark.asyncio
async def test_upload_key_prefers_the_content_hash():
    db = _Db(_Row("session-1", "abc123"))
    assert await upload_source_key(db, "account-1", "upload://videos/a.mp4") == "upload:sha256:abc123"
    assert await source_key_for_url(db, "account-1", "upload://videos/a.mp4") == "upload:sha256:abc123"


@pytest.mark.asyncio
async def test_upload_key_falls_back_to_the_session_then_the_path():
    assert await upload_source_key(_Db(_Row("session-1", None)), "account-1", "upload://videos/a.mp4") == "upload:id:session-1"
    assert await upload_source_key(_Db(None), "account-1", "upload://videos/a.mp4") == "upload:url:upload://videos/a.mp4"


@pytest.mark.asyncio
async def test_missing_hash_column_falls_back_to_the_path():
    db = _Db(error=ProgrammingError("stmt", {}, Exception("missing")))
    assert await upload_source_key(db, "account-1", "upload://videos/a.mp4") == "upload:url:upload://videos/a.mp4"
    assert db.rolled_back is True


def test_duplicate_copy_is_translated_in_every_app_language():
    assert DUPLICATE_SOURCE_MESSAGE == LOCALES["en"]
    for locale, message in LOCALES.items():
        payload = json.loads((ROOT / "frontend" / "messages" / locale / "task.json").read_text())
        assert payload["alreadyProcessing"] == message
