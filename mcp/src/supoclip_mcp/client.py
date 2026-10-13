"""
Thin async HTTP client for the SupoClip backend API.

Authentication is resolved from :class:`~supoclip_mcp.config.Settings` and
supports three modes, tried in priority order:

1. ``api_key``         -> ``Authorization: Bearer <key>`` (recommended; the
   default for the hosted service).
2. ``signed_headers``  -> the frontend's HMAC scheme, when a user id and the
   backend auth secret are both provided (useful for self-hosting).
3. ``unsigned_user_id`` -> a bare ``x-supoclip-user-id`` header, for a
   self-hosted backend running with ``ALLOW_UNSIGNED_BACKEND_AUTH=true``.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urljoin, urlsplit

import httpx

from .config import Settings


class SupoClipError(Exception):
    """A friendly, already-formatted error suitable for returning to the model."""


class AuthNotConfiguredError(SupoClipError):
    """Raised when an authenticated operation is attempted without credentials."""


def _signed_headers(user_id: str, secret: str) -> Dict[str, str]:
    timestamp = str(int(time.time()))
    payload = f"{user_id}:{timestamp}".encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return {
        "x-supoclip-user-id": user_id,
        "x-supoclip-ts": timestamp,
        "x-supoclip-signature": signature,
    }


class SupoClipClient:
    """Async client wrapping the SupoClip REST API."""

    def __init__(self, settings: Settings):
        self.settings = settings

    # -- auth ---------------------------------------------------------------
    def _auth_headers(self) -> Dict[str, str]:
        mode = self.settings.auth_mode
        if mode == "api_key":
            return {"Authorization": f"Bearer {self.settings.api_key}"}
        if mode == "signed_headers":
            return _signed_headers(self.settings.user_id, self.settings.auth_secret)  # type: ignore[arg-type]
        if mode == "unsigned_user_id":
            return {"x-supoclip-user-id": self.settings.user_id}  # type: ignore[dict-item]
        return {}

    def _require_auth(self) -> None:
        if not self.settings.is_authenticated:
            raise AuthNotConfiguredError(
                "No credentials configured. Set SUPOCLIP_API_KEY to a key created "
                "in your SupoClip account (Settings -> API Keys). For a self-hosted "
                "backend you may instead set SUPOCLIP_USER_ID (+ SUPOCLIP_AUTH_SECRET "
                "if signing is enforced)."
            )

    # -- requests -----------------------------------------------------------
    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        authenticated: bool = True,
    ) -> Any:
        """Make a JSON request and return the decoded body."""
        if authenticated:
            self._require_auth()

        headers = {"Accept": "application/json"}
        if authenticated:
            headers.update(self._auth_headers())

        url = f"{self.settings.api_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.settings.timeout) as client:
                response = await client.request(
                    method, url, params=params, json=json_body, headers=headers
                )
        except httpx.TimeoutException as exc:
            raise SupoClipError(
                f"Request to {path} timed out after {self.settings.timeout:g}s."
            ) from exc
        except httpx.HTTPError as exc:
            raise SupoClipError(f"Could not reach SupoClip at {url}: {exc}") from exc

        return self._parse(response, path)

    async def download(
        self,
        path: str,
        destination_dir: str,
        filename: str,
        *,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Stream a binary file (a clip) to ``destination_dir`` and return its path."""
        self._require_auth()
        headers = self._auth_headers()
        url = f"{self.settings.api_url}{path}"

        dest_dir = Path(destination_dir).expanduser()
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / filename

        bytes_written = 0
        temporary = dest_path.with_name(f".{dest_path.name}.{uuid.uuid4().hex}.part")
        backend_origin = urlsplit(self.settings.api_url)[:2]
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                download_url = url
                for _ in range(6):
                    async with client.stream("GET", download_url, params=params, headers=headers) as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise SupoClipError("Download redirect has no destination.")
                            download_url = urljoin(download_url, location)
                            target = urlsplit(download_url)
                            same_backend = target[:2] == backend_origin
                            if target.scheme not in {"http", "https"} or (not same_backend and target.scheme != "https"):
                                raise SupoClipError("Download redirect must use HTTPS for external storage.")
                            # HTTP clients do not automatically strip custom HMAC
                            # headers on a cross-origin redirect. Send no backend
                            # credentials to storage; the URL carries its own grant.
                            headers = self._auth_headers() if same_backend else {}
                            params = None
                            continue
                        if response.status_code not in {200, 206}:
                            if urlsplit(download_url)[:2] != backend_origin:
                                raise SupoClipError(f"Storage download failed ({response.status_code}). Please retry.")
                            body = (await response.aread()).decode("utf-8", "replace")
                            raise SupoClipError(_error_message(response.status_code, body, path))
                        with temporary.open("wb") as handle:
                            async for chunk in response.aiter_bytes(chunk_size=1 << 16):
                                handle.write(chunk)
                                bytes_written += len(chunk)
                        temporary.replace(dest_path)
                        break
                else:
                    raise SupoClipError("Download redirected too many times.")
        except httpx.HTTPError as exc:
            # Exceptions may contain signed storage URLs. Keep them out of the
            # user-visible error while retaining the exception type for support.
            raise SupoClipError(f"Download from {path} failed ({type(exc).__name__}).") from exc
        finally:
            temporary.unlink(missing_ok=True)

        return {
            "path": str(dest_path.resolve()),
            "filename": filename,
            "bytes": bytes_written,
        }

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _parse(response: httpx.Response, path: str) -> Any:
        if response.status_code >= 400:
            raise SupoClipError(
                _error_message(response.status_code, response.text, path)
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {"raw": response.text}


def _error_message(status: int, body: str, path: str) -> str:
    """Turn a backend error response into an actionable message."""
    detail: Any = body
    try:
        import json

        parsed = json.loads(body)
        detail = parsed.get("detail", parsed) if isinstance(parsed, dict) else parsed
    except ValueError:
        pass

    if status == 401:
        return (
            "Authentication failed (401). Check SUPOCLIP_API_KEY is valid and not "
            f"revoked. Backend said: {detail}"
        )
    if status == 402:
        return (
            "Payment required (402): this account needs an active paid plan to "
            f"process videos. Backend said: {detail}"
        )
    if status == 403:
        return f"Permission denied (403): you do not have access to this resource. {detail}"
    if status == 404:
        return f"Not found (404) for {path}: {detail}"
    if status == 429:
        return "Rate limited (429). Wait a moment and try again."
    return f"SupoClip API error ({status}) for {path}: {detail}"
