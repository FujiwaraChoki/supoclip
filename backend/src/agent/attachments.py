"""Files sent with a message to the agent.

Videos are uploaded first (``upload://`` references) and can become projects. Images, PDFs and text
files travel inside the message: Claude sees the images and reads the documents. Each kind becomes
its Messages API content block here; the chat keeps a small preview and the file's name.
Limits match ``frontend/src/lib/agent-attachments.ts``.
"""

from __future__ import annotations

import base64
import binascii
import re
from typing import Any

MAX_ATTACHMENTS = 6
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 500_000
MAX_INLINE_BYTES = 18 * 1024 * 1024
MAX_PREVIEW_CHARS = 200_000
_UPLOAD_URL = re.compile(r"^upload://[A-Za-z0-9/_.:-]{1,400}$")
_PREVIEW = re.compile(r"^data:image/(jpeg|png|webp);base64,[A-Za-z0-9+/=]+$")


class AttachmentError(ValueError):
    """An attachment the server won't take; the message says which and why."""


def _name(item: dict, default: str) -> str:
    value = item.get("name")
    return (value if isinstance(value, str) and value.strip() else default).strip()[:200]


def _decoded_size(data: Any, name: str) -> int:
    if not isinstance(data, str) or not data:
        raise AttachmentError(f"{name} is empty.")
    try:
        return len(base64.b64decode(data, validate=True))
    except (binascii.Error, ValueError):
        raise AttachmentError(f"{name} couldn't be read.") from None


def parse_attachments(raw: Any) -> list[dict]:
    """Validated attachments in a normalized shape, or AttachmentError."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise AttachmentError("Attachments must be a list.")
    if len(raw) > MAX_ATTACHMENTS:
        raise AttachmentError(f"Up to {MAX_ATTACHMENTS} files per message.")
    parsed: list[dict] = []
    inline = 0
    for item in raw:
        if not isinstance(item, dict):
            continue
        # Older clients sent videos as {url, name}.
        kind = item.get("kind") or ("video" if "url" in item else None)
        if kind == "video":
            url = item.get("url")
            if isinstance(url, str) and _UPLOAD_URL.match(url):
                parsed.append({"kind": "video", "url": url, "name": _name(item, "video")})
            continue
        if kind == "image":
            name = _name(item, "image")
            media_type = item.get("media_type")
            if media_type not in IMAGE_TYPES:
                raise AttachmentError(f"{name} isn't an image type Agent can read.")
            size = _decoded_size(item.get("data"), name)
            if size > MAX_IMAGE_BYTES:
                raise AttachmentError(f"{name} is too big.")
            preview = item.get("preview")
            if not (isinstance(preview, str) and len(preview) <= MAX_PREVIEW_CHARS and _PREVIEW.match(preview)):
                preview = None
            inline += size
            parsed.append({"kind": "image", "name": name, "media_type": media_type, "data": item["data"],
                           "preview": preview, "size": size})
            continue
        if kind == "pdf":
            name = _name(item, "document.pdf")
            size = _decoded_size(item.get("data"), name)
            if size > MAX_PDF_BYTES:
                raise AttachmentError(f"{name} is too big. PDFs can be up to 10 MB.")
            inline += size
            parsed.append({"kind": "pdf", "name": name, "data": item["data"], "size": size})
            continue
        if kind == "text":
            name = _name(item, "notes.txt")
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                raise AttachmentError(f"{name} is empty.")
            if len(text) > MAX_TEXT_CHARS:
                raise AttachmentError(f"{name} is too long.")
            size = len(text.encode("utf-8"))
            inline += size
            parsed.append({"kind": "text", "name": name, "text": text, "size": size})
            continue
    if inline > MAX_INLINE_BYTES:
        raise AttachmentError("That's too much for one message. Send some of the files in the next one.")
    return parsed


def content_blocks(attachments: list[dict]) -> list[dict]:
    """Messages API blocks for the files Claude should see or read (videos are listed in the context)."""
    blocks: list[dict] = []
    for item in attachments:
        if item["kind"] == "image":
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": item["media_type"], "data": item["data"]}})
        elif item["kind"] == "pdf":
            blocks.append({
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": item["data"]},
                "title": item["name"],
            })
        elif item["kind"] == "text":
            blocks.append({
                "type": "document",
                "source": {"type": "text", "media_type": "text/plain", "data": item["text"]},
                "title": item["name"],
            })
    return blocks


def previews(attachments: list[dict]) -> list[dict]:
    """What the chat shows: name, kind, size, and a small preview for images. Never the file itself."""
    return [
        {
            "name": item["name"],
            "kind": item["kind"],
            **({"preview": item["preview"]} if item.get("preview") else {}),
            **({"size": item["size"]} if item.get("size") else {}),
        }
        for item in attachments
    ]
