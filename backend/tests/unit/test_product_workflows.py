from pathlib import Path
from types import SimpleNamespace
import hashlib
import hmac
import json
import zipfile

import httpx
import pytest

from src.config import Config, set_config_override
from src.external_sources import is_supported_external_url
from src.services.export_service import render_export
from src.services import social_service
from src.api.routes import clip_workflows, social as social_routes, workflows
from src.content_ai import LocalizedTranscript
from src.services import brand_service, variant_service, webhook_service
from src.services.brand_service import apply_brand_kit_to_clip
from src.services.social_service import (
    fetch_tiktok_publish_status,
    publish_post,
    refresh_access_token,
    sign_media_token,
    sign_state,
    tiktok_chunk_plan,
    verify_media_token,
    verify_state,
)
from src.services import source_import_service
from src.services.source_import_service import fetch_subscription_items
from src.services.variant_service import render_localized_variant
from src.services.webhook_service import emit_webhook_event
from src.services.variant_service import _write_even_srt


class MappingResult:
    def __init__(self, rows: list[dict] | None = None):
        self.rows = rows or []

    def mappings(self):
        return self

    def __iter__(self):
        return iter(self.rows)

    def first(self):
        return self.rows[0] if self.rows else None


class ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeDatabase:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, statement, parameters=None):
        query = str(statement)
        values = parameters or {}
        self.calls.append((query, values))
        return self.handler(query, values)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def sample_clip(tmp_path: Path) -> dict:
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"video")
    return {
        "id": "clip-1", "filename": "clip.mp4", "file_path": str(media),
        "start_time": "00:10", "end_time": "00:40", "duration": 30.0,
        "text": "A useful clip transcript.", "virality_score": 88,
    }


@pytest.mark.parametrize(
    "url",
    [
        "https://vimeo.com/123", "https://drive.google.com/file/d/abc/view",
        "https://www.dropbox.com/s/example/video.mp4", "https://clips.twitch.tv/example",
        "https://www.loom.com/share/example", "https://example.zoom.us/rec/share/example",
    ],
)
def test_supported_external_sources(url: str):
    assert is_supported_external_url(url)


@pytest.mark.parametrize("url", ["http://vimeo.com/1", "https://localhost/video", "https://example.com/video.mp4"])
def test_external_sources_reject_untrusted_hosts(url: str):
    assert not is_supported_external_url(url)


@pytest.mark.parametrize("export_type,suffix", [("csv", ".csv"), ("srt", ".srt"), ("fcpxml", ".fcpxml"), ("edl", ".edl")])
def test_professional_exports(tmp_path: Path, export_type: str, suffix: str):
    result = render_export(export_type, [sample_clip(tmp_path)], tmp_path, "job")
    assert result.suffix == suffix
    assert result.stat().st_size > 0


def test_zip_export_contains_media_captions_and_manifest(tmp_path: Path):
    result = render_export("zip", [sample_clip(tmp_path)], tmp_path, "job")
    with zipfile.ZipFile(result) as archive:
        assert set(archive.namelist()) == {
            "clips/clip.mp4", "captions/clip.srt", "manifest.json"
        }


def test_batch_srt_uses_non_overlapping_timeline(tmp_path: Path):
    first = sample_clip(tmp_path)
    second_media = tmp_path / "clip-2.mp4"
    second_media.write_bytes(b"video")
    second = {**first, "id": "clip-2", "filename": "clip-2.mp4",
              "file_path": str(second_media), "duration": 5.0}
    result = render_export("srt", [first, second], tmp_path, "timeline")
    contents = result.read_text()
    assert "00:00:00,000 --> 00:00:30,000" in contents
    assert "00:00:30,000 --> 00:00:35,000" in contents


def test_even_srt_spans_full_duration(tmp_path: Path):
    output = tmp_path / "translated.srt"
    _write_even_srt("First sentence. Second sentence!", 10, output)
    text = output.read_text()
    assert "00:00:00,000" in text
    assert "00:00:10,000" in text
    assert "First sentence." in text


def test_signed_oauth_state_and_media_tokens(monkeypatch):
    monkeypatch.setenv("BETTER_AUTH_SECRET", "test-secret-long-enough")
    state = sign_state({"user_id": "user-1", "platform": "youtube"})
    assert verify_state(state)["user_id"] == "user-1"
    token = sign_media_token("post-1")
    assert verify_media_token("post-1", token)
    assert not verify_media_token("post-2", token)


