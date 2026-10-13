"""Private durable media storage, with leased local files for ffmpeg.

Database paths stay strings: existing filesystem paths and new s3:// references
can coexist. URLs are signed only after the API has checked access.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from functools import wraps
import fcntl
import hashlib
import json
import mimetypes
from pathlib import Path, PurePosixPath
import shutil
import threading
import time
from urllib.parse import quote, unquote, urlsplit
import uuid

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from .config import get_config

SIDECARS = (".source_map.json", ".transcript_cache.json")
PART_BYTES = 64 * 1024 * 1024
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
        leases = {"files": {}, "lock": threading.Lock()}
        token = _workspace.set(leases)
        try:
            return await method(*args, **kwargs)
        finally:
            _workspace.reset(token)
            await asyncio.to_thread(_release_workspace, leases)
    return wrapped


def _release_workspace(leases):
    for directory, handle in leases["files"].items():
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()
        # Other processes may still be using the same immutable source.
        with (directory / ".lease").open("a") as exclusive:
            try:
                fcntl.flock(exclusive, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            # Leave the lock inode in place so a waiting reader cannot acquire
            # an unlinked lock while another process opens a replacement.
            for path in directory.iterdir():
                if path.is_file() and path.name not in {".lease", ".download", ".reference", ".access"}:
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
        if not 0 < size <= self.config.max_video_upload_bytes or (expected and size != int(expected)):
            raise ValueError("Uploaded video has an invalid size")
        return reference

    def upload_file(self, path: Path, key: str, **extra) -> str:
        self.client.upload_file(str(path), self.bucket, key, ExtraArgs={
            "ContentType": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            **extra,
        })
        return self.reference(key)

    def persist_source(self, path: Path, user_id: str) -> tuple[str, str]:
        relative = f"{owner_key(user_id)}/{uuid.uuid4().hex}/video{path.suffix.lower()}"
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
            self.client.download_file(self.bucket, key, str(temporary))
            temporary.replace(path)
        except ClientError as exc:
            if not optional or exc.response["Error"]["Code"] not in {"404", "NoSuchKey", "NotFound"}:
                raise
        finally:
            temporary.unlink(missing_ok=True)

    def local_path(self, reference: str, *, metadata_only=False) -> Path:
        if not is_s3(reference):
            return Path(reference)
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
        # Download atomically and serialize cache population across processes.
        with (directory / ".download").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            path = directory / PurePosixPath(key).name
            (directory / ".reference").write_text(reference)
            (directory / ".access").touch()
            for suffix in SIDECARS:
                sidecar = path.with_suffix(suffix)
                if not sidecar.exists():
                    self._download(str(PurePosixPath(key).with_suffix(suffix)), sidecar, optional=True)
            if not metadata_only and not path.exists():
                self._download(key, path)
            return path

    def save_source_metadata(self, path: Path):
        marker = path.parent / ".reference"
        if not self.enabled or not marker.exists():
            return
        key = self.parse(marker.read_text())
        for suffix in SIDECARS:
            sidecar = path.with_suffix(suffix)
            if sidecar.exists():
                self.upload_file(sidecar, str(PurePosixPath(key).with_suffix(suffix)))

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

    def signed_url(self, reference: str, filename: str, *, attachment=False) -> str:
        # Filenames originate from UUID-based application output names.
        safe_name = Path(filename).name.replace('"', "").replace("\r", "").replace("\n", "")
        return self.client.generate_presigned_url("get_object", Params={
            "Bucket": self.bucket, "Key": self.parse(reference),
            "ResponseContentDisposition": f'{"attachment" if attachment else "inline"}; filename="{safe_name}"',
        }, ExpiresIn=self.config.media_url_ttl_seconds)

    def exists(self, reference: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self.parse(reference))
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def begin_upload(self, user_id: str, filename: str, size: int, content_type: str):
        if not 0 < size <= self.config.max_video_upload_bytes:
            raise ValueError("Uploaded file is too large or empty")
        extension = Path(filename).suffix.lower()
        if extension not in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}:
            raise ValueError("Unsupported video file extension")
        relative = f"{owner_key(user_id)}/{uuid.uuid4().hex}/video{extension}"
        key = self.key("sources", relative)
        upload = self.client.create_multipart_upload(Bucket=self.bucket, Key=key,
            ContentType=content_type or "application/octet-stream", Metadata={"expected-size": str(size)})
        upload_id = upload["UploadId"]
        try:
            parts = [{"partNumber": i + 1, "url": self.client.generate_presigned_url(
                "upload_part", Params={"Bucket": self.bucket, "Key": key,
                "UploadId": upload_id, "PartNumber": i + 1}, ExpiresIn=3600,
            )} for i in range((size + PART_BYTES - 1) // PART_BYTES)]
        except BaseException:
            self.client.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id)
            raise
        return {"storage": "s3", "video_path": f"upload://s3/{relative}",
                "uploadId": upload_id, "partSize": PART_BYTES, "parts": parts}

    def owned_upload_key(self, video_path: str, user_id: str) -> str:
        key = self.parse(self.source_reference(video_path))
        if not key.startswith(self.key("sources", owner_key(user_id)) + "/"):
            raise ValueError("Uploaded video does not belong to this user")
        return key

    def complete_upload(self, user_id: str, payload: dict):
        key = self.owned_upload_key(payload["video_path"], user_id)
        parts = payload["parts"]
        if (not isinstance(parts, list) or not parts or len(parts) > 10000
                or [p.get("partNumber") for p in parts] != list(range(1, len(parts) + 1))
                or any(not isinstance(p.get("etag"), str) for p in parts)):
            raise ValueError("Invalid multipart upload parts")
        self.client.complete_multipart_upload(Bucket=self.bucket, Key=key, UploadId=payload["uploadId"],
            MultipartUpload={"Parts": [{"PartNumber": p["partNumber"], "ETag": p["etag"]} for p in parts]})
        try:
            self.validate_source(payload["video_path"], user_id)
        except BaseException:
            self.client.delete_object(Bucket=self.bucket, Key=key)
            raise
        return {"video_path": payload["video_path"]}

    def abort_upload(self, user_id: str, payload: dict):
        self.client.abort_multipart_upload(Bucket=self.bucket,
            Key=self.owned_upload_key(payload["video_path"], user_id), UploadId=payload["uploadId"])

    def delete(self, reference: str, *, prefix=False):
        if is_s3(reference):
            key = self.parse(reference)
            if prefix:
                for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=key.rstrip("/") + "/"):
                    for obj in page.get("Contents", []):
                        self.client.delete_object(Bucket=self.bucket, Key=obj["Key"])
            else:
                object_keys = [key]
                if PurePosixPath(key).suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}:
                    object_keys.extend(str(PurePosixPath(key).with_suffix(s)) for s in SIDECARS)
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

    def save_editor(self, directory: Path):
        if not self.enabled:
            return
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / ".sync").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            manifest = directory / ".s3-manifest"
            stamps = json.loads(manifest.read_text()) if manifest.exists() else {}
            for path in directory.iterdir():
                if not path.is_file() or path.name.startswith(".") or path.suffix not in {".json", ".jpg", ".mp4"}:
                    continue
                stat = path.stat()
                stamp = [stat.st_size, stat.st_mtime_ns]
                if stamps.get(path.name) == stamp:
                    continue
                self.upload_file(path, self.parse(self.editor_reference(directory, path.name)))
                stamps[path.name] = stamp
                manifest.write_text(json.dumps(stamps))

    def restore_editor(self, directory: Path, *, media=False):
        if not self.enabled:
            return
        prefix = self.parse(self.editor_reference(directory)) + "/"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / ".access").touch()
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
                    if path.exists() or (path.suffix == ".mp4" and not media):
                        continue
                    self._download(obj["Key"], path)
                    stat = path.stat()
                    stamps[path.name] = [stat.st_size, stat.st_mtime_ns]
                    manifest.write_text(json.dumps(stamps))

    def sweep_cache(self):
        root = Path(self.config.temp_dir) / "media-cache"
        if not root.exists():
            return
        for directory in root.iterdir():
            access = directory / ".access"
            if not directory.is_dir() or (access.exists() and time.time() - access.stat().st_mtime < self.config.media_cache_ttl_seconds):
                continue
            _release_workspace({"files": {directory: (directory / ".lease").open("a")}})

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
            for name, stamp in json.loads(manifest.read_text()).items():
                path = directory / name
                if path.suffix == ".mp4" and path.exists():
                    stat = path.stat()
                    if [stat.st_size, stat.st_mtime_ns] == stamp:
                        path.unlink(missing_ok=True)
