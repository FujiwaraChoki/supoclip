"""Two independent object stores behind stable, private S3 references.

The durable outbox lives on the API/worker's shared media volume. Per-object
flocks serialize mutable editor snapshots, retries and deletions. A failed
replica never causes a successful upload to be discarded. Pending payloads
are outside all media-cache sweepers and are released only after both stores
acknowledge the exact revision. Credentials and signed URLs are never logged.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import time
import uuid
from urllib.request import Request, urlopen

from boto3.s3.transfer import TransferConfig
from boto3.exceptions import Boto3Error
from botocore.exceptions import BotoCoreError, ClientError

from .editor_document import atomic_json

logger = logging.getLogger(__name__)
ERRORS = (Boto3Error, BotoCoreError, ClientError, OSError)
TRANSFER = TransferConfig(max_concurrency=2, multipart_chunksize=64 * 1024 * 1024)
MISSING = {"404", "NoSuchKey", "NotFound"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ReplicatedMediaClient:
    """Implement the S3 operations used by MediaStorage, preserving its scope."""

    def __init__(self, primary, fallback, primary_bucket, fallback_bucket, root, *, failover=False):
        self.stores = {"primary": (primary, primary_bucket), "fallback": (fallback, fallback_bucket)}
        self.bucket = primary_bucket
        self.root = Path(root) / "media-replication"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.failover = failover

    def _scope(self, bucket):
        if bucket != self.bucket:
            raise ValueError("Replication request is outside the configured media bucket")

    def _directory(self, key):
        return self.root / hashlib.sha256(key.encode()).hexdigest()

    @contextmanager
    def _locked(self, key, *, blocking=True):
        directory = self._directory(key)
        directory.mkdir(exist_ok=True, mode=0o700)
        with (directory / ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            yield directory

    def _save(self, directory, manifest):
        temporary = directory / (uuid.uuid4().hex + ".manifest")
        try:
            with temporary.open("x") as stream:
                os.chmod(temporary, 0o600)
                json.dump(manifest, stream, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(directory / "pending.json")
        finally:
            temporary.unlink(missing_ok=True)
        # Also persist the directory entry, not only the payload/manifest data.
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _stage(self, directory, path, key, extra, *, pending=None, adopt=False):
        if adopt and (path.parent != directory or path.suffix != ".part"):
            raise ValueError("Only this object's private temporary download can be adopted")
        if not adopt and shutil.disk_usage(self.root).free < path.stat().st_size + 256 * 1024 * 1024:
            raise OSError("Insufficient disk space for durable media replication")
        temporary = path if adopt else directory / (uuid.uuid4().hex + ".part")
        try:
            if adopt:
                with temporary.open("rb") as stream:
                    os.fsync(stream.fileno())
            else:
                with path.open("rb") as source, temporary.open("wb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
                    target.flush()
                    os.fsync(target.fileno())
            digest = sha256_file(temporary)
            # A new immutable filename ensures a crash cannot pair an older
            # manifest with a newer payload or vice versa.
            payload = directory / (uuid.uuid4().hex + ".payload")
            temporary.replace(payload)
            manifest = {"operation": "upload", "key": key, "payload": payload.name,
                        "size": payload.stat().st_size, "sha256": digest,
                        "extra": extra, "pending": list(self.stores) if pending is None else list(pending),
                        "created_at": time.time()}
            self._save(directory, manifest)
            self._prune_payloads(directory, keep=payload.name)
            return manifest
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _prune_payloads(directory, *, keep=None):
        for path in directory.glob("*.payload"):
            if path.name != keep:
                path.unlink(missing_ok=True)

    def _apply(self, directory, manifest):
        successes = 0
        last_error = None
        for name in list(manifest["pending"]):
            client, bucket = self.stores[name]
            try:
                if manifest["operation"] == "delete":
                    client.delete_object(Bucket=bucket, Key=manifest["key"])
                else:
                    extra = dict(manifest.get("extra", {}))
                    extra["Metadata"] = {**extra.get("Metadata", {}), "supoclip-sha256": manifest["sha256"]}
                    client.upload_file(str(directory / manifest["payload"]), bucket, manifest["key"],
                                       ExtraArgs=extra, Config=TRANSFER)
                    head = client.head_object(Bucket=bucket, Key=manifest["key"])
                    if head["ContentLength"] != manifest["size"] or head.get("Metadata", {}).get("supoclip-sha256") != manifest["sha256"]:
                        raise OSError("Replica verification failed")
                manifest["pending"].remove(name)
                self._save(directory, manifest)
                successes += 1
            except ERRORS as error:
                last_error = error
                logger.warning("Media replica operation retained for retry: provider=%s operation=%s",
                               name, manifest["operation"])
        if not manifest["pending"]:
            (directory / "pending.json").unlink(missing_ok=True)
            self._prune_payloads(directory)
        return successes, last_error

    def upload_file(self, filename, bucket, key, ExtraArgs=None, **kwargs):
        self._scope(bucket)
        with self._locked(key) as directory:
            manifest = self._stage(directory, Path(filename), key, ExtraArgs or {})
            successes, error = self._apply(directory, manifest)
            if not successes:
                raise error or OSError("No media store accepted the upload")

    def _read(self, operation, *, Bucket, Key, **kwargs):
        self._scope(Bucket)
        error = None
        names = self._read_names(Key)
        for name in names:
            client, bucket = self.stores[name]
            try:
                return getattr(client, operation)(Bucket=bucket, Key=Key, **kwargs)
            except ERRORS as exc:
                # An unavailable store must not hide a real error by becoming
                # a false "object missing" when its replica is absent.
                missing = isinstance(exc, ClientError) and exc.response["Error"]["Code"] in MISSING
                if error is None or not missing:
                    error = exc
        raise error

    def _read_names(self, key):
        if not self.failover:
            return ["primary"]
        names = list(self.stores)
        try:
            health = json.loads((self.root / "health.json").read_text())
            if (0 <= time.time() - health["checked_at"] <= 180
                    and health["providers"]["primary"]["status"] == "unhealthy"
                    and health["providers"]["fallback"]["status"] == "healthy"):
                # Avoid a full AWS timeout on every playback during a known
                # outage. Independent probes keep testing recovery each minute.
                names.reverse()
        except (OSError, ValueError, KeyError, TypeError):
            pass
        try:
            manifest = json.loads((self._directory(key) / "pending.json").read_text())
            if manifest["operation"] == "upload":
                # Mutable drafts must prefer the store that acknowledged the
                # newest revision, even if AWS still permits reads of an old one.
                names.sort(key=lambda name: name in manifest["pending"])
            elif manifest["operation"] == "copy":
                names.sort(key=lambda name: name != manifest["source"])
        except FileNotFoundError:
            pass
        return names

    def head_object(self, **kwargs):
        return self._read("head_object", **kwargs)

    def get_object(self, **kwargs):
        return self._read("get_object", **kwargs)

    def download_file(self, bucket, key, filename, *, optional=False):
        self._scope(bucket)
        errors = []
        names = self._read_names(key)
        for name in names:
            client, target = self.stores[name]
            try:
                client.download_file(target, key, filename, Config=TRANSFER)
                return
            except ERRORS as exc:
                errors.append(exc)
        if optional:
            # A newly completed upload has no timing sidecars yet. When AWS is
            # down, an accessible R2 404 means optional metadata can be skipped.
            missing = next((e for e in errors if isinstance(e, ClientError) and e.response["Error"]["Code"] in MISSING), None)
            if missing is not None:
                raise missing
        raise next((e for e in errors if not isinstance(e, ClientError) or e.response["Error"]["Code"] not in MISSING), errors[0])

    def generate_presigned_url(self, ClientMethod, Params, **kwargs):
        params = dict(Params)
        self._scope(params["Bucket"])
        if ClientMethod == "upload_part":
            name, params["UploadId"] = self._upload_identity(params["UploadId"])
            client, params["Bucket"] = self.stores[name]
            return client.generate_presigned_url(ClientMethod, Params=params, **kwargs)
        if ClientMethod != "get_object" or not self.failover:
            return self.stores["primary"][0].generate_presigned_url(ClientMethod, Params=params, **kwargs)
        error = None
        for name in self._read_names(params["Key"]):
            client, bucket = self.stores[name]
            try:
                # Signing is local and succeeds even for a suspended account.
                # Probe access before handing the browser a storage URL.
                client.head_object(Bucket=bucket, Key=params["Key"])
                return client.generate_presigned_url(ClientMethod, Params={**params, "Bucket": bucket}, **kwargs)
            except ERRORS as exc:
                error = exc
        raise error

    @staticmethod
    def _upload_identity(upload_id):
        return ("fallback", upload_id[3:]) if upload_id.startswith("r2:") else ("primary", upload_id)

    def create_multipart_upload(self, **kwargs):
        self._scope(kwargs["Bucket"])
        error = None
        names = self._read_names(kwargs["Key"])
        for name in names:
            client, bucket = self.stores[name]
            try:
                result = client.create_multipart_upload(**{**kwargs, "Bucket": bucket})
                return {**result, "UploadId": ("r2:" if name == "fallback" else "") + result["UploadId"]}
            except ERRORS as exc:
                error = exc
        raise error

    def _multipart(self, operation, **kwargs):
        self._scope(kwargs["Bucket"])
        name, upload_id = self._upload_identity(kwargs["UploadId"])
        client, bucket = self.stores[name]
        return getattr(client, operation)(**{**kwargs, "Bucket": bucket, "UploadId": upload_id})

    def list_parts(self, **kwargs):
        return self._multipart("list_parts", **kwargs)

    def abort_multipart_upload(self, **kwargs):
        return self._multipart("abort_multipart_upload", **kwargs)

    def complete_multipart_upload(self, **kwargs):
        with self._locked(kwargs["Key"]) as directory:
            result = self._multipart("complete_multipart_upload", **kwargs)
            source, _ = self._upload_identity(kwargs["UploadId"])
            self._save(directory, {"operation": "copy", "key": kwargs["Key"], "source": source,
                                   "pending": [n for n in self.stores if n != source], "created_at": time.time()})
            return result

    def delete_object(self, *, Bucket, Key):
        self._scope(Bucket)
        with self._locked(Key) as directory:
            manifest = {"operation": "delete", "key": Key, "pending": list(self.stores), "created_at": time.time()}
            self._save(directory, manifest)
            self._prune_payloads(directory)
            _, error = self._apply(directory, manifest)
            if manifest["pending"]:
                # Retain the database deletion as well as the durable tombstone.
                raise error or OSError("Media deletion is pending")
            return {}

    def get_paginator(self, operation):
        if operation != "list_objects_v2":
            raise ValueError("Unsupported media paginator")
        parent = self

        class Paginator:
            def paginate(self, *, Bucket, Prefix, **kwargs):
                parent._scope(Bucket)
                merged = {}
                errors = []
                for client, bucket in parent.stores.values():
                    try:
                        for page in client.get_paginator(operation).paginate(Bucket=bucket, Prefix=Prefix, **kwargs):
                            for obj in page.get("Contents", []):
                                previous = merged.get(obj["Key"])
                                if previous is None or obj["LastModified"] > previous["LastModified"]:
                                    merged[obj["Key"]] = obj
                    except ERRORS as exc:
                        errors.append(exc)
                if len(errors) == len(parent.stores):
                    raise errors[0]
                # Bound each result page while preserving canonical object keys.
                values = list(merged.values())
                for start in range(0, len(values), 1000):
                    yield {"Contents": values[start:start + 1000]}
        return Paginator()

    def sync_pending(self, *, limit=100, max_seconds=45):
        # A timed-out asyncio.to_thread call keeps running. A process-shared
        # lease prevents the next cron invocation from starting another batch.
        with (self.root / ".sync.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return self.pending_status()
            return self._sync_pending(limit=limit, deadline=time.monotonic() + max_seconds)

    def _sync_pending(self, *, limit, deadline):
        done = 0
        for manifest_path in self.root.glob("*/pending.json"):
            if done >= limit or time.monotonic() >= deadline:
                break
            try:
                initial = json.loads(manifest_path.read_text())
                with self._locked(initial["key"], blocking=False) as directory:
                    if not manifest_path.exists():
                        continue
                    manifest = json.loads(manifest_path.read_text())
                    if manifest["operation"] == "copy":
                        client, bucket = self.stores[manifest["source"]]
                        head = client.head_object(Bucket=bucket, Key=manifest["key"])
                        temporary = directory / (uuid.uuid4().hex + ".part")
                        try:
                            if shutil.disk_usage(self.root).free < head["ContentLength"] + 256 * 1024 * 1024:
                                raise OSError("Insufficient replication disk space")
                            client.download_file(bucket, manifest["key"], str(temporary), Config=TRANSFER)
                            if temporary.stat().st_size != head["ContentLength"]:
                                raise OSError("Incomplete replication download")
                            extra = {k: head[k] for k in ("ContentType", "ContentDisposition", "CacheControl", "Metadata") if k in head}
                            manifest = self._stage(directory, temporary, manifest["key"], extra,
                                                   pending=manifest["pending"], adopt=True)
                        finally:
                            temporary.unlink(missing_ok=True)
                    self._apply(directory, manifest)
                    done += 1
            except (BlockingIOError, FileNotFoundError):
                continue
            except ERRORS:
                logger.warning("Media replication retry deferred")
        return self.pending_status()

    def pending_status(self):
        count = 0
        oldest = time.time()
        for path in self.root.glob("*/pending.json"):
            try:
                manifest = json.loads(path.read_text())
                count += 1
                oldest = min(oldest, manifest["created_at"])
            except FileNotFoundError:
                continue
        return {"pending": count, "oldest_pending_seconds": int(time.time() - oldest) if count else 0}

    def mirror_existing(self, key, *, verify=False):
        """Backfill one current object without rewriting the AWS original."""
        primary, source_bucket = self.stores["primary"]
        fallback, target_bucket = self.stores["fallback"]
        with self._locked(key) as directory:
            if (directory / "pending.json").exists():
                # A writer/deleter owns this revision. Its durable retry takes
                # priority over a bulk inventory that may already be stale.
                return "pending"
            try:
                source = primary.head_object(Bucket=source_bucket, Key=key)
            except ClientError as exc:
                if exc.response["Error"]["Code"] in MISSING:
                    return "removed"
                raise
            try:
                target = fallback.head_object(Bucket=target_bucket, Key=key)
            except ClientError as exc:
                if exc.response["Error"]["Code"] not in MISSING:
                    raise
                target = None
            digest = source.get("Metadata", {}).get("supoclip-sha256")
            same = target and target["ContentLength"] == source["ContentLength"] and (
                (digest and target.get("Metadata", {}).get("supoclip-sha256") == digest)
                or target.get("Metadata", {}).get("supoclip-source-etag") == source["ETag"]
            )
            if same:
                if verify:
                    if not self.replica_matches({"Key": key, **source}, {"Key": key, **target}):
                        self._verify_remote(fallback, target_bucket, key, target["Metadata"]["supoclip-sha256"])
                        self._record_verification(directory, key, source, target, target["Metadata"]["supoclip-sha256"])
                return "verified" if verify else "unchanged"
            temporary = directory / (uuid.uuid4().hex + ".part")
            try:
                if shutil.disk_usage(self.root).free < source["ContentLength"] + 256 * 1024 * 1024:
                    raise OSError("Insufficient disk space for media backfill")
                response = primary.get_object(Bucket=source_bucket, Key=key, IfMatch=source["ETag"])
                with response["Body"] as body, temporary.open("wb") as stream:
                    for chunk in iter(lambda: body.read(1024 * 1024), b""):
                        stream.write(chunk)
                if temporary.stat().st_size != source["ContentLength"]:
                    raise OSError("Incomplete source object")
                if primary.head_object(Bucket=source_bucket, Key=key)["ETag"] != source["ETag"]:
                    return "changed"
                extra = {k: source[k] for k in ("ContentType", "ContentDisposition", "CacheControl", "Metadata") if k in source}
                extra["Metadata"] = {**extra.get("Metadata", {}), "supoclip-source-etag": source["ETag"]}
                manifest = self._stage(directory, temporary, key, extra, pending=["fallback"], adopt=True)
                _, error = self._apply(directory, manifest)
                if manifest["pending"]:
                    raise error or OSError("Backfill upload is pending")
                if verify:
                    self._verify_remote(fallback, target_bucket, key, manifest["sha256"])
                    target = fallback.head_object(Bucket=target_bucket, Key=key)
                    self._record_verification(directory, key, source, target, manifest["sha256"])
                return "copied"
            finally:
                temporary.unlink(missing_ok=True)

    def _record_verification(self, directory, key, source, target, digest):
        atomic_json(directory / "verified.json", {"key": key, "source_etag": source["ETag"],
                    "target_etag": target["ETag"], "bytes": source["ContentLength"],
                    "sha256": digest, "verified_at": time.time()})

    def replica_matches(self, source, target):
        """A full checksum verification remains valid for these exact revisions."""
        if not target:
            return False
        try:
            record = json.loads((self._directory(source["Key"]) / "verified.json").read_text())
            return (record["source_etag"] == source["ETag"]
                    and record["target_etag"] == target["ETag"]
                    and record["bytes"] == source.get("Size", source.get("ContentLength"))
                    and record["bytes"] == target.get("Size", target.get("ContentLength")))
        except FileNotFoundError:
            return False

    @staticmethod
    def _verify_remote(client, bucket, key, expected):
        digest = hashlib.sha256()
        with client.get_object(Bucket=bucket, Key=key)["Body"] as body:
            for chunk in iter(lambda: body.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise OSError("Remote media checksum mismatch")

    def health_check(self, prefix):
        results = {}
        for name, (client, bucket) in self.stores.items():
            key = f"{prefix}/healthchecks/{uuid.uuid4().hex}.txt"
            body = uuid.uuid4().hex.encode()
            try:
                client.put_object(Bucket=bucket, Key=key, Body=body, ContentType="text/plain")
                with client.get_object(Bucket=bucket, Key=key)["Body"] as stream:
                    if stream.read() != body:
                        raise OSError("Storage read mismatch")
                url = client.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=60)
                with urlopen(Request(url, headers={"Range": "bytes=0-31"}), timeout=10) as response:
                    if response.read() != body:
                        raise OSError("Signed storage delivery mismatch")
                results[name] = {"status": "healthy"}
            except Exception as error:
                # Return only type/code, never URLs, bucket names or credentials.
                code = error.response["Error"]["Code"] if isinstance(error, ClientError) else type(error).__name__
                results[name] = {"status": "unhealthy", "error": code}
            finally:
                try:
                    client.delete_object(Bucket=bucket, Key=key)
                except ERRORS:
                    results[name] = {"status": "unhealthy", "error": "canary_cleanup_failed"}
        pending = self.pending_status()
        healthy = sum(r["status"] == "healthy" for r in results.values())
        status = "healthy" if healthy == 2 and not pending["pending"] else "degraded" if healthy else "unhealthy"
        report = {"status": status, "checked_at": time.time(), "providers": results, "replication": pending}
        atomic_json(self.root / "health.json", report)
        return report