@pytest.mark.parametrize(
    "size,expected",
    [
        (4 * 1024 * 1024, (4 * 1024 * 1024, 1)),
        (70 * 1024 * 1024, (32 * 1024 * 1024, 2)),
    ],
)
def test_tiktok_chunk_plan(size: int, expected: tuple[int, int]):
    assert tiktok_chunk_plan(size) == expected


@pytest.mark.asyncio
async def test_refreshes_google_access_token(monkeypatch):
    config = Config()
    config.google_oauth_client_id = "google-client"
    config.google_oauth_client_secret = "google-secret"
    set_config_override(config)
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"access_token": "fresh", "expires_in": 3600})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        social_service.httpx,
        "AsyncClient",
        lambda **_kwargs: real_client(transport=httpx.MockTransport(handler)),
    )
    token = await refresh_access_token("youtube", "refresh-me")
    assert token["access_token"] == "fresh"
    assert "grant_type=refresh_token" in captured["body"]
    assert "refresh_token=refresh-me" in captured["body"]


@pytest.mark.asyncio
async def test_youtube_uses_resumable_upload(monkeypatch, tmp_path: Path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"video-bytes")
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, headers={"Location": "https://upload.example/session"})
        return httpx.Response(200, json={"id": "youtube-video"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        social_service.httpx,
        "AsyncClient",
        lambda **_kwargs: real_client(transport=httpx.MockTransport(handler)),
    )
    result = await publish_post(
        platform="youtube",
        access_token="access",
        account_id="channel",
        clip_path=media,
        title="Title",
        description="Description",
        hashtags="#clip",
        post_id="post",
    )
    assert result["external_post_id"] == "youtube-video"
    assert [request.method for request in requests] == ["POST", "PUT"]
    assert requests[0].url.params["uploadType"] == "resumable"


@pytest.mark.asyncio
async def test_tiktok_upload_stays_pending_until_provider_completes(
    monkeypatch, tmp_path: Path
):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"video-bytes")
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/video/init/"):
            return httpx.Response(
                200,
                json={"data": {"publish_id": "publish-1", "upload_url": "https://upload.example/video"}},
            )
        if request.url.host == "upload.example":
            return httpx.Response(200)
        if request.url.path.endswith("/status/fetch/"):
            return httpx.Response(
                200,
                json={"data": {"status": "PUBLISH_COMPLETE"}, "error": {"code": "ok"}},
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        social_service.httpx,
        "AsyncClient",
        lambda **_kwargs: real_client(transport=httpx.MockTransport(handler)),
    )
    result = await publish_post(
        platform="tiktok",
        access_token="access",
        account_id="creator",
        clip_path=media,
        title="Title",
        description="Description",
        hashtags="#clip",
        post_id="post",
    )
    assert result["external_post_id"] == "publish-1"
    assert result["pending"] is True
    assert await fetch_tiktok_publish_status("access", "publish-1") == {
        "status": "PUBLISH_COMPLETE"
    }
    assert [request.method for request in requests] == ["POST", "PUT", "POST"]


@pytest.mark.asyncio
async def test_meta_publish_adapters(monkeypatch, tmp_path: Path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"video-bytes")
    config = Config()
    config.backend_auth_secret = "test-secret"
    config.backend_public_url = "https://api.example.com"
    set_config_override(config)
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/media"):
            return httpx.Response(200, json={"id": "container-1"})
        if request.url.path.endswith("/media_publish"):
            return httpx.Response(200, json={"id": "instagram-post"})
        if request.url.path.endswith("/videos"):
            return httpx.Response(200, json={"id": "facebook-post"})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        social_service.httpx,
        "AsyncClient",
        lambda **_kwargs: real_client(transport=httpx.MockTransport(handler)),
    )
    instagram = await publish_post(
        platform="instagram", access_token="access", account_id="ig",
        clip_path=media, title="Title", description="Description",
        hashtags="#clip", post_id="post-ig",
    )
    facebook = await publish_post(
        platform="facebook", access_token="access", account_id="page",
        clip_path=media, title="Title", description="Description",
        hashtags="#clip", post_id="post-fb",
    )
    assert instagram["external_post_id"] == "instagram-post"
    assert facebook["external_post_id"] == "facebook-post"
    assert [request.url.path.rsplit("/", 1)[-1] for request in requests] == [
        "media", "media_publish", "videos"
    ]


