"""Non-destructive editor persistence and safe task-scoped media uploads."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import logging
import math
from pathlib import Path
import shutil
import subprocess
from typing import Any
from uuid import uuid4

import aiofiles
from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Config, get_config
from ..models import EditorAsset
from ..repositories.editor_repository import EditorRepository


logger = logging.getLogger(__name__)

MAX_EDITOR_PROJECT_BYTES = 2_000_000
EDITOR_ASSET_MAX_BYTES = {
    "video": 1_000_000_000,
    "image": 25_000_000,
    "audio": 250_000_000,
}
UPLOAD_CHUNK_BYTES = 1024 * 1024
MAX_EDITOR_VIDEO_PIXELS = 3840 * 2160
EDITOR_ITEM_TYPES = {"video", "image", "audio", "text", "caption", "shape"}
EDITOR_TRACKS = {"main", "overlay", "text", "audio"}
EDITOR_BLEND_MODES = {"normal", "multiply", "screen", "overlay", "darken", "lighten"}


@dataclass(frozen=True)
class AssetTypeSpec:
    kind: str
    extensions: frozenset[str]


@dataclass(frozen=True)
class AssetMetadata:
    duration: float | None
    width: int | None
    height: int | None
    video_codec: str | None = None
    audio_codec: str | None = None
    pixel_format: str | None = None


ASSET_TYPE_BY_MIME: dict[str, AssetTypeSpec] = {
    "video/mp4": AssetTypeSpec("video", frozenset({".mp4"})),
    "video/quicktime": AssetTypeSpec("video", frozenset({".mov"})),
    "video/webm": AssetTypeSpec("video", frozenset({".webm"})),
    "video/x-matroska": AssetTypeSpec("video", frozenset({".mkv"})),
    "image/jpeg": AssetTypeSpec("image", frozenset({".jpg", ".jpeg"})),
    "image/png": AssetTypeSpec("image", frozenset({".png"})),
    "image/webp": AssetTypeSpec("image", frozenset({".webp"})),
    "audio/mpeg": AssetTypeSpec("audio", frozenset({".mp3"})),
    "audio/mp3": AssetTypeSpec("audio", frozenset({".mp3"})),
    "audio/wav": AssetTypeSpec("audio", frozenset({".wav"})),
    "audio/x-wav": AssetTypeSpec("audio", frozenset({".wav"})),
    "audio/mp4": AssetTypeSpec("audio", frozenset({".m4a"})),
    "audio/x-m4a": AssetTypeSpec("audio", frozenset({".m4a"})),
    "audio/aac": AssetTypeSpec("audio", frozenset({".aac"})),
    "audio/ogg": AssetTypeSpec("audio", frozenset({".ogg", ".oga"})),
    "audio/webm": AssetTypeSpec("audio", frozenset({".webm", ".weba"})),
    "audio/flac": AssetTypeSpec("audio", frozenset({".flac"})),
}


class EditorAssetError(ValueError):
    pass


class EditorAssetTooLarge(EditorAssetError):
    pass


class EditorAssetNotFound(EditorAssetError):
    pass


class EditorAssetInUse(EditorAssetError):
    pass


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def validate_editor_project(task_id: str, project: dict[str, Any]) -> None:
    """Reject malformed documents before they can replace a recoverable project."""
    if (
        project.get("schemaVersion") != 1
        or not isinstance(project.get("id"), str)
        or not isinstance(project.get("name"), str)
        or project.get("taskId") != task_id
        or not isinstance(project.get("version"), int)
        or not _finite_number(project.get("duration"))
    ):
        raise ValueError("Editor project schema is invalid")

    canvas = project.get("canvas")
    if not isinstance(canvas, dict) or not all(
        _finite_number(canvas.get(field)) for field in ("width", "height", "fps")
    ) or not isinstance(canvas.get("background"), str):
        raise ValueError("Editor project canvas is invalid")

    items = project.get("items")
    if not isinstance(items, list) or len(items) > 10_000:
        raise ValueError("Editor project items are invalid")

    for item in items:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("id"), str)
            or not isinstance(item.get("name"), str)
            or item.get("type") not in EDITOR_ITEM_TYPES
            or item.get("track") not in EDITOR_TRACKS
            or item.get("blendMode") not in EDITOR_BLEND_MODES
        ):
            raise ValueError("Editor project contains an invalid timeline item")
        if not all(
            _finite_number(item.get(field))
            for field in (
                "start",
                "duration",
                "trimStart",
                "speed",
                "volume",
                "opacity",
                "fadeIn",
                "fadeOut",
            )
        ) or not all(
            isinstance(item.get(field), bool)
            for field in ("muted", "hidden", "locked")
        ):
            raise ValueError("Editor project contains invalid timeline values")

        nested_numbers = {
            "transform": ("x", "y", "width", "height", "rotation"),
            "crop": ("top", "right", "bottom", "left"),
            "effects": ("brightness", "contrast", "saturation", "blur", "hue"),
        }
        if any(
            not isinstance(item.get(group), dict)
            or not all(_finite_number(item[group].get(field)) for field in fields)
            for group, fields in nested_numbers.items()
        ):
            raise ValueError("Editor project contains invalid layer settings")

        if item["type"] in {"video", "image", "audio"} and not isinstance(
            item.get("assetId"), str
        ):
            raise ValueError("Editor media layers must reference an asset")
        if item["type"] in {"text", "caption"} and not isinstance(
            item.get("text"), dict
        ):
            raise ValueError("Editor text layers must include text settings")
        if item["type"] == "shape" and not isinstance(item.get("shape"), dict):
            raise ValueError("Editor shape layers must include shape settings")


def _safe_display_name(filename: str | None) -> str:
    normalized = (filename or "").replace("\\", "/").replace("\x00", "")
    name = "".join(
        character
        for character in normalized.rsplit("/", 1)[-1]
        if ord(character) >= 32 and ord(character) != 127
    ).strip()
    if not name or name in {".", ".."}:
        raise EditorAssetError("A valid file name is required")
    return name[:255]


def classify_editor_upload(
    filename: str | None, content_type: str | None
) -> tuple[str, str, str]:
    name = _safe_display_name(filename)
    mime_type = (content_type or "").split(";", 1)[0].strip().lower()
    spec = ASSET_TYPE_BY_MIME.get(mime_type)
    if spec is None:
        raise EditorAssetError("Unsupported media type. Upload video, image, or audio")

    extension = Path(name).suffix.lower()
    if extension not in spec.extensions:
        raise EditorAssetError("File extension does not match its media type")
    return name, mime_type, spec.kind


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _is_animated_webp(path: Path) -> bool:
    try:
        with path.open("rb") as source:
            header = source.read(12)
            if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WEBP":
                return False
            while True:
                chunk_header = source.read(8)
                if len(chunk_header) != 8:
                    return False
                chunk_name = chunk_header[:4]
                chunk_size = int.from_bytes(chunk_header[4:], "little")
                if chunk_name in {b"ANIM", b"ANMF"}:
                    return True
                source.seek(chunk_size + (chunk_size % 2), 1)
    except OSError:
        return False


def _is_browser_ready_asset(
    path: Path, kind: str, metadata: AssetMetadata
) -> bool:
    extension = path.suffix.lower()
    if kind == "video":
        return (
            extension == ".mp4"
            and metadata.video_codec == "h264"
            and metadata.pixel_format in {"yuv420p", "yuvj420p"}
            and metadata.audio_codec in {None, "aac"}
            and (metadata.width or 0) * (metadata.height or 0)
            <= MAX_EDITOR_VIDEO_PIXELS
        )
    if kind == "audio":
        return (
            (extension == ".mp3" and metadata.audio_codec == "mp3")
            or (extension == ".m4a" and metadata.audio_codec == "aac")
            or (
                extension == ".wav"
                and metadata.audio_codec
                in {
                    "pcm_u8",
                    "pcm_s16le",
                    "pcm_s24le",
                    "pcm_s32le",
                    "pcm_f32le",
                }
            )
        )
    return True


def transcode_editor_asset_for_browser(
    path: Path,
    kind: str,
    metadata: AssetMetadata,
) -> Path:
    """Normalize valid media to codecs the preview and browser exporter can decode."""
    if kind == "image" or _is_browser_ready_asset(path, kind, metadata):
        return path

    output_suffix = ".mp4" if kind == "video" else ".m4a"
    temporary = path.with_name(f"{path.stem}.normalizing{output_suffix}")
    final_path = path.with_suffix(output_suffix)
    if kind == "video":
        source_width = metadata.width or 0
        source_height = metadata.height or 0
        within_pixel_limit = (
            source_width > 0
            and source_height > 0
            and source_width * source_height <= MAX_EDITOR_VIDEO_PIXELS
        )
        maximum_width, maximum_height = (
            (3840, 2160)
            if source_width >= source_height
            else (2160, 3840)
        )
        video_codec_args = (
            ["-c:v", "copy"]
            if metadata.video_codec == "h264"
            and metadata.pixel_format in {"yuv420p", "yuvj420p"}
            and within_pixel_limit
            else [
                "-vf",
                (
                    f"scale='min(iw,{maximum_width})':'min(ih,{maximum_height})'"
                    ":force_original_aspect_ratio=decrease,"
                    "scale=trunc(iw/2)*2:trunc(ih/2)*2"
                ),
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
            ]
        )
        command = [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-sn",
            "-dn",
            *video_codec_args,
            "-c:a",
            "aac",
            "-ac",
            "2",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(temporary),
        ]
    else:
        command = [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(path),
            "-vn",
            "-c:a",
            "aac",
            "-ac",
            "2",
            "-ar",
            "48000",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(temporary),
        ]

    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            raise EditorAssetError("Media conversion produced an empty file")
        if final_path != path:
            path.unlink(missing_ok=True)
        temporary.replace(final_path)
        return final_path
    except FileNotFoundError as exc:
        raise EditorAssetError("Media conversion is unavailable") from exc
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise EditorAssetError(
            "This media could not be converted to a browser-compatible format"
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)


def probe_editor_asset(path: Path, kind: str) -> AssetMetadata:
    """Verify uploaded bytes with ffprobe and return safe preview metadata."""
    if (
        kind == "image"
        and path.suffix.lower() == ".webp"
        and _is_animated_webp(path)
    ):
        raise EditorAssetError(
            "Animated WebP is not supported; upload a video instead"
        )

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        payload = json.loads(result.stdout)
    except (
        FileNotFoundError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
    ) as exc:
        raise EditorAssetError("Uploaded file is not valid media") from exc

    streams = payload.get("streams")
    if not isinstance(streams, list):
        raise EditorAssetError("Uploaded file has no readable media stream")

    if kind == "audio":
        stream = next(
            (item for item in streams if item.get("codec_type") == "audio"), None
        )
        if stream is None:
            raise EditorAssetError("Uploaded file does not contain audio")
        duration = _positive_float(stream.get("duration")) or _positive_float(
            (payload.get("format") or {}).get("duration")
        )
        if duration is None:
            raise EditorAssetError("Uploaded audio has no readable duration")
        return AssetMetadata(
            duration=duration,
            width=None,
            height=None,
            audio_codec=str(stream.get("codec_name") or "").lower() or None,
        )

    stream = next(
        (item for item in streams if item.get("codec_type") == "video"), None
    )
    if stream is None:
        raise EditorAssetError("Uploaded file does not contain visual media")

    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    if width <= 0 or height <= 0:
        raise EditorAssetError("Uploaded visual media has invalid dimensions")

    if kind == "image":
        image_codecs = {
            ".jpg": {"mjpeg", "jpeg"},
            ".jpeg": {"mjpeg", "jpeg"},
            ".png": {"png"},
            ".webp": {"webp"},
        }
        if str(stream.get("codec_name") or "").lower() not in image_codecs.get(
            path.suffix.lower(), set()
        ):
            raise EditorAssetError("Uploaded file is not a supported image")
        return AssetMetadata(duration=None, width=width, height=height)

    duration = _positive_float(stream.get("duration")) or _positive_float(
        (payload.get("format") or {}).get("duration")
    )
    if duration is None:
        raise EditorAssetError("Uploaded video has no readable duration")
    audio_stream = next(
        (item for item in streams if item.get("codec_type") == "audio"), None
    )
    return AssetMetadata(
        duration=duration,
        width=width,
        height=height,
        video_codec=str(stream.get("codec_name") or "").lower() or None,
        audio_codec=(
            str(audio_stream.get("codec_name") or "").lower() or None
            if audio_stream is not None
            else None
        ),
        pixel_format=str(stream.get("pix_fmt") or "").lower() or None,
    )


class EditorService:
    def __init__(
        self,
        db: AsyncSession,
        config: Config | None = None,
        repository: EditorRepository | None = None,
    ):
        self.db = db
        self.config = config or get_config()
        self.repository = repository or EditorRepository()

    def _asset_root(self, task_id: str) -> Path:
        task_key = hashlib.sha256(task_id.encode("utf-8")).hexdigest()
        return Path(self.config.temp_dir).resolve() / "editor_assets" / task_key

    @staticmethod
    def _serialize_asset(asset: EditorAsset) -> dict[str, Any]:
        return {
            "id": asset.id,
            "name": asset.name,
            "kind": asset.kind,
            "mime_type": asset.mime_type,
            "size_bytes": int(asset.size_bytes),
            "duration": asset.duration,
            "width": asset.width,
            "height": asset.height,
            "url": f"/tasks/{asset.task_id}/editor/assets/{asset.id}/file",
            "created_at": asset.created_at,
        }

    async def get_editor(self, task_id: str) -> dict[str, Any]:
        project = await self.repository.get_project(self.db, task_id)
        assets = await self.repository.list_assets(self.db, task_id)
        return {
            "project": project.project if project is not None else None,
            "version": project.version if project is not None else 0,
            "updated_at": project.updated_at if project is not None else None,
            "assets": [self._serialize_asset(asset) for asset in assets],
        }

    async def save_project(
        self,
        task_id: str,
        project: dict[str, Any],
        expected_version: int | None,
    ) -> dict[str, Any]:
        validate_editor_project(task_id, project)
        try:
            encoded = json.dumps(
                project,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("Editor project must be valid JSON") from exc
        if len(encoded) > MAX_EDITOR_PROJECT_BYTES:
            raise ValueError("Editor project is too large")

        saved = await self.repository.save_project(
            self.db, task_id, project, expected_version
        )
        return {
            "project": saved["project"],
            "version": saved["version"],
            "updated_at": saved["updated_at"],
        }

    async def list_assets(self, task_id: str) -> list[dict[str, Any]]:
        assets = await self.repository.list_assets(self.db, task_id)
        return [self._serialize_asset(asset) for asset in assets]

    async def upload_asset(
        self, task_id: str, uploaded_file: UploadFile
    ) -> dict[str, Any]:
        target_path: Path | None = None
        try:
            name, mime_type, kind = classify_editor_upload(
                uploaded_file.filename, uploaded_file.content_type
            )
            max_bytes = EDITOR_ASSET_MAX_BYTES[kind]
            if uploaded_file.size is not None and uploaded_file.size > max_bytes:
                raise EditorAssetTooLarge(
                    f"Uploaded {kind} exceeds the {max_bytes}-byte limit"
                )

            asset_id = str(uuid4())
            extension = Path(name).suffix.lower()
            asset_root = self._asset_root(task_id)
            asset_root.mkdir(parents=True, exist_ok=True)
            target_path = (asset_root / f"{asset_id}{extension}").resolve()

            written = 0
            async with aiofiles.open(target_path, "wb") as destination:
                while True:
                    chunk = await uploaded_file.read(UPLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        raise EditorAssetTooLarge(
                            f"Uploaded {kind} exceeds the {max_bytes}-byte limit"
                        )
                    await destination.write(chunk)

            if written <= 0:
                raise EditorAssetError("Uploaded file is empty")

            metadata = await asyncio.to_thread(probe_editor_asset, target_path, kind)
            target_path = await asyncio.to_thread(
                transcode_editor_asset_for_browser,
                target_path,
                kind,
                metadata,
            )
            metadata = await asyncio.to_thread(probe_editor_asset, target_path, kind)
            written = target_path.stat().st_size
            if written > max_bytes:
                raise EditorAssetTooLarge(
                    f"Converted {kind} exceeds the {max_bytes}-byte limit"
                )
            if kind == "video" and target_path.suffix.lower() == ".mp4":
                name = f"{Path(name).stem}.mp4"
                mime_type = "video/mp4"
            elif kind == "audio" and target_path.suffix.lower() == ".m4a":
                name = f"{Path(name).stem}.m4a"
                mime_type = "audio/mp4"
            asset = await self.repository.create_asset(
                self.db,
                asset_id=asset_id,
                task_id=task_id,
                name=name,
                kind=kind,
                mime_type=mime_type,
                size_bytes=written,
                file_path=str(target_path),
                duration=metadata.duration,
                width=metadata.width,
                height=metadata.height,
            )
            return self._serialize_asset(asset)
        except Exception:
            if target_path is not None:
                target_path.unlink(missing_ok=True)
            raise
        finally:
            await uploaded_file.close()

    async def get_asset(
        self, task_id: str, asset_id: str
    ) -> tuple[EditorAsset, Path]:
        asset = await self.repository.get_asset(self.db, task_id, asset_id)
        if asset is None:
            raise EditorAssetNotFound("Editor asset not found")

        asset_root = self._asset_root(task_id)
        asset_path = Path(asset.file_path).resolve()
        if not asset_path.is_relative_to(asset_root) or not asset_path.is_file():
            raise EditorAssetNotFound("Editor asset file not found")
        return asset, asset_path

    async def delete_asset(self, task_id: str, asset_id: str) -> None:
        asset = await self.repository.get_asset(self.db, task_id, asset_id)
        if asset is None:
            raise EditorAssetNotFound("Editor asset not found")

        project = await self.repository.get_project(self.db, task_id)
        project_items = project.project.get("items", []) if project else []
        if isinstance(project_items, list) and any(
            isinstance(item, dict) and item.get("assetId") == asset_id
            for item in project_items
        ):
            raise EditorAssetInUse(
                "Remove this asset from the saved timeline before deleting it"
            )

        deleted = await self.repository.delete_asset(self.db, task_id, asset_id)
        if not deleted:
            raise EditorAssetNotFound("Editor asset not found")

        asset_root = self._asset_root(task_id)
        asset_path = Path(asset.file_path).resolve()
        if asset_path.is_relative_to(asset_root):
            try:
                asset_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Unable to remove deleted editor asset %s", asset_path)

    async def delete_asset_and_references(
        self,
        task_id: str,
        asset_id: str,
        expected_version: int,
    ) -> dict[str, Any]:
        result = await self.repository.delete_asset_and_references(
            self.db,
            task_id,
            asset_id,
            expected_version,
        )
        if result is None:
            raise EditorAssetNotFound("Editor asset not found")

        asset_root = self._asset_root(task_id)
        asset_path = Path(result["file_path"]).resolve()
        if asset_path.is_relative_to(asset_root):
            try:
                asset_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Unable to remove deleted editor asset %s", asset_path)

        return {
            "project": result["project"],
            "version": result["version"],
            "updated_at": result["updated_at"],
        }

    async def delete_task_assets(self, task_id: str) -> None:
        """Remove the validated, task-scoped editor asset directory after task deletion."""
        asset_root = self._asset_root(task_id)
        editor_root = (Path(self.config.temp_dir).resolve() / "editor_assets").resolve()
        if asset_root.is_relative_to(editor_root):
            await asyncio.to_thread(shutil.rmtree, asset_root, ignore_errors=True)
