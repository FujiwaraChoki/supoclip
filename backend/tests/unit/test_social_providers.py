import json
from urllib.parse import parse_qs, urlparse

import pytest

from src.config import Config
from src.social.base import SocialProviderError, build_caption
from src.social.instagram import InstagramProvider
from src.social.registry import get_provider, list_provider_status
from src.social.tiktok import TikTokProvider
from src.social.youtube import YouTubeProvider


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    """Returns canned responses keyed by (method, url-substring)."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def _dispatch(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        for (route_method, fragment), responses in self.routes.items():
            if route_method == method and fragment in url:
                if isinstance(responses, list):
                    return responses.pop(0)
                return responses
        raise AssertionError(f"Unexpected {method} {url}")

    def get(self, url, **kwargs):
        return self._dispatch("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._dispatch("POST", url, **kwargs)

    def put(self, url, **kwargs):
        return self._dispatch("PUT", url, **kwargs)


def _config(**overrides) -> Config:
    config = Config()
    config.youtube_oauth_client_id = "yt-client"
    config.youtube_oauth_client_secret = "yt-secret"
    config.tiktok_client_key = "tt-key"
    config.tiktok_client_secret = "tt-secret"
    config.instagram_app_id = "ig-app"
    config.instagram_app_secret = "ig-secret"
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


@pytest.fixture
def clip_file(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"0" * 1024)
    return str(path)


# ----------------------------------------------------------------------
# Shared helpers
# ----------------------------------------------------------------------
def test_build_caption_appends_hashtags_and_respects_limit():
    caption = build_caption("Hello world", ["one", "#two", " ", "three"], 2200)
    assert caption == "Hello world\n\n#one #two #three"

    trimmed = build_caption("x" * 100, ["tag"], 40)
    assert trimmed.endswith("#tag")
    assert len(trimmed) <= 40


def test_registry_reports_configuration_state():
    statuses = {row["provider"]: row for row in list_provider_status(_config(tiktok_client_key=None))}
    assert statuses["youtube"]["configured"] is True
    assert statuses["tiktok"]["configured"] is False
    assert statuses["tiktok"]["setup_hint"]
    assert statuses["instagram"]["supported_privacy_levels"] == ["public"]

    with pytest.raises(ValueError):
        get_provider("myspace", _config())


@pytest.mark.parametrize("value,expected", [
    ("youtube", frozenset({"youtube"})),
    (" YouTube, TIKTOK, youtube ", frozenset({"youtube", "tiktok"})),
    ("", frozenset()),
])
def test_provider_allowlist_configuration(monkeypatch, value, expected):
    monkeypatch.setenv("SOCIAL_PUBLISHING_PROVIDERS", value)
    assert Config().social_publishing_providers == expected


def test_provider_allowlist_defaults_and_rejects_typos(monkeypatch):
    monkeypatch.delenv("SOCIAL_PUBLISHING_PROVIDERS", raising=False)
    assert Config().social_publishing_providers == frozenset({"youtube", "tiktok", "instagram"})
    monkeypatch.setenv("SOCIAL_PUBLISHING_PROVIDERS", "youtub")
    with pytest.raises(ValueError, match="SOCIAL_PUBLISHING_PROVIDERS"):
        Config()


def test_credentials_do_not_enable_platforms_outside_rollout():
    config = _config(social_publishing_providers=frozenset({"youtube"}))
    statuses = {row["provider"]: row for row in list_provider_status(config)}
    assert statuses["youtube"]["configured"] is True
    for platform in ("tiktok", "instagram"):
        assert statuses[platform]["configured"] is False
        assert "disabled" in statuses[platform]["setup_hint"]


# ----------------------------------------------------------------------
# YouTube
# ----------------------------------------------------------------------
def test_youtube_authorize_url_requests_offline_upload_access():
    provider = YouTubeProvider(_config(), session=FakeSession({}))
    url = provider.build_authorize_url(state="abc", redirect_uri="https://app/cb")
    query = parse_qs(urlparse(url).query)
    assert query["client_id"] == ["yt-client"]
    assert query["state"] == ["abc"]
    assert query["access_type"] == ["offline"]
    assert "youtube.upload" in query["scope"][0]


def test_youtube_publish_uses_resumable_upload(clip_file):
    session = FakeSession(
        {
            ("POST", "upload/youtube/v3/videos"): FakeResponse(
                200, {}, headers={"Location": "https://upload.example/session"}
            ),
            ("PUT", "upload.example/session"): FakeResponse(200, {"id": "vid123"}),
        }
    )
    provider = YouTubeProvider(_config(), session=session)
    from src.social.base import PublishRequest

    result = provider.publish(
        "token",
        PublishRequest(
            file_path=clip_file,
            title="My clip",
            caption="Caption",
            hashtags=["podcast"],
            privacy_level="unlisted",
        ),
    )
    assert result.external_post_id == "vid123"
    assert result.external_url == "https://www.youtube.com/shorts/vid123"

    init_call = session.calls[0]
    put_call = session.calls[1]
    assert put_call[2]["headers"]["Authorization"] == "Bearer token"
    body = init_call[2]["json"]
    assert body["status"]["privacyStatus"] == "unlisted"
    assert "#Shorts" in body["snippet"]["description"]
    assert body["snippet"]["title"] == "My clip"
    assert init_call[2]["headers"]["X-Upload-Content-Length"] == "1024"


def test_youtube_metrics_and_reauth_detection():
    session = FakeSession(
        {
            ("GET", "/videos"): [
                FakeResponse(
                    200,
                    {"items": [{"statistics": {"viewCount": "12", "likeCount": "3", "commentCount": "1"}}]},
                ),
                FakeResponse(401, {"error": {"message": "Invalid Credentials", "code": 401}}),
            ]
        }
    )
    provider = YouTubeProvider(_config(), session=session)
    metrics = provider.fetch_metrics("token", "vid", {})
    assert metrics.as_dict() == {"views": 12, "likes": 3, "comments": 1, "shares": None, "saves": None}

    with pytest.raises(SocialProviderError) as exc:
        provider.fetch_metrics("token", "vid", {})
    assert exc.value.reauth is True


# ----------------------------------------------------------------------
# TikTok
# ----------------------------------------------------------------------
def test_tiktok_authorize_url_uses_pkce():
    provider = TikTokProvider(_config(), session=FakeSession({}))
    assert provider.uses_pkce is True
    url = provider.build_authorize_url(
        state="s", redirect_uri="https://app/cb", code_challenge="chal"
    )
    query = parse_qs(urlparse(url).query)
    assert query["client_key"] == ["tt-key"]
    assert query["code_challenge"] == ["chal"]
    assert query["code_challenge_method"] == ["S256"]
    assert "video.publish" in query["scope"][0]


def test_tiktok_rejects_privacy_levels_the_account_cannot_use(clip_file):
    session = FakeSession(
        {
            ("POST", "creator_info/query"): FakeResponse(
                200,
                {"data": {"privacy_level_options": ["SELF_ONLY"]}, "error": {"code": "ok"}},
            )
        }
    )
    provider = TikTokProvider(_config(), session=session)
    from src.social.base import PublishRequest

    with pytest.raises(SocialProviderError) as exc:
        provider.publish(
            "token",
            PublishRequest(
                file_path=clip_file, title="t", caption="c", hashtags=[], privacy_level="public"
            ),
        )
    assert "private" in str(exc.value)


def test_tiktok_publish_persists_handle_before_checking_status(clip_file):
    session = FakeSession(
        {
            ("POST", "creator_info/query"): FakeResponse(
                200,
                {
                    "data": {"privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"]},
                    "error": {"code": "ok"},
                },
            ),
            ("POST", "video/init"): FakeResponse(
                200,
                {
                    "data": {"publish_id": "pub-1", "upload_url": "https://upload.tiktok/x"},
                    "error": {"code": "ok"},
                },
            ),
            ("PUT", "upload.tiktok/x"): FakeResponse(201, {}),
            ("POST", "status/fetch"): FakeResponse(
                200,
                {
                    "data": {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": [7351]},
                    "error": {"code": "ok"},
                },
            ),
        }
    )
    provider = TikTokProvider(_config(), session=session)
    from src.social.base import PublishRequest

    result = provider.publish(
        "token",
        PublishRequest(
            file_path=clip_file, title="Title", caption="Caption", hashtags=["fyp"], privacy_level="public"
        ),
    )
    assert result.external_post_id is None
    assert result.pending_handle == "pub-1"
    assert not any("status/fetch" in call[1] for call in session.calls)
    completed = provider.resolve_pending("token", result.pending_handle)
    assert completed.external_post_id == "7351"

    init_body = next(call for call in session.calls if "video/init" in call[1])[2]["json"]
    assert init_body["post_info"]["privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert init_body["post_info"]["title"] == "Caption\n\n#fyp"
    assert init_body["source_info"] == {
        "source": "FILE_UPLOAD",
        "video_size": 1024,
        "chunk_size": 1024,
        "total_chunk_count": 1,
    }
    put_call = next(call for call in session.calls if call[0] == "PUT")
    assert put_call[2]["headers"]["Content-Range"] == "bytes 0-1023/1024"


def test_tiktok_pending_and_failed_status():
    session = FakeSession(
        {
            ("POST", "status/fetch"): [
                FakeResponse(200, {"data": {"status": "PROCESSING_UPLOAD"}, "error": {"code": "ok"}}),
                FakeResponse(
                    200,
                    {"data": {"status": "FAILED", "fail_reason": "video too long"}, "error": {"code": "ok"}},
                ),
            ]
        }
    )
    provider = TikTokProvider(_config(), session=session)
    assert provider.resolve_pending("token", "pub") is None
    with pytest.raises(SocialProviderError, match="video too long"):
        provider.resolve_pending("token", "pub")


def test_tiktok_metrics_parse():
    session = FakeSession(
        {
            ("POST", "video/query"): FakeResponse(
                200,
                {
                    "data": {
                        "videos": [
                            {"id": "1", "view_count": 500, "like_count": 20, "comment_count": 2, "share_count": 4}
                        ]
                    },
                    "error": {"code": "ok"},
                },
            )
        }
    )
    provider = TikTokProvider(_config(), session=session)
    metrics = provider.fetch_metrics("token", "1", {})
    assert metrics.views == 500 and metrics.shares == 4


# ----------------------------------------------------------------------
# Instagram
# ----------------------------------------------------------------------
def test_instagram_requires_public_media_url(clip_file):
    provider = InstagramProvider(_config(), session=FakeSession({}), sleep=lambda _: None)
    from src.social.base import PublishRequest

    with pytest.raises(SocialProviderError, match="publicly reachable"):
        provider.publish(
            "token",
            PublishRequest(file_path=clip_file, title="t", caption="c", hashtags=[], privacy_level="public"),
        )


def test_instagram_publish_polls_container_then_publishes(clip_file):
    session = FakeSession(
        {
            ("POST", "/me/media_publish"): FakeResponse(200, {"id": "media-9"}),
            ("POST", "/me/media"): FakeResponse(200, {"id": "container-1"}),
            ("GET", "/container-1"): [
                FakeResponse(200, {"status_code": "IN_PROGRESS"}),
                FakeResponse(200, {"status_code": "FINISHED"}),
            ],
            ("GET", "/media-9"): FakeResponse(200, {"permalink": "https://instagram.com/reel/abc"}),
        }
    )
    provider = InstagramProvider(_config(), session=session, sleep=lambda _: None)
    from src.social.base import PublishRequest

    result = provider.publish(
        "token",
        PublishRequest(
            file_path=clip_file,
            title="Title",
            caption="Caption",
            hashtags=["reels"],
            privacy_level="public",
            public_media_url="https://app.example/api/social/media/tok",
        ),
    )
    assert result.external_post_id == "media-9"
    assert result.external_url == "https://instagram.com/reel/abc"

    create_call = next(call for call in session.calls if call[1].endswith("/me/media"))
    # Tokens travel in the Authorization header, never in URLs or form bodies.
    for _method, _url, kwargs in session.calls:
        assert kwargs["headers"]["Authorization"] == "Bearer token"
        assert "access_token" not in (kwargs.get("params") or {})
        assert "access_token" not in (kwargs.get("data") or {})
    assert create_call[2]["data"]["media_type"] == "REELS"
    assert create_call[2]["data"]["video_url"] == "https://app.example/api/social/media/tok"
    assert create_call[2]["data"]["caption"] == "Caption\n\n#reels"


def test_instagram_metrics_fall_back_between_metric_sets():
    session = FakeSession(
        {
            ("GET", "/insights"): [
                FakeResponse(400, {"error": {"message": "views not supported", "code": 100}}),
                FakeResponse(
                    200,
                    {
                        "data": [
                            {"name": "plays", "values": [{"value": 900}]},
                            {"name": "shares", "values": [{"value": 12}]},
                            {"name": "saved", "values": [{"value": 5}]},
                        ]
                    },
                ),
            ],
            ("GET", "/media-9"): FakeResponse(200, {"like_count": 30, "comments_count": 4}),
        }
    )
    provider = InstagramProvider(_config(), session=session, sleep=lambda _: None)
    metrics = provider.fetch_metrics("token", "media-9", {})
    assert metrics.as_dict() == {"views": 900, "likes": 30, "comments": 4, "shares": 12, "saves": 5}


def test_instagram_profile_requires_professional_account():
    session = FakeSession(
        {
            ("GET", "/me"): FakeResponse(
                200, {"id": "1", "user_id": "2", "username": "me", "account_type": "PERSONAL"}
            )
        }
    )
    provider = InstagramProvider(_config(), session=session, sleep=lambda _: None)
    with pytest.raises(SocialProviderError, match="professional"):
        provider.fetch_profile("token")


def test_instagram_token_endpoint_failures_are_redacted():
    import requests

    from src.social.base import redacted_errors

    class LeakySession(FakeSession):
        def get(self, url, **kwargs):
            query = "&".join(f"{key}={value}" for key, value in kwargs["params"].items())
            raise requests.ConnectionError(f"Max retries exceeded with url: {url}?{query}")

    provider = InstagramProvider(_config(), session=LeakySession({}), sleep=lambda _: None)
    with pytest.raises(SocialProviderError) as exc:
        redacted_errors(provider.refresh_tokens)("IGQVJlong-lived-secret")
    assert "IGQVJlong-lived-secret" not in str(exc.value)
    assert exc.value.retryable is True
    assert exc.value.__suppress_context__ is True


# ----------------------------------------------------------------------
# Ambiguous network failures (the platform may already have the post)
# ----------------------------------------------------------------------
class RaisingSession(FakeSession):
    """FakeSession whose routes may be exceptions to raise."""

    def _dispatch(self, method, url, **kwargs):
        response = super()._dispatch(method, url, **kwargs)
        if isinstance(response, Exception):
            raise response
        return response


def _request(clip_file, **overrides):
    from src.social.base import PublishRequest

    fields = dict(file_path=clip_file, title="t", caption="c", hashtags=[], privacy_level="private")
    fields.update(overrides)
    return PublishRequest(**fields)


def _publish(provider, request):
    from src.social.base import redacted_errors

    return redacted_errors(provider.publish)("token", request)


def test_youtube_timeout_after_sending_the_video_is_outcome_unknown(clip_file):
    import requests

    def session(put_error):
        return RaisingSession({
            ("POST", "upload/youtube/v3/videos"): FakeResponse(
                200, {}, headers={"Location": "https://upload.example/session"}
            ),
            ("PUT", "upload.example/session"): put_error,
        })

    with pytest.raises(SocialProviderError) as exc:
        _publish(YouTubeProvider(_config(), session=session(requests.ReadTimeout("slow"))), _request(clip_file))
    assert exc.value.outcome_unknown is True
    assert exc.value.retryable is False
    assert "outcome unknown" in str(exc.value)

    # Never connected: nothing was sent, so a retry is safe.
    with pytest.raises(SocialProviderError) as exc:
        _publish(YouTubeProvider(_config(), session=session(requests.ConnectTimeout("no route"))), _request(clip_file))
    assert exc.value.outcome_unknown is False
    assert exc.value.retryable is True

    # A failure before the upload (session start) stays retryable.
    early = RaisingSession({("POST", "upload/youtube/v3/videos"): requests.ReadTimeout("slow")})
    with pytest.raises(SocialProviderError) as exc:
        _publish(YouTubeProvider(_config(), session=early), _request(clip_file))
    assert exc.value.outcome_unknown is False
    assert exc.value.retryable is True


def test_instagram_timeout_on_media_publish_is_outcome_unknown(clip_file):
    import requests

    def session(**routes):
        return RaisingSession({
            ("POST", "/me/media_publish"): routes.get("publish", FakeResponse(200, {"id": "m"})),
            ("POST", "/me/media"): routes.get("create", FakeResponse(200, {"id": "container-1"})),
            ("GET", "/container-1"): FakeResponse(200, {"status_code": "FINISHED"}),
        })

    request = _request(clip_file, privacy_level="public", public_media_url="https://media/x")
    provider = InstagramProvider(
        _config(), session=session(publish=requests.ConnectionError("reset")), sleep=lambda _: None
    )
    with pytest.raises(SocialProviderError) as exc:
        _publish(provider, request)
    assert exc.value.outcome_unknown is True

    provider = InstagramProvider(
        _config(), session=session(create=requests.ReadTimeout("slow")), sleep=lambda _: None
    )
    with pytest.raises(SocialProviderError) as exc:
        _publish(provider, request)
    assert exc.value.outcome_unknown is False
    assert exc.value.retryable is True


def test_tiktok_timeout_on_final_chunk_is_outcome_unknown(clip_file):
    import requests

    session = RaisingSession({
        ("POST", "creator_info/query"): FakeResponse(
            200, {"data": {"privacy_level_options": ["SELF_ONLY"]}, "error": {"code": "ok"}}
        ),
        ("POST", "video/init"): FakeResponse(
            200,
            {"data": {"publish_id": "pub-1", "upload_url": "https://upload.tiktok/x"}, "error": {"code": "ok"}},
        ),
        ("PUT", "upload.tiktok/x"): requests.ReadTimeout("slow"),
    })
    with pytest.raises(SocialProviderError) as exc:
        _publish(TikTokProvider(_config(), session=session), _request(clip_file))
    assert exc.value.outcome_unknown is True


# ----------------------------------------------------------------------
# Revocation
# ----------------------------------------------------------------------
def test_youtube_revokes_the_refresh_token():
    session = FakeSession({("POST", "oauth2.googleapis.com/revoke"): FakeResponse(200, {})})
    assert YouTubeProvider(_config(), session=session).revoke_tokens("access", "refresh") is True
    assert session.calls[0][2]["data"] == {"token": "refresh"}

    failing = FakeSession({("POST", "/revoke"): FakeResponse(400, {"error": "invalid_token"})})
    with pytest.raises(SocialProviderError):
        YouTubeProvider(_config(), session=failing).revoke_tokens("access", None)
    assert YouTubeProvider(_config(), session=FakeSession({})).revoke_tokens(None, None) is False


def test_tiktok_revokes_with_client_credentials_and_refreshes_expired_tokens():
    session = FakeSession({("POST", "oauth/revoke"): FakeResponse(200, {})})
    assert TikTokProvider(_config(), session=session).revoke_tokens("access", "refresh") is True
    assert session.calls[0][2]["data"] == {
        "client_key": "tt-key", "client_secret": "tt-secret", "token": "access",
    }

    expired = FakeSession({
        ("POST", "oauth/revoke"): [
            FakeResponse(401, {"error": "invalid_token", "error_description": "expired"}),
            FakeResponse(200, {}),
        ],
        ("POST", "oauth/token"): FakeResponse(200, {"access_token": "fresh", "expires_in": 86400}),
    })
    assert TikTokProvider(_config(), session=expired).revoke_tokens("stale", "refresh") is True
    revoke_calls = [call for call in expired.calls if "revoke" in call[1]]
    assert revoke_calls[-1][2]["data"]["token"] == "fresh"

    rejected = FakeSession({("POST", "oauth/revoke"): FakeResponse(401, {"error": "invalid_token"})})
    with pytest.raises(SocialProviderError):
        TikTokProvider(_config(), session=rejected).revoke_tokens("stale", None)


def test_instagram_has_no_revocation_endpoint():
    session = FakeSession({})
    assert InstagramProvider(_config(), session=session).revoke_tokens("token", "token") is False
    assert session.calls == []


def test_tiktok_offers_public_and_private_only_and_defaults_to_private():
    provider = TikTokProvider(_config(), session=FakeSession({}))
    assert provider.supported_privacy_levels == ("public", "private")
    assert provider.default_privacy_level == "private"
    statuses = {row["provider"]: row for row in list_provider_status(_config())}
    assert statuses["tiktok"]["supported_privacy_levels"] == ["public", "private"]
    assert statuses["tiktok"]["default_privacy_level"] == "private"
    assert statuses["youtube"]["default_privacy_level"] == "public"


def test_tiktok_allowed_levels_are_named_readably(clip_file):
    session = FakeSession({
        ("POST", "creator_info/query"): FakeResponse(
            200,
            {"data": {"privacy_level_options": ["MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"]}, "error": {"code": "ok"}},
        )
    })
    from src.social.base import PublishRequest

    with pytest.raises(SocialProviderError) as exc:
        TikTokProvider(_config(), session=session).publish(
            "token",
            PublishRequest(file_path=clip_file, title="t", caption="c", hashtags=[], privacy_level="public"),
        )
    assert "Allowed: friends, private" in str(exc.value)


def test_tiktok_profile_only_requests_basic_scope_fields():
    session = FakeSession({
        ("GET", "/user/info/"): FakeResponse(
            200,
            {"data": {"user": {"open_id": "o1", "display_name": "Creator"}}, "error": {"code": "ok"}},
        )
    })
    profile = TikTokProvider(_config(), session=session).fetch_profile("token")
    fields = session.calls[0][2]["params"]["fields"].split(",")
    assert "username" not in fields
    assert profile.external_account_id == "o1"
    assert profile.display_name == "Creator"
    assert profile.username is None
