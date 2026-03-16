import time

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.auth_headers import get_signed_user_id
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
