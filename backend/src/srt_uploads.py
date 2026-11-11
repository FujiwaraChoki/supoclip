"""User-supplied ``.srt`` subtitle files: references, ownership and storage.

An uploaded subtitle is stored content-addressed under the uploads volume, one
directory per owner, and referenced from a task as ``srt://<owner>/<sha256>.srt``
— the same "reference, not path" idea as ``upload://`` video files. Ownership is
part of the reference, so a task can never point at another user's file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Tuple
import hashlib
import os
import re
import uuid

from .config import get_config
from .media.srt_transcript import SRT_REFERENCE_PREFIX, SrtSubtitleError, parse_srt_bytes
from .media_storage import owner_key

SRT_MAX_UPLOAD_BYTES = 5 * 1024 * 1024
SRT_SUFFIX = ".srt"
SRT_UPLOAD_SUBDIR = "uploads/srt"
SRT_UPLOAD_TEMP_SUBDIR = "uploads/srt-tmp"

_REFERENCE_RE = re.compile(r"^srt://([0-9a-f]{64})/([0-9a-f]{64})\.srt$")


def is_srt_reference(value: object) -> bool:
    return isinstance(value, str) and _REFERENCE_RE.match(value.strip()) is not None


def srt_uploads_dir() -> Path:
    return Path(get_config().temp_dir) / SRT_UPLOAD_SUBDIR


def srt_upload_temp_dir() -> Path:
    return Path(get_config().temp_dir) / SRT_UPLOAD_TEMP_SUBDIR


def srt_upload_path(reference: str) -> Path:
    """Path for a validated ``srt://`` reference (no filesystem access)."""
    match = _REFERENCE_RE.match((reference or "").strip())
    if not match:
        raise SrtSubtitleError("The subtitle reference is not valid.")
    owner, digest = match.groups()
    return srt_uploads_dir() / owner / f"{digest}.srt"


def srt_digest(reference: str) -> str:
    """Content digest encoded in a ``srt://`` reference; used in cache keys."""
    match = _REFERENCE_RE.match((reference or "").strip())
    if not match:
        raise SrtSubtitleError("The subtitle reference is not valid.")
    return match.group(2)


def store_srt_bytes(user_id: str, raw: bytes) -> Tuple[str, int]:
    """Validate subtitle bytes and store them content-addressed for one owner.

    Returns ``(reference, cue_count)``.
    """
    if len(raw) > SRT_MAX_UPLOAD_BYTES:
        raise SrtSubtitleError("The subtitle file is larger than the allowed size.")
    cue_count = len(parse_srt_bytes(raw))
    digest = hashlib.sha256(raw).hexdigest()
    owner = owner_key(user_id)
    target = srt_uploads_dir() / owner / f"{digest}.srt"
    if not target.is_file() or target.stat().st_size != len(raw):
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_bytes(raw)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return f"{SRT_REFERENCE_PREFIX}{owner}/{digest}.srt", cue_count


def validate_srt_reference(reference: str, user_id: str) -> Path:
    """Resolve a task's subtitle reference, proving it is this user's file."""
    match = _REFERENCE_RE.match((reference or "").strip())
    if not match:
        raise SrtSubtitleError("The subtitle reference is not valid.")
    owner, digest = match.groups()
    if owner != owner_key(user_id):
        raise SrtSubtitleError("That subtitle file belongs to another account.")
    path = srt_uploads_dir() / owner / f"{digest}.srt"
    if not path.is_file():
        raise SrtSubtitleError("The subtitle file is no longer available. Upload it again.")
    if path.stat().st_size > SRT_MAX_UPLOAD_BYTES:
        raise SrtSubtitleError("The subtitle file is larger than the allowed size.")
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise SrtSubtitleError("The subtitle file changed after upload. Upload it again.")
    return path


def resolve_srt_path(reference: str) -> Path:
    """Worker-side path lookup for a reference already validated at task creation."""
    return srt_upload_path(reference)