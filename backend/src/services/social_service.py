"""OAuth connection and direct social publishing adapters."""

from __future__ import annotations

import base64
import asyncio
import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from collections.abc import AsyncIterator
from urllib.parse import urlencode

import httpx

from ..config import get_config


async def _file_chunks(path: Path, chunk_size: int = 1024 * 1024) -> AsyncIterator[bytes]:
    """Stream a local file without blocking the async publishing worker."""
    with path.open("rb") as media:
        while True:
            chunk = await asyncio.to_thread(media.read, chunk_size)
            if not chunk:
                return
            yield chunk


def _state_secret() -> bytes:
    config = get_config()
    value = config.backend_auth_secret or os.getenv("BETTER_AUTH_SECRET") or config.openai_api_key
    if not value:
        raise RuntimeError("BACKEND_AUTH_SECRET is required for OAuth state signing")
    return value.encode()


def sign_state(payload: dict) -> str:
    data = {**payload, "exp": int(time.time()) + 600}
    raw = base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = hmac.new(_state_secret(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{signature}"


def verify_state(value: str) -> dict:
    raw, signature = value.rsplit(".", 1)
    expected = hmac.new(_state_secret(), raw.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("Invalid OAuth state")
    payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    if int(payload.get("exp", 0)) < int(time.time()):
        raise ValueError("Expired OAuth state")
    return payload


def oauth_config(platform: str) -> dict:
    config = get_config()
    callback = f"{config.backend_public_url}/social/oauth/{platform}/callback"
    if platform == "youtube":
        return {
            "client_id": config.google_oauth_client_id,
            "client_secret": config.google_oauth_client_secret,
            "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_url": "https://oauth2.googleapis.com/token",
            "scope": "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly",
            "redirect_uri": callback,
        }
    if platform == "tiktok":
        return {
            "client_id": config.tiktok_client_key,
            "client_secret": config.tiktok_client_secret,
            "authorize_url": "https://www.tiktok.com/v2/auth/authorize/",
            "token_url": "https://open.tiktokapis.com/v2/oauth/token/",
            "scope": "user.info.basic,video.publish,video.upload",
            "redirect_uri": callback,
        }
    if platform in {"instagram", "facebook"}:
        return {
            "client_id": config.meta_app_id,
            "client_secret": config.meta_app_secret,
            "authorize_url": "https://www.facebook.com/v23.0/dialog/oauth",
            "token_url": "https://graph.facebook.com/v23.0/oauth/access_token",
            "scope": "pages_show_list,pages_read_engagement,pages_manage_posts,instagram_basic,instagram_content_publish",
            "redirect_uri": callback,
        }
    raise ValueError("Unsupported OAuth provider")


def oauth_authorize_url(platform: str, state: str) -> str:
    config = oauth_config(platform)
    if not config["client_id"] or not config["client_secret"]:
        raise RuntimeError(f"{platform.title()} OAuth credentials are not configured")
    params = {
        "client_id": config["client_id"],
        "redirect_uri": config["redirect_uri"],
        "response_type": "code",
        "scope": config["scope"],
        "state": state,
    }
    if platform == "youtube":
        params.update({"access_type": "offline", "prompt": "consent"})
    return f"{config['authorize_url']}?{urlencode(params)}"


async def exchange_code(platform: str, code: str) -> dict:
    config = oauth_config(platform)
    data = {
        "client_id": config["client_id"], "client_secret": config["client_secret"],
        "code": code, "redirect_uri": config["redirect_uri"], "grant_type": "authorization_code",
    }
    if platform == "tiktok":
        data["client_key"] = data.pop("client_id")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(config["token_url"], data=data)
        response.raise_for_status()
        token = response.json()
    return token


async def refresh_access_token(platform: str, refresh_token: str) -> dict:
    """Refresh a provider access token for unattended scheduled publishing."""
    config = oauth_config(platform)
    if platform == "youtube":
        data = {
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    elif platform == "tiktok":
        data = {
            "client_key": config["client_id"],
            "client_secret": config["client_secret"],
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    else:
        raise RuntimeError(f"{platform.title()} does not provide a refresh token for this connection")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(config["token_url"], data=data)
        response.raise_for_status()
        token = response.json()
    if not token.get("access_token"):
        raise RuntimeError(f"{platform.title()} token refresh returned no access token")
    return token


async def account_identity(platform: str, access_token: str, token: dict) -> dict:
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        if platform == "youtube":
            response = await client.get(
                "https://www.googleapis.com/youtube/v3/channels",
                params={"part": "id,snippet", "mine": "true"}, headers=headers,
            )
            response.raise_for_status()
            item = response.json().get("items", [{}])[0]
            return {"id": item.get("id", "youtube"),
                    "name": (item.get("snippet") or {}).get("title", "YouTube channel"),
                    "metadata": {"thumbnail": (((item.get("snippet") or {}).get("thumbnails") or {}).get("default") or {}).get("url")}}
        if platform == "tiktok":
            response = await client.get(
                "https://open.tiktokapis.com/v2/user/info/",
                params={"fields": "open_id,display_name,avatar_url"}, headers=headers,
            )
            response.raise_for_status()
            user = (response.json().get("data") or {}).get("user") or {}
            return {"id": user.get("open_id") or token.get("open_id") or "tiktok",
                    "name": user.get("display_name") or "TikTok account", "metadata": user}
        response = await client.get(
            "https://graph.facebook.com/v23.0/me/accounts",
            params={"access_token": access_token, "fields": "id,name,access_token,instagram_business_account"},
        )
        response.raise_for_status()
        pages = response.json().get("data") or []
        if not pages:
            raise RuntimeError("No eligible Facebook Page was returned")
        page = pages[0]
        if platform == "instagram":
            instagram = page.get("instagram_business_account") or {}
            if not instagram.get("id"):
                raise RuntimeError("The selected Facebook Page has no connected Instagram professional account")
            return {"id": instagram["id"], "name": page.get("name") or "Instagram account",
                    "access_token": page.get("access_token") or access_token,
                    "metadata": {"page_id": page.get("id")}}
        return {"id": page["id"], "name": page.get("name") or "Facebook Page",
                "access_token": page.get("access_token") or access_token, "metadata": {}}


def tiktok_chunk_plan(size: int) -> tuple[int, int]:
    """Return TikTok's declared chunk size and count for a local upload."""
    if size <= 0:
        raise ValueError("TikTok video file is empty")
    max_chunk = 32 * 1024 * 1024
    chunk_size = size if size < 5 * 1024 * 1024 else max_chunk
    return chunk_size, max(1, size // chunk_size)


def sign_media_token(post_id: str, expires: int | None = None) -> str:
    expires = expires or int(time.time()) + 3600
    payload = f"{post_id}.{expires}"
    signature = hmac.new(_state_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{signature}"


def verify_media_token(post_id: str, token: str) -> bool:
    try:
        expires_text, signature = token.split(".", 1)
        if int(expires_text) < int(time.time()):
            return False
        expected = hmac.new(_state_secret(), f"{post_id}.{expires_text}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)
    except Exception:
        return False


async def publish_post(*, platform: str, access_token: str, account_id: str,
                       clip_path: Path, title: str, description: str,
                       hashtags: str, post_id: str) -> dict:
    caption = "\n\n".join(part for part in [description, hashtags] if part).strip()
    if platform == "youtube":
        metadata = {"snippet": {"title": title[:100], "description": caption[:5000]},
                    "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False}}
        async with httpx.AsyncClient(timeout=600) as client:
            start = await client.post(
                "https://www.googleapis.com/upload/youtube/v3/videos",
                params={"uploadType": "resumable", "part": "snippet,status"},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json; charset=UTF-8",
                    "X-Upload-Content-Length": str(clip_path.stat().st_size),
                    "X-Upload-Content-Type": "video/mp4",
                },
                json=metadata,
            )
            start.raise_for_status()
            upload_url = start.headers.get("Location")
            if not upload_url:
                raise RuntimeError("YouTube did not return a resumable upload URL")
            response = await client.put(
                upload_url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "video/mp4",
                    "Content-Length": str(clip_path.stat().st_size),
                },
                content=_file_chunks(clip_path),
            )
        response.raise_for_status()
        data = response.json()
        return {"external_post_id": data["id"], "response": data}
    if platform == "tiktok":
        size = clip_path.stat().st_size
        chunk_size, total_chunks = tiktok_chunk_plan(size)
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=UTF-8"}
        async with httpx.AsyncClient(timeout=600) as client:
            init = await client.post(
                "https://open.tiktokapis.com/v2/post/publish/video/init/", headers=headers,
                json={"post_info": {"title": (title + " " + hashtags)[:2200], "privacy_level": "SELF_ONLY",
                                    "disable_duet": False, "disable_comment": False, "disable_stitch": False},
                      "source_info": {"source": "FILE_UPLOAD", "video_size": size,
                                      "chunk_size": chunk_size, "total_chunk_count": total_chunks}},
            )
            init.raise_for_status()
            data = init.json().get("data") or {}
            with clip_path.open("rb") as media:
                offset = 0
                for index in range(total_chunks):
                    remaining = size - offset
                    length = remaining if index == total_chunks - 1 else min(chunk_size, remaining)
                    payload = media.read(length)
                    upload = await client.put(
                        data["upload_url"],
                        content=payload,
                        headers={
                            "Content-Type": "video/mp4",
                            "Content-Length": str(len(payload)),
                            "Content-Range": f"bytes {offset}-{offset + len(payload) - 1}/{size}",
                        },
                    )
                    upload.raise_for_status()
                    offset += len(payload)
        return {
            "external_post_id": data["publish_id"],
            "response": data,
            "pending": True,
        }
    config = get_config()
    media_token = sign_media_token(post_id)
    video_url = f"{config.backend_public_url}/social/media/{post_id}?token={media_token}"
    async with httpx.AsyncClient(timeout=120) as client:
        if platform == "instagram":
            create = await client.post(
                f"https://graph.facebook.com/v23.0/{account_id}/media",
                data={"media_type": "REELS", "video_url": video_url, "caption": caption,
                      "access_token": access_token},
            )
            create.raise_for_status()
            creation_id = create.json()["id"]
            # Meta processes containers asynchronously; caller retries this publish if not ready yet.
            publish = await client.post(
                f"https://graph.facebook.com/v23.0/{account_id}/media_publish",
                data={"creation_id": creation_id, "access_token": access_token},
            )
            publish.raise_for_status()
            data = publish.json()
            return {"external_post_id": data["id"], "response": data}
        if platform == "facebook":
            with clip_path.open("rb") as media:
                response = await client.post(
                    f"https://graph-video.facebook.com/v23.0/{account_id}/videos",
                    data={"description": caption, "title": title, "access_token": access_token},
                    files={"source": (clip_path.name, media, "video/mp4")},
                )
            response.raise_for_status()
            data = response.json()
            return {"external_post_id": data["id"], "response": data}
    raise ValueError("Unsupported publishing platform")


async def fetch_tiktok_publish_status(access_token: str, publish_id: str) -> dict:
    """Fetch TikTok's authoritative processing state for an uploaded post."""
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://open.tiktokapis.com/v2/post/publish/status/fetch/",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={"publish_id": publish_id},
        )
        response.raise_for_status()
        payload = response.json()
    error = payload.get("error") or {}
    if error.get("code") not in {None, "ok"}:
        raise RuntimeError(error.get("message") or error["code"])
    data = payload.get("data") or {}
    if not data.get("status"):
        raise RuntimeError("TikTok status response did not include a status")
    return data