@pytest.mark.asyncio
async def test_youtube_auto_import_parses_atom_links(monkeypatch):
    feed = b"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:yt="http://www.youtube.com/xml/schemas/2015">
      <entry>
        <yt:videoId>video-123</yt:videoId>
        <title>Fresh upload</title>
        <link rel="alternate" href="https://www.youtube.com/watch?v=video-123" />
      </entry>
    </feed>"""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=feed)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        source_import_service.httpx,
        "AsyncClient",
        lambda **_kwargs: real_client(transport=httpx.MockTransport(handler)),
    )
    items = await fetch_subscription_items(
        {"provider": "youtube", "external_source_id": "UC1234567890123456789012"}
    )
    assert [(item.external_id, item.url, item.title) for item in items] == [
        ("video-123", "https://www.youtube.com/watch?v=video-123", "Fresh upload")
    ]


@pytest.mark.asyncio
async def test_translated_and_dubbed_variant_orchestration(monkeypatch, tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")

    async def translate(_text: str, language: str):
        return LocalizedTranscript(language=language, translated_text="Texto traducido.")

    async def run_locally(function, *args):
        if function is variant_service._write_even_srt:
            function(*args)
        else:
            args[-2 if function is variant_service._mux_dub else -1].write_bytes(b"rendered")

    class SpeechResponse:
        def read(self):
            return b"audio"

    class Speech:
        async def create(self, **_kwargs):
            return SpeechResponse()

    class OpenAIClient:
        def __init__(self, **_kwargs):
            self.audio = SimpleNamespace(speech=Speech())

    config = Config()
    config.openai_api_key = "openai-test-key"
    set_config_override(config)
    monkeypatch.setattr(variant_service, "translate_transcript", translate)
    monkeypatch.setattr(variant_service, "run_in_thread", run_locally)
    monkeypatch.setattr(variant_service, "AsyncOpenAI", OpenAIClient)

    translated_output = tmp_path / "translated.mp4"
    translated_text, translated_metadata = await render_localized_variant(
        source_path=source, transcript="Original.", duration=4,
        output_path=translated_output, target_language="Spanish",
    )
    dubbed_output = tmp_path / "dubbed.mp4"
    dubbed_text, dubbed_metadata = await render_localized_variant(
        source_path=source, transcript="Original.", duration=4,
        output_path=dubbed_output, target_language="Spanish", dub=True,
    )
    assert translated_text == dubbed_text == "Texto traducido."
    assert translated_metadata["ai_voice"] is False
    assert dubbed_metadata["ai_voice"] is True
    assert translated_output.read_bytes() == dubbed_output.read_bytes() == b"rendered"
    assert dubbed_output.with_suffix(".mp3").read_bytes() == b"audio"


def test_brand_kit_applies_logo_then_music_without_mutating_on_failure(
    monkeypatch, tmp_path: Path
):
    clip = tmp_path / "clip.mp4"
    logo = tmp_path / "logo.png"
    music = tmp_path / "music.mp3"
    clip.write_bytes(b"original")
    logo.write_bytes(b"logo")
    music.write_bytes(b"music")
    passes = []

    def overlay(source, _asset, output, position):
        passes.append(("logo", position))
        output.write_bytes(source.read_bytes() + b"+logo")

    def mix(source, _asset, output, volume):
        passes.append(("music", volume))
        output.write_bytes(source.read_bytes() + b"+music")

    monkeypatch.setattr(brand_service, "_overlay_logo", overlay)
    monkeypatch.setattr(brand_service, "_mix_music", mix)
    assert apply_brand_kit_to_clip(
        clip,
        [{"asset_type": "logo", "file_path": str(logo)},
         {"asset_type": "music", "file_path": str(music)}],
        {"logo_position": "bottom_left", "music_volume": 0.2},
    )
    assert clip.read_bytes() == b"original+logo+music"
    assert passes == [("logo", "bottom_left"), ("music", 0.2)]

    def fail(*_args):
        raise RuntimeError("ffmpeg failed")

    clip.write_bytes(b"safe-original")
    monkeypatch.setattr(brand_service, "_overlay_logo", fail)
    assert not apply_brand_kit_to_clip(
        clip, [{"asset_type": "logo", "file_path": str(logo)}], {}
    )
    assert clip.read_bytes() == b"safe-original"


@pytest.mark.asyncio
async def test_workspace_viewers_are_read_only():
    role_db = FakeDatabase(
        lambda _query, _values: MappingResult([{"role": "viewer"}])
    )
    with pytest.raises(Exception) as exc_info:
        await workflows._require_workspace(
            role_db, "workspace-1", "viewer-1", {"owner", "admin", "editor"}
        )
    assert getattr(exc_info.value, "status_code", None) == 403

    clip_db = FakeDatabase(
        lambda _query, _values: MappingResult(
            [{"id": "clip-1", "workspace_role": "viewer", "file_path": "/clip.mp4"}]
        )
    )
    readable = await clip_workflows._owned_clip(
        clip_db, "clip-1", "viewer-1", require_write=False
    )
    assert readable["id"] == "clip-1"
    with pytest.raises(Exception) as clip_exc:
        await clip_workflows._owned_clip(
            clip_db, "clip-1", "viewer-1", require_write=True
        )
    assert getattr(clip_exc.value, "status_code", None) == 403


@pytest.mark.asyncio
async def test_signed_webhook_delivery(monkeypatch):
    captured = {}

    def database_handler(query: str, _values: dict):
        if "SELECT id, url, secret_encrypted, events" in query:
            return MappingResult(
                [{"id": "hook-1", "url": "https://hooks.example.test/supoclip",
                  "secret_encrypted": "encrypted", "events": '["task.completed"]'}]
            )
        return MappingResult()

    database = FakeDatabase(database_handler)

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(204)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(webhook_service, "decrypt_setting_value", lambda _value: "hook-secret")
    monkeypatch.setattr(
        webhook_service.httpx,
        "AsyncClient",
        lambda **_kwargs: real_client(transport=httpx.MockTransport(handler)),
    )
    deliveries = await emit_webhook_event(
        database, user_id="user-1", event_type="task.completed",
        payload={"task_id": "task-1"},
    )
    request = captured["request"]
    timestamp = request.headers["X-SupoClip-Timestamp"]
    expected = hmac.new(
        b"hook-secret", f"{timestamp}.".encode() + request.content, hashlib.sha256
    ).hexdigest()
    assert deliveries[0]["status"] == "delivered"
    assert request.headers["X-SupoClip-Signature"] == f"v1={expected}"
    assert json.loads(request.content)["data"] == {"task_id": "task-1"}


@pytest.mark.asyncio
async def test_tiktok_reconciliation_distinguishes_complete_and_rejected(monkeypatch):
    rows = [
        {"id": "post-complete", "external_post_id": "publish-complete",
         "social_account_id": "account-1", "access_token_encrypted": "token",
         "refresh_token_encrypted": None, "token_expires_at": None},
        {"id": "post-failed", "external_post_id": "publish-failed",
         "social_account_id": "account-1", "access_token_encrypted": "token",
         "refresh_token_encrypted": None, "token_expires_at": None},
    ]

    def database_handler(query: str, _values: dict):
        if "FROM social_posts sp" in query:
            return MappingResult(rows)
        return MappingResult()

    async def status(_token: str, publish_id: str):
        if publish_id == "publish-complete":
            return {"status": "PUBLISH_COMPLETE"}
        return {"status": "FAILED", "fail_reason": "moderation_rejected"}

    database = FakeDatabase(database_handler)
    monkeypatch.setattr(social_routes, "decrypt_setting_value", lambda _value: "access")
    monkeypatch.setattr(social_routes, "fetch_tiktok_publish_status", status)
    result = await social_routes.reconcile_tiktok_posts(database)
    assert result == {
        "published": ["post-complete"], "processing": [],
        "rejected": ["post-failed"], "errors": [],
    }
    update_statuses = [
        values for query, values in database.calls if "UPDATE social_posts SET status" in query
    ]
    assert [values["id"] for values in update_statuses] == [
        "post-complete", "post-failed"
    ]
