"""Private durable media storage, with leased local files for ffmpeg.

Database paths stay strings: existing filesystem paths and new s3:// references
can coexist. URLs are signed only after the API has checked access.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from contextlib import contextmanager
from functools import wraps
import fcntl
import hashlib
import json
import mimetypes
import logging
from pathlib import Path, PurePosixPath
import shutil
import threading
import time
from urllib.parse import quote, unquote, urlsplit
import uuid

import boto3
from boto3.exceptions import Boto3Error
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from .config import get_config
from .editor_document import atomic_json
from .media.upload_limits import upload_too_large_detail

SIDECARS = (".source_map.json", ".transcript_cache.json")
PART_BYTES = 64 * 1024 * 1024
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".3gp", ".3g2", ".mts", ".m2ts", ".ts", ".mpg", ".mpeg", ".wmv", ".flv", ".ogv", ".vob", ".mxf", ".asf"}
STORAGE_ERRORS = (Boto3Error, BotoCoreError, ClientError, OSError)
_workspace: ContextVar[dict | None] = ContextVar("media_workspace", default=None)


def is_s3(reference: str) -> bool:
    return reference.startswith("s3://")


def owner_key(user_id: str) -> str:
    return hashlib.sha256(user_id.encode()).hexdigest()


def media_work(method):
    """Keep downloads leased until a render finishes, including cancellation."""
    @wraps(method)
    async def wrapped(*args, **kwargs):
        if _workspace.get() is not None:
            return await method(*args, **kwargs)
        leases = {"files": {}, "storages": {}, "editors": {}, "lock": threading.Lock()}
        token = _workspace.set(leases)
        try:
            return await method(*args, **kwargs)
        finally:
            _workspace.reset(token)
            await asyncio.to_thread(_release_workspace, leases)
    return wrapped


def _release_workspace(leases):
    for handle in leases.get("editors", {}).values():
        handle.close()
    for directory, handle in leases["files"].items():
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()
        # Other processes may still be using the same immutable source.
        with (directory / ".lease").open("a") as exclusive:
            try:
                fcntl.flock(exclusive, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            storage = leases.get("storages", {}).get(directory)
            marker = directory / ".reference"
            if storage and marker.exists():
                try:
                    key = storage.parse(marker.read_text())
                    if key.startswith(storage.key("sources") + "/"):
                        storage.save_source_metadata(directory / PurePosixPath(key).name)
                except STORAGE_ERRORS + (ValueError,):
                    if any(p.name.endswith(SIDECARS) for p in directory.iterdir()):
                        (directory / ".metadata-pending").touch()
                    logging.getLogger(__name__).warning("Source timings retained for upload retry")
            # Leave the lock inode in place so a waiting reader cannot acquire
            # an unlinked lock while another process opens a replacement.
            with (directory / ".download").open("a") as download:
                try:
                    fcntl.flock(download, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                pending = (directory / ".metadata-pending").exists()
                for path in directory.iterdir():
                    if path.name in {".lease", ".download", ".reference", ".access", ".metadata-pending"}:
                        continue
                    if pending and path.name.endswith(SIDECARS):
                        continue
                    if path.is_file():
                        path.unlink(missing_ok=True)


class MediaStorage:
    def __init__(self, config=None, client=None):
        self.config = config or get_config()
        self.enabled = self.config.media_storage == "s3"
        self.bucket = self.config.media_s3_bucket
        self.prefix = self.config.media_s3_prefix
        self._client = client

    @property
    def client(self):
        if self._client is None:
            options = {
                "region_name": self.config.media_s3_region,
                # Pin the regional endpoint: some SDK versions presign against
                # s3.amazonaws.com, whose regional redirect invalidates SigV4.
                "endpoint_url": self.config.media_s3_endpoint or f"https://s3.{self.config.media_s3_region}.amazonaws.com",
                "config": BotoConfig(signature_version="s3v4", retries={"mode": "standard", "max_attempts": 4}),
            }
            if self.config.media_s3_access_key:
                options.update(
                    aws_access_key_id=self.config.media_s3_access_key,
                    aws_secret_access_key=self.config.media_s3_secret_key,
                    aws_session_token=self.config.media_s3_session_token,
                )
            if getattr(self.config, "media_fallback_enabled", False):
                from .media_replication import ReplicatedMediaClient

                options["config"] = BotoConfig(
                    signature_version="s3v4", connect_timeout=3, read_timeout=15,
                    retries={"mode": "standard", "total_max_attempts": 2},
                )
                primary = boto3.client("s3", **options)
                fallback = boto3.client(
                    "s3", region_name=self.config.media_fallback_region,
                    endpoint_url=self.config.media_fallback_endpoint,
                    aws_access_key_id=self.config.media_fallback_access_key,
                    aws_secret_access_key=self.config.media_fallback_secret_key,
                    config=BotoConfig(signature_version="s3v4", connect_timeout=3, read_timeout=15,
                                      retries={"mode": "standard", "total_max_attempts": 2},
                                      request_checksum_calculation="when_required",
                                      response_checksum_validation="when_required"),
                )
                self._client = ReplicatedMediaClient(
                    primary, fallback, self.bucket, self.config.media_fallback_bucket,
                    self.config.temp_dir, failover=self.config.media_failover_enabled,
                )
            else:
                self._client = boto3.client("s3", **options)
        return self._client

    def key(self, *parts: str) -> str:
        return "/".join(p for p in (self.prefix, *parts) if p)

    def reference(self, key: str) -> str:
        return f"s3://{self.bucket}/{quote(key, safe='/')}"

    def parse(self, reference: str) -> str:
        parsed = urlsplit(reference)
        key = unquote(parsed.path.lstrip("/"))
        if (parsed.scheme != "s3" or parsed.netloc != self.bucket or parsed.query or parsed.fragment
                or not key.startswith(self.key() + "/") or ".." in PurePosixPath(key).parts):
            raise ValueError("Media reference is outside the configured bucket/prefix")
        return key

    def source_reference(self, upload_url: str) -> str:
        if not upload_url.startswith("upload://s3/"):
            raise ValueError("Invalid S3 upload reference")
        relative = upload_url.removeprefix("upload://s3/")
        parts = relative.split("/")
        if len(parts) != 3 or any(not p or p in {".", ".."} for p in parts):
            raise ValueError("Invalid S3 upload reference")
        return self.reference(self.key("sources", relative))

    def validate_source(self, upload_url: str, user_id: str) -> str:
        reference = self.source_reference(upload_url)
        if not self.parse(reference).startswith(self.key("sources", owner_key(user_id)) + "/"):
            raise ValueError("Uploaded video does not belong to this user")
        metadata = self.client.head_object(Bucket=self.bucket, Key=self.parse(reference))
        size = metadata["ContentLength"]
        expected = metadata.get("Metadata", {}).get("expected-size")
        if size <= 0 or (expected and size != int(expected)):
            raise ValueError("Uploaded video has an invalid size")
        if size > self.config.max_video_upload_bytes:
            raise ValueError(upload_too_large_detail(self.config.max_video_upload_bytes))
        return reference

    def upload_file(self, path: Path, key: str, **extra) -> str:
        self.client.upload_file(str(path), self.bucket, key, ExtraArgs={
            "ContentType": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            **extra,
        })
        return self.reference(key)

    def persist_source(self, path: Path, user_id: str) -> tuple[str, str]:
        relative = f"{owner_key(user_id)}/{uuid.uuid4().hex}/video{self.video_extension(path.name)}"
        key = self.key("sources", relative)
        uploaded = []
        try:
            for suffix in SIDECARS:
                sidecar = path.with_suffix(suffix)
                if sidecar.exists():
                    uploaded.append(self.upload_file(sidecar, str(PurePosixPath(key).with_suffix(suffix))))
            uploaded.append(self.upload_file(path, key))
        except BaseException:
            for reference in uploaded:
                self.client.delete_object(Bucket=self.bucket, Key=self.parse(reference))
            raise
        return f"upload://s3/{relative}", self.reference(key)

    def persist_clip(self, path: str, task_id: str) -> str:
        if not self.enabled or is_s3(path):
            return path
        source = Path(path)
        key = self.key("clips", str(task_id), uuid.uuid4().hex, source.name)
        uploaded = []
        try:
            # Save timing and caption metadata alongside the video.
            for suffix in SIDECARS:
                sidecar = source.with_suffix(suffix)
                if sidecar.exists():
                    uploaded.append(self.upload_file(sidecar, str(PurePosixPath(key).with_suffix(suffix))))
            uploaded.append(self.upload_file(source, key))
        except BaseException:
            for reference in uploaded:
                self.client.delete_object(Bucket=self.bucket, Key=self.parse(reference))
            raise
        return self.reference(key)

    def _download(self, key: str, path: Path, *, optional=False):
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.part")
        try:
            from .media_replication import ReplicatedMediaClient

            if isinstance(self.client, ReplicatedMediaClient):
                self.client.download_file(self.bucket, key, str(temporary), optional=optional)
            else:
                self.client.download_file(self.bucket, key, str(temporary))
            temporary.replace(path)
            return True
        except ClientError as exc:
            if not optional or exc.response["Error"]["Code"] not in {"404", "NoSuchKey", "NotFound"}:
                raise
            return False
        finally:
            temporary.unlink(missing_ok=True)

    def local_path(self, reference: str, *, metadata_only=False) -> Path:
        if not is_s3(reference):
            path = Path(reference)
            # Older processing-cache rows contain the materialized path.
            # Resolve its durable marker so these readers acquire a lease too.
            cache_root = (Path(self.config.temp_dir) / "media-cache").resolve()
            marker = path.parent / ".reference"
            if path.resolve().is_relative_to(cache_root) and marker.exists():
                return self.local_path(marker.read_text(), metadata_only=metadata_only)
            return path
        key = self.parse(reference)
        digest = hashlib.sha256(reference.encode()).hexdigest()
        directory = Path(self.config.temp_dir) / "media-cache" / digest
        directory.mkdir(parents=True, exist_ok=True)
        leases = _workspace.get()
        if leases is not None:
            with leases["lock"]:
                if directory not in leases["files"]:
                    handle = (directory / ".lease").open("a")
                    fcntl.flock(handle, fcntl.LOCK_SH)
                    leases["files"][directory] = handle
                    leases.setdefault("storages", {})[directory] = self
        # Download atomically and serialize cache population across processes.
        with (directory / ".download").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            path = directory / PurePosixPath(key).name
            (directory / ".reference").write_text(reference)
            (directory / ".access").touch()
            for suffix in SIDECARS[:1] if metadata_only else SIDECARS:
                sidecar = path.with_suffix(suffix)
                missing = directory / f".{sidecar.name}.missing"
                if not sidecar.exists() and (not missing.exists() or time.time() - missing.stat().st_mtime >= self.config.media_cache_ttl_seconds):
                    if not self._download(str(PurePosixPath(key).with_suffix(suffix)), sidecar, optional=True):
                        missing.touch()
            if not metadata_only and not path.exists():
                capped = self.capped_source_key(key)
                # A previous job may already have stored a 1080p/60 master.
                # Misses fall through to the original upload.
                if not (capped and self._download(capped, path, optional=True)):
                    self._download(key, path)
            return path

    def caption_settings(self, reference: str):
        from .clip_source_map import load_clip_caption_settings
        try:
            with self.read_path(reference, metadata_only=True) as path:
                return load_clip_caption_settings(path)
        except STORAGE_ERRORS:
            # Listing clips should remain available during a storage outage.
            logging.getLogger(__name__).warning("Could not retrieve optional clip caption settings")
            return None

    @contextmanager
    def read_path(self, reference: str, *, metadata_only=False):
        """Lease synchronous metadata readers without evicting the shared cache."""
        if not is_s3(reference):
            yield self.local_path(reference, metadata_only=metadata_only)
            return
        self.parse(reference)
        directory = Path(self.config.temp_dir) / "media-cache" / hashlib.sha256(reference.encode()).hexdigest()
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / ".lease").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_SH)
            yield self.local_path(reference, metadata_only=metadata_only)

    def save_source_metadata(self, path: Path):
        marker = path.parent / ".reference"
        if not self.bucket or not marker.exists():
            return
        key = self.parse(marker.read_text())
        if not key.startswith(self.key("sources") + "/"):
            return
        sidecars = [path.with_suffix(s) for s in SIDECARS if path.with_suffix(s).exists()]
        if not sidecars:
            return
        pending = path.parent / ".metadata-pending"
        pending.touch()
        with (path.parent / ".download").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            for sidecar in sidecars:
                self.upload_file(sidecar, str(PurePosixPath(key).with_suffix(sidecar.name.removeprefix(path.stem))))
            pending.unlink(missing_ok=True)

    def sync_source_metadata(self):
        root = Path(self.config.temp_dir) / "media-cache"
        if not self.bucket or not root.exists():
            return
        for directory in root.iterdir():
            marker = directory / ".reference"
            if not (directory / ".metadata-pending").exists() or not marker.exists():
                continue
            try:
                with (directory / ".lease").open("a") as handle:
                    fcntl.flock(handle, fcntl.LOCK_SH)
                    key = self.parse(marker.read_text())
                    self.save_source_metadata(directory / PurePosixPath(key).name)
            except STORAGE_ERRORS + (ValueError,):
                logging.getLogger(__name__).warning("Source metadata upload will retry")

    def youtube_metadata_reference(self, video_id: str) -> str:
        return self.reference(self.key("transcripts", f"{video_id}.transcript_cache.json"))

    def save_youtube_metadata(self, video_id: str, path: Path):
        sidecar = path.with_suffix(".transcript_cache.json")
        if self.enabled and sidecar.exists():
            self.upload_file(sidecar, self.parse(self.youtube_metadata_reference(video_id)))

    def restore_youtube_metadata(self, video_id: str, path: Path):
        sidecar = path.with_suffix(".transcript_cache.json")
        if self.enabled and not sidecar.exists():
            self._download(self.parse(self.youtube_metadata_reference(video_id)), sidecar, optional=True)

    def signed_url(self, reference: str, filename: str, *, attachment=False, expires_in: int | None = None) -> str:
        # Filenames originate from UUID-based application output names.
        safe_name = Path(filename).name.replace('"', "").replace("\r", "").replace("\n", "")
        return self.client.generate_presigned_url("get_object", Params={
            "Bucket": self.bucket, "Key": self.parse(reference),
            "ResponseContentDisposition": f'{"attachment" if attachment else "inline"}; filename="{safe_name}"',
        }, ExpiresIn=expires_in or self.config.media_url_ttl_seconds)

    def exists(self, reference: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self.parse(reference))
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def begin_upload(self, user_id: str, filename: str, size: int, content_type: str, *, authorization=None):
        if type(size) is not int or size <= 0:
            raise ValueError("Uploaded file is too large or empty")
        if size > self.config.max_video_upload_bytes:
            raise ValueError(upload_too_large_detail(self.config.max_video_upload_bytes))
        extension = self.video_extension(filename, content_type)
        relative = f"{owner_key(user_id)}/{uuid.uuid4().hex}/video{extension}"
        key = self.key("sources", relative)
        upload = self.client.create_multipart_upload(Bucket=self.bucket, Key=key,
            ContentType=content_type or "application/octet-stream", Metadata={"expected-size": str(size)})
        upload_id = upload["UploadId"]
        result = {"storage": "s3", "video_path": f"upload://s3/{relative}",
                  "uploadId": upload_id, "partSize": PART_BYTES}
        # Record the identity before signing, so callers retain it if signing
        # and the best-effort abort both fail.
        if authorization is not None:
            authorization.update(result)
        try:
            parts = self.upload_part_urls(key, upload_id, size, range(1, (size + PART_BYTES - 1) // PART_BYTES + 1))
        except BaseException:
            self.client.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id)
            raise
        result["parts"] = parts
        return result

    def capped_source_key(self, key: str) -> str | None:
        """Derived object for a source that has already been capped to 1080p/60."""
        if not key.startswith(self.key("sources") + "/"):
            return None
        path = PurePosixPath(key)
        if path.stem.endswith(".capped"):
            return None
        return str(path.with_name(f"{path.stem}.capped.mp4"))

    def store_capped_source(self, path: Path) -> None:
        """Upload the local capped master next to the stored original.

        The original object and its expected-size metadata stay put. Later
        downloads use the capped object so the worker does not transcode the
        same oversized upload on every edit.
        """
        marker = path.parent / ".reference"
        if not self.enabled or not marker.is_file():
            return
        key = self.parse(marker.read_text())
        capped = self.capped_source_key(key)
        if not capped:
            return
        self.upload_file(path, capped, ContentType="video/mp4")

    def owned_upload_key(self, video_path: str, user_id: str) -> str:
        key = self.parse(self.source_reference(video_path))
        if not key.startswith(self.key("sources", owner_key(user_id)) + "/"):
            raise ValueError("Uploaded video does not belong to this user")
        return key

    @staticmethod
    def video_extension(filename: str, content_type: str = "") -> str:
        suffix = Path(filename).suffix.lower()
        if suffix in VIDEO_SUFFIXES:
            return suffix
        guessed = mimetypes.guess_extension(content_type.split(";", 1)[0].lower())
        return guessed if guessed in VIDEO_SUFFIXES else ".mp4"

    def upload_part_urls(self, key: str, upload_id: str, size: int, numbers):
        return [{"partNumber": number, "url": self.client.generate_presigned_url(
            "upload_part", Params={"Bucket": self.bucket, "Key": key,
            "UploadId": upload_id, "PartNumber": number,
            "ContentLength": min(PART_BYTES, size - (number - 1) * PART_BYTES)}, ExpiresIn=3600,
        )} for number in numbers]

    def refresh_upload_parts(self, user_id: str, payload: dict, *, size: int):
        key = self.owned_upload_key(payload["video_path"], user_id)
        numbers = payload["partNumbers"]
        max_parts = (size + PART_BYTES - 1) // PART_BYTES
        if (type(size) is not int or not 0 < size <= self.config.max_video_upload_bytes
                or not isinstance(numbers, list) or not 0 < len(numbers) <= 3
                or any(type(n) is not int or not 1 <= n <= max_parts for n in numbers)):
            raise ValueError("Invalid multipart part numbers")
        if not isinstance(payload["uploadId"], str) or not payload["uploadId"]:
            raise ValueError("An upload ID is required")
        return {"parts": self.upload_part_urls(key, payload["uploadId"], size, numbers)}

    def _list_uploaded_parts(self, key: str, upload_id: str) -> list[dict]:
        collected: list[dict] = []
        marker = None
        while True:
            kwargs: dict = {"Bucket": self.bucket, "Key": key, "UploadId": upload_id, "MaxParts": 1000}
            if marker is not None:
                kwargs["PartNumberMarker"] = marker
            page = self.client.list_parts(**kwargs)
            collected.extend(page.get("Parts") or [])
            if not page.get("IsTruncated"):
                return collected
            marker = page.get("NextPartNumberMarker")
            if marker is None:
                return collected

    def _require_declared_part_sizes(self, key: str, upload_id: str, size: int) -> None:
        """Reject a multipart body that is not the size the part URLs signed.

        Presigned Content-Length is not an S3 bucket policy. A client can PUT a
        larger body when the signature does not pin that header, including a
        chunked part up to S3's 5 GB part maximum. Listing the parts before
        completion is what enforces the cap.
        """
        uploaded = self._list_uploaded_parts(key, upload_id)
        expected_count = (size + PART_BYTES - 1) // PART_BYTES
        expected_numbers = list(range(1, expected_count + 1))
        # An incomplete set must stay open so the client can finish uploading.
        # A part that is already larger than the signed length is aborted now:
        # waiting for the other parts would leave a multi-gigabyte part in the
        # bucket, and completing later would only then notice it.
        seen: dict[int, int] = {}
        oversized = False
        for part in uploaded:
            number = int(part.get("PartNumber") or 0)
            part_size = int(part.get("Size") or 0)
            if number not in expected_numbers or number in seen:
                if part_size > PART_BYTES or part_size > self.config.max_video_upload_bytes:
                    oversized = True
                continue
            expected_part = min(PART_BYTES, size - (number - 1) * PART_BYTES)
            if part_size > expected_part or part_size > self.config.max_video_upload_bytes:
                oversized = True
                continue
            seen[number] = part_size
        if oversized or sum(seen.values()) > self.config.max_video_upload_bytes:
            try:
                self.client.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id)
            except ClientError as exc:
                if exc.response["Error"]["Code"] != "NoSuchUpload":
                    raise
            raise ValueError(upload_too_large_detail(self.config.max_video_upload_bytes))
        if sorted(seen) != expected_numbers:
            raise ValueError("Multipart upload does not contain every expected part")
        if any(seen[number] != min(PART_BYTES, size - (number - 1) * PART_BYTES) for number in expected_numbers):
            raise ValueError("Uploaded video has an invalid size")
        if sum(seen.values()) != size:
            raise ValueError("Uploaded video has an invalid size")

    def complete_upload(self, user_id: str, payload: dict, *, size: int | None = None):
        key = self.owned_upload_key(payload["video_path"], user_id)
        parts = payload["parts"]
        if (not isinstance(parts, list) or not parts or len(parts) > 10000
                or any(not isinstance(p, dict) or type(p.get("partNumber")) is not int for p in parts)
                or [p.get("partNumber") for p in parts] != list(range(1, len(parts) + 1))
                or any(not isinstance(p.get("etag"), str) for p in parts)):
            raise ValueError("Invalid multipart upload parts")
        if size is not None and len(parts) != (size + PART_BYTES - 1) // PART_BYTES:
            raise ValueError("Multipart upload does not contain every expected part")
        if size is not None:
            self._require_declared_part_sizes(key, payload["uploadId"], size)
        try:
            self.client.complete_multipart_upload(Bucket=self.bucket, Key=key, UploadId=payload["uploadId"],
                MultipartUpload={"Parts": [{"PartNumber": p["partNumber"], "ETag": p["etag"]} for p in parts]})
        except ClientError as exc:
            # Retrying after a lost response or a failed HEAD is idempotent:
            # the completed object remains, but the multipart session is gone.
            if exc.response["Error"]["Code"] != "NoSuchUpload":
                raise
        try:
            self.validate_source(payload["video_path"], user_id)
        except ValueError:
            self.client.delete_object(Bucket=self.bucket, Key=key)
            raise
        except ClientError as exc:
            if exc.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}:
                raise ValueError("Upload is unavailable; restart the upload") from None
            raise
        return {"video_path": payload["video_path"]}

    def abort_upload(self, user_id: str, payload: dict):
        try:
            self.client.abort_multipart_upload(Bucket=self.bucket,
                Key=self.owned_upload_key(payload["video_path"], user_id), UploadId=payload["uploadId"])
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "NoSuchUpload":
                raise

    def delete(self, reference: str, *, prefix=False):
        if is_s3(reference):
            key = self.parse(reference)
            if prefix:
                for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=key.rstrip("/") + "/"):
                    for obj in page.get("Contents", []):
                        self.client.delete_object(Bucket=self.bucket, Key=obj["Key"])
            else:
                object_keys = [key]
                if PurePosixPath(key).suffix.lower() in VIDEO_SUFFIXES:
                    object_keys.extend(str(PurePosixPath(key).with_suffix(s)) for s in SIDECARS)
                    capped = self.capped_source_key(key)
                    if capped:
                        object_keys.append(capped)
                for object_key in object_keys:
                    self.client.delete_object(Bucket=self.bucket, Key=object_key)
            return
        path = Path(reference).resolve()
        root = Path(self.config.temp_dir).resolve()
        if not path.is_relative_to(root) or path == root:
            raise ValueError("Refusing media deletion outside TEMP_DIR")
        if prefix:
            shutil.rmtree(path, ignore_errors=False) if path.exists() else None
        else:
            path.unlink(missing_ok=True)
            for suffix in SIDECARS:
                path.with_suffix(suffix).unlink(missing_ok=True)
            path.with_name(f"{path.stem}.transcription.mp3").unlink(missing_ok=True)

    def editor_reference(self, directory: Path, filename: str = "") -> str:
        return self.reference(self.key("editor", directory.name, filename).rstrip("/"))

    def save_editor(self, directory: Path, *, metadata_only=False, blocking=True):
        if not self.enabled:
            return
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / ".sync").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError:
                return
            manifest = directory / ".s3-manifest"
            stamps = json.loads(manifest.read_text()) if manifest.exists() else {}
            for path in sorted(directory.iterdir(), key=lambda p: (p.suffix != ".json", p.name)):
                if not path.is_file() or path.name.startswith(".") or path.suffix not in {".json", ".jpg", ".mp4"}:
                    continue
                if metadata_only and path.suffix != ".json":
                    continue
                if path.name.startswith("export-") and path.suffix == ".mp4":
                    job = directory / f"job-{path.stem.removeprefix('export-')}.json"
                    if not job.exists() or json.loads(job.read_text()).get("status") != "completed":
                        continue
                if path.name == "clean.mp4":
                    asset = directory / "asset.json"
                    if not asset.exists() or json.loads(asset.read_text()).get("status") != "ready":
                        continue
                stat = path.stat()
                stamp = [stat.st_size, stat.st_mtime_ns]
                if stamps.get(path.name) == stamp:
                    continue
                self.upload_file(path, self.parse(self.editor_reference(directory, path.name)))
                stamps[path.name] = stamp
                atomic_json(manifest, stamps)

    def persist_editor_export(self, directory: Path, job_id: str):
        if not self.enabled:
            return
        path = directory / f"export-{job_id}.mp4"
        with (directory / ".sync").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            manifest = directory / ".s3-manifest"
            stamps = json.loads(manifest.read_text()) if manifest.exists() else {}
            self.upload_file(path, self.parse(self.editor_reference(directory, path.name)))
            stat = path.stat()
            stamps[path.name] = [stat.st_size, stat.st_mtime_ns]
            atomic_json(manifest, stamps)

    def restore_editor(self, directory: Path, *, media=False, s3_backed=False):
        directory.mkdir(parents=True, exist_ok=True)
        leases = _workspace.get()
        if media and leases is not None:
            with leases["lock"]:
                if directory not in leases.setdefault("editors", {}):
                    handle = (directory / ".media-lease").open("a")
                    fcntl.flock(handle, fcntl.LOCK_SH)
                    leases["editors"][directory] = handle
        (directory / ".access").touch()
        asset = directory / "asset.json"
        if asset.exists():
            metadata = json.loads(asset.read_text())
            ready = metadata.get("status") == "ready"
            drafts_ready = not ready or all((directory / name).exists() for name in ("draft.json", "original.json"))
            media_ready = ((directory / "clean.mp4").exists()
                           and (not metadata.get("hasLayout") or (directory / "layout.mp4").exists()))
            if drafts_ready and (not media or media_ready):
                return
        manifest = directory / ".s3-manifest"
        if not self.bucket or (not self.enabled and not manifest.exists() and not s3_backed):
            return
        prefix = self.parse(self.editor_reference(directory)) + "/"
        stamps = json.loads(manifest.read_text()) if manifest.exists() else {}
        checked = directory / ".s3-checked"
        if not media and checked.exists() and time.time() - checked.stat().st_mtime < 300:
            return
        # Shared local state is authoritative; avoid waiting on a video upload
        # (or contacting S3) on every editor poll when the needed files exist.
        if stamps and all((directory / name).exists() for name in stamps
                          if not name.endswith(".mp4") or (media and name in {"clean.mp4", "layout.mp4"})):
            return
        with (directory / ".sync").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            manifest = directory / ".s3-manifest"
            stamps = json.loads(manifest.read_text()) if manifest.exists() else {}
            for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    name = obj["Key"].removeprefix(prefix)
                    if "/" in name or name.startswith(".") or not name:
                        continue
                    path = directory / name
                    if path.exists() or (path.suffix == ".mp4" and (not media or name not in {"clean.mp4", "layout.mp4"})):
                        continue
                    self._download(obj["Key"], path)
                    stat = path.stat()
                    stamps[path.name] = [stat.st_size, stat.st_mtime_ns]
                    atomic_json(manifest, stamps)
            checked.touch()

    def sync_editor_drafts(self):
        root = Path(self.config.temp_dir) / "editor"
        if not self.enabled or not root.exists():
            return
        for directory in root.iterdir():
            if directory.is_dir():
                try:
                    self.save_editor(directory, blocking=False)
                except STORAGE_ERRORS:
                    logging.getLogger(__name__).warning("Editor snapshot sync will retry")

    def sweep_cache(self):
        root = Path(self.config.temp_dir) / "media-cache"
        if not root.exists():
            return
        for directory in root.iterdir():
            access = directory / ".access"
            if not directory.is_dir() or (access.exists() and time.time() - access.stat().st_mtime < self.config.media_cache_ttl_seconds):
                continue
            _release_workspace({"files": {directory: (directory / ".lease").open("a")}, "storages": {directory: self}})

    def sweep_editor_cache(self):
        if not self.enabled:
            return
        root = Path(self.config.temp_dir) / "editor"
        if not root.exists():
            return
        for directory in root.iterdir():
            manifest = directory / ".s3-manifest"
            access = directory / ".access"
            if not manifest.exists() or (access.exists() and time.time() - access.stat().st_mtime < self.config.media_cache_ttl_seconds):
                continue
            with (directory / ".media-lease").open("a") as lease:
                try:
                    fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                if access.exists() and time.time() - access.stat().st_mtime < self.config.media_cache_ttl_seconds:
                    continue
                for name, stamp in json.loads(manifest.read_text()).items():
                    path = directory / name
                    if path.suffix == ".mp4" and path.exists():
                        stat = path.stat()
                        if [stat.st_size, stat.st_mtime_ns] == stamp:
                            path.unlink(missing_ok=True)
