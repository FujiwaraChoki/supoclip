from __future__ import annotations

import hmac
import hashlib
import time

from fastapi import HTTPException, Request

from .config import Config


USER_ID_HEADER = "x-supoclip-user-id"
TIMESTAMP_HEADER = "x-supoclip-ts"
SIGNATURE_HEADER = "x-supoclip-signature"


def _expected_signature(secret: str, user_id: str, timestamp: str) -> str:
    payload = f"{user_id}:{timestamp}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def get_signed_user_id(request: Request, config: Config) -> str:
    user_id = request.headers.get(USER_ID_HEADER)
    timestamp = request.headers.get(TIMESTAMP_HEADER)
    signature = request.headers.get(SIGNATURE_HEADER)

    if not user_id or not timestamp or not signature:
        raise HTTPException(status_code=401, detail="Signed authentication required")

    if not config.backend_auth_secret:
        raise HTTPException(
            status_code=500,
            detail="Server authentication secret is not configured",
        )

    try:
        timestamp_int = int(timestamp)
    except ValueError as e:
        raise HTTPException(status_code=401, detail="Invalid auth timestamp") from e

    now = int(time.time())
    if abs(now - timestamp_int) > config.auth_signature_ttl_seconds:
        raise HTTPException(status_code=401, detail="Expired auth signature")

    expected = _expected_signature(config.backend_auth_secret, user_id, timestamp)
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="Invalid auth signature")

    return user_id
