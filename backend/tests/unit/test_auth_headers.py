import hashlib
import hmac
import time

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.auth_headers import get_authenticated_user_id, get_signed_user_id
from src.config import Config


def _build_request(headers: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [
                (key.lower().encode("utf-8"), value.encode("utf-8"))
                for key, value in headers.items()
            ],
        }
    )


def test_get_signed_user_id_rejects_missing_headers():
    config = Config()
    config.backend_auth_secret = "secret"

    with pytest.raises(HTTPException) as exc:
        get_signed_user_id(_build_request({}), config)

    assert exc.value.status_code == 401


def test_get_signed_user_id_rejects_expired_signature():
    config = Config()
    config.backend_auth_secret = "secret"
    config.auth_signature_ttl_seconds = 1
    request = _build_request(
        {
            "x-supoclip-user-id": "user-1",
            "x-supoclip-ts": str(int(time.time()) - 10),
            "x-supoclip-signature": "invalid",
        }
    )

    with pytest.raises(HTTPException) as exc:
        get_signed_user_id(request, config)

    assert exc.value.status_code == 401


def test_get_authenticated_user_id_allows_unsigned_fallback_when_enabled():
    config = Config()
    config.backend_auth_secret = "secret"
    config.allow_unsigned_backend_auth = True

    user_id = get_authenticated_user_id(
        _build_request({"x-supoclip-user-id": "user-1"}),
        config,
    )

    assert user_id == "user-1"


def test_get_authenticated_user_id_keeps_rejecting_bad_signed_headers():
    config = Config()
    config.backend_auth_secret = "secret"
    config.allow_unsigned_backend_auth = True

    with pytest.raises(HTTPException) as exc:
        get_authenticated_user_id(
            _build_request(
                {
                    "x-supoclip-user-id": "user-1",
                    "x-supoclip-ts": str(int(time.time())),
                    "x-supoclip-signature": "invalid",
                }
            ),
            config,
        )

    assert exc.value.status_code == 401


def test_get_authenticated_user_id_accepts_valid_signed_headers():
    config = Config()
    config.backend_auth_secret = "secret"
    timestamp = str(int(time.time()))
    payload = f"user-1:{timestamp}".encode("utf-8")
    signature = hmac.new(b"secret", payload, hashlib.sha256).hexdigest()

    user_id = get_authenticated_user_id(
        _build_request(
            {
                "x-supoclip-user-id": "user-1",
                "x-supoclip-ts": timestamp,
                "x-supoclip-signature": signature,
            }
        ),
        config,
    )

    assert user_id == "user-1"
