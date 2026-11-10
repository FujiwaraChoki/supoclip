"""Two independent object stores behind stable, private S3 references.

Hosts share PostgreSQL but no disk, so what is still owed to a store lives in
a PostgreSQL ledger (media_replication_ledger): one row per object key, replaced
by each newer write or delete. Any host can read it to route a request and any
host can drain it. A per-key advisory lock serializes overwrites, retries and
deletions across hosts, and a dead process releases its lock when its
connection drops.

Uploads go to both stores straight from the caller's file. A failed replica
never causes a successful upload to be discarded: the row names the store that
has not acknowledged the exact revision (size and SHA-256), and the drainer
copies it from the store that has. Local disk is only scratch for that copy.
Credentials and signed URLs are never logged.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import logging
from pathlib import Path
import shutil
import time
import uuid
from urllib.request import Request, urlopen

from boto3.s3.transfer import TransferConfig
from boto3.exceptions import Boto3Error
from botocore.exceptions import BotoCoreError, ClientError

from .editor_document import atomic_json
from .media_replication_ledger import LockBusy, PostgresLedger

logger = logging.getLogger(__name__)
ERRORS = (Boto3Error, BotoCoreError, ClientError, OSError)
TRANSFER = TransferConfig(max_concurrency=2, multipart_chunksize=64 * 1024 * 1024)
MISSING = {"404", "NoSuchKey", "NotFound"}
COPIED_HEADERS = ("ContentType", "ContentDisposition", "CacheControl", "Metadata")
HEADROOM = 256 * 1024 * 1024
_ledger_warning_at = float("-inf")


def fingerprint(path: Path) -> tuple[str, int]:
    """SHA-256 and byte count of a file, read once."""
    digest = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _missing(error) -> bool:
    return isinstance(error, ClientError) and error.response["Error"]["Code"] in MISSING


class ReplicatedMediaClient:
    """Implement the S3 operations used by MediaStorage, preserving its scope."""

    def __init__(self, primary, fallback, primary_bucket, fallback_bucket, root, *, failover=False, ledger=None):
        self.stores = {"primary": (primary, primary_bucket), "fallback": (fallback, fallback_bucket)}
        self.bucket = primary_bucket
        # Scratch for store-to-store copies and the backfill tool's records.
        # Nothing under it is needed by another host.
        self.root = Path(root) / "media-replication"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.failover = failover
        self._ledger = ledger

    @property
    def ledger(self):
        # Created on first use: constructing the client must not need the database.
        if self._ledger is None:
            self._ledger = PostgresLedger()
        return self._ledger

    def _scope(self, bucket):
        if bucket != self.bucket:
            raise ValueError("Replication request is outside the configured media bucket")

    def _directory(self, key):
        return self.root / hashlib.sha256(key.encode()).hexdigest()

    @contextmanager
    def _scratch(self):
        directory = self.root / "scratch"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = directory / (uuid.uuid4().hex + ".part")
        try:
            yield temporary
        finally:
            temporary.unlink(missing_ok=True)

    def _clean_scratch(self):
        # A pass that crashed mid-copy cannot run its own cleanup.
        cutoff = time.time() - 3600
        for path in (self.root / "scratch").glob("*"):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                continue

    def _require_space(self, size):
        if shutil.disk_usage(self.root).free < size + HEADROOM:
            raise OSError("Insufficient replication disk space")

    def _put(self, name, path, key, extra, digest, size):
        """Write one revision to one store and check that the store holds it."""
        client, bucket = self.stores[name]
        extra = dict(extra or {})
        extra["Metadata"] = {**extra.get("Metadata", {}), "supoclip-sha256": digest}
        client.upload_file(str(path), bucket, key, ExtraArgs=extra, Config=TRANSFER)
        head = client.head_object(Bucket=bucket, Key=key)
        if head["ContentLength"] != size or head.get("Metadata", {}).get("supoclip-sha256") != digest:
            raise OSError("Replica verification failed")

    def _holds(self, name, row):
        client, bucket = self.stores[name]
        try:
            head = client.head_object(Bucket=bucket, Key=row["object_key"])
        except ClientError as error:
            if _missing(error):
                return False
            raise
        return head["ContentLength"] == row["size"] and head.get("Metadata", {}).get("supoclip-sha256") == row["sha256"]

    def _acknowledge(self, row, name):
        try:
            self.ledger.confirm(row["object_key"], name, op_id=row["op_id"])
        except Exception as error:
            # The store has the revision. A row that still lists it only costs
            # the drainer a repeated copy or delete, so this must not fail the caller.
            logger.warning("Media replication acknowledgement not recorded: provider=%s error=%s",
                           name, type(error).__name__)

    def _replicate(self, row, path, names, *, sha256, size, extra):
        """Send one revision to each named store. Returns the stores still owed and the last error."""
        remaining, error = [], None
        for name in names:
            try:
                self._put(name, path, row["object_key"], extra, sha256, size)
            except ERRORS as exc:
                error = exc
                remaining.append(name)
                logger.warning("Media replica operation retained for retry: provider=%s operation=%s",
                               name, row["operation"])
                continue
            self._acknowledge(row, name)
        return remaining, error

    def _remove(self, row, names):
        remaining, error = [], None
        for name in names:
            client, bucket = self.stores[name]
            try:
                client.delete_object(Bucket=bucket, Key=row["object_key"])
            except ERRORS as exc:
                error = exc
                remaining.append(name)
                logger.warning("Media replica operation retained for retry: provider=%s operation=%s",
                               name, row["operation"])
                continue
            self._acknowledge(row, name)
        return remaining, error

    def upload_file(self, filename, bucket, key, ExtraArgs=None, **kwargs):
        self._scope(bucket)
        path = Path(filename)
        extra = dict(ExtraArgs or {})
        digest, size = fingerprint(path)
        with self.ledger.lock(key):
            previous = self.ledger.get(key)
            # Recorded before the first byte is sent, so a crash from here on
            # leaves a row the drainer can settle against what the stores hold.
            row = self.ledger.begin(key, "upload", pending=list(self.stores), sha256=digest, size=size, extra=extra)
            remaining, error = self._replicate(row, path, list(self.stores), sha256=digest, size=size, extra=extra)
            if len(remaining) == len(self.stores):
                # Nothing changed in either store: what was owed before is still owed.
                try:
                    if previous:
                        self.ledger.restore(previous)
                    else:
                        self.ledger.clear(key, op_id=row["op_id"])
                except Exception as failure:
                    logger.warning("Media replication ledger not restored: error=%s", type(failure).__name__)
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
                if error is None or not _missing(exc):
                    error = exc
        raise error

    def _read_names(self, key):
        if not self.failover:
            return ["primary"]
        names = list(self.stores)
        try:
            row, health = self.ledger.routing(key)
        except Exception as error:
            # A database blip must not take playback down with it.
            global _ledger_warning_at
            if time.monotonic() - _ledger_warning_at >= 60:
                _ledger_warning_at = time.monotonic()
                logger.warning("Media replication ledger unreadable, using the default store order: error=%s",
                               type(error).__name__)
            return names
        try:
            if (0 <= time.time() - health["checked_at"] <= 180
                    and health["providers"]["primary"]["status"] == "unhealthy"
                    and health["providers"]["fallback"]["status"] == "healthy"):
                # Avoid a full AWS timeout on every playback during a known
                # outage. Independent probes keep testing recovery each minute.
                names.reverse()
        except (KeyError, TypeError):
            pass
        if row and row["operation"] == "upload":
            # Mutable drafts must prefer the store that acknowledged the
            # newest revision, even if AWS still permits reads of an old one.
            names.sort(key=lambda name: name in row["pending"])
        elif row and row["operation"] == "copy":
            names.sort(key=lambda name: name != row["source_store"])
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
            missing = next((e for e in errors if _missing(e)), None)
            if missing is not None:
                raise missing
        raise next((e for e in errors if not _missing(e)), errors[0])

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
        self._scope(kwargs["Bucket"])
        with self.ledger.lock(kwargs["Key"]):
            result = self._multipart("complete_multipart_upload", **kwargs)
            source, _ = self._upload_identity(kwargs["UploadId"])
            # The browser sent the bytes to one store. The drainer copies them to the other.
            self.ledger.begin(kwargs["Key"], "copy", source=source,
                              pending=[name for name in self.stores if name != source])
            return result

    def delete_object(self, *, Bucket, Key):
        self._scope(Bucket)
        with self.ledger.lock(Key):
            # The tombstone replaces any upload still owed, so a retry cannot resurrect the object.
            row = self.ledger.begin(Key, "delete", pending=list(self.stores))
            remaining, error = self._remove(row, list(self.stores))
            if remaining:
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
        deadline = time.monotonic() + max_seconds
        try:
            # A timed-out asyncio.to_thread call keeps running, and any host may
            # run this cron. One lease across all of them keeps batches from overlapping.
            with self.ledger.drain_lock():
                self._clean_scratch()
                self.import_legacy_outbox(deadline=deadline)
                self._drain(limit, deadline)
        except LockBusy:
            pass
        return self.pending_status()

    def _drain(self, limit, deadline):
        for due in self.ledger.due(limit):
            if time.monotonic() >= deadline:
                break
            key = due["object_key"]
            try:
                with self.ledger.lock(key, blocking=False):
                    row = self.ledger.get(key)
                    if row is None or row["op_id"] != due["op_id"]:
                        # Settled or replaced by a newer write since the list was read.
                        continue
                    try:
                        settled = self._apply(row)
                    except ERRORS:
                        settled = False
                    if not settled:
                        self.ledger.defer(key, op_id=row["op_id"])
                        logger.warning("Media replication retry deferred")
            except LockBusy:
                # A writer or deleter is working on this key right now.
                continue

    def _apply(self, row):
        """Bring the stores in line with one row. True once nothing is owed."""
        if row["operation"] == "delete":
            return not self._remove(row, row["pending"])[0]
        if row["operation"] == "copy":
            return self._apply_copy(row)
        return self._apply_upload(row)

    def _apply_upload(self, row):
        key = row["object_key"]
        pending = list(row["pending"])
        sources = [name for name in self.stores if name not in pending]
        if not sources:
            # The writer died between recording the revision and its first
            # acknowledgement. The stores themselves say how far it got.
            unreachable = None
            for name in pending:
                try:
                    if self._holds(name, row):
                        sources.append(name)
                except ERRORS as error:
                    unreachable = error
            if not sources:
                if unreachable:
                    raise unreachable
                # Both stores answered and neither has it: the upload never happened.
                self.ledger.clear(key, op_id=row["op_id"])
                return True
            for name in sources:
                self._acknowledge(row, name)
                pending.remove(name)
            if not pending:
                return True
        client, bucket = self.stores[sources[0]]
        with self._scratch() as temporary:
            self._require_space(row["size"] or 0)
            try:
                client.download_file(bucket, key, str(temporary), Config=TRANSFER)
            except ClientError as error:
                if not _missing(error):
                    raise
                # The object was removed since. There is nothing left to copy.
                self.ledger.clear(key, op_id=row["op_id"])
                return True
            if fingerprint(temporary) != (row["sha256"], row["size"]):
                # The store that acknowledged this revision now holds another
                # one, written without a row of its own. Waiting cannot fix
                # that. Make the stores agree on what is actually there.
                logger.warning("Media replication source changed; copying its current revision")
                self.ledger.begin(key, "copy", source=sources[0], pending=pending)
                return False
            remaining, _ = self._replicate(row, temporary, pending, sha256=row["sha256"], size=row["size"],
                                           extra=row["extra"])
        return not remaining

    def _apply_copy(self, row):
        key = row["object_key"]
        client, bucket = self.stores[row["source_store"]]
        try:
            head = client.head_object(Bucket=bucket, Key=key)
        except ClientError as error:
            if not _missing(error):
                raise
            self.ledger.clear(key, op_id=row["op_id"])
            return True
        with self._scratch() as temporary:
            self._require_space(head["ContentLength"])
            client.download_file(bucket, key, str(temporary), Config=TRANSFER)
            digest, size = fingerprint(temporary)
            if size != head["ContentLength"]:
                raise OSError("Incomplete replication download")
            extra = {name: head[name] for name in COPIED_HEADERS if name in head}
            remaining, _ = self._replicate(row, temporary, row["pending"], sha256=digest, size=size, extra=extra)
        return not remaining

    def import_legacy_outbox(self, *, deadline=None):
        """Carry the earlier file outbox on this host's disk over to the ledger.

        Before the ledger, pending work was pending.json plus a payload file
        under root/<sha256(key)>/, visible only to this host. Each pass moves
        what is still there, removes the files once the ledger holds the work,
        and finds nothing to do afterwards. Never raises: an entry that cannot
        be moved yet stays for the next pass.
        """
        for manifest_path in self.root.glob("*/pending.json"):
            if deadline is not None and time.monotonic() >= deadline:
                break
            if manifest_path.parent.name == "scratch":
                continue
            try:
                self._import_legacy(manifest_path)
            except LockBusy:
                continue
            except Exception as error:
                logger.warning("Legacy media replication entry kept for the next pass: error=%s",
                               type(error).__name__)

    def _import_legacy(self, manifest_path):
        directory = manifest_path.parent
        try:
            manifest = json.loads(manifest_path.read_text())
        except FileNotFoundError:
            return
        key = manifest["key"]
        with self.ledger.lock(key, blocking=False):
            if not manifest_path.exists():
                return
            # A row means the key was written again through the ledger, which is newer.
            if self.ledger.get(key) is None and not self._adopt_legacy(directory, manifest):
                return
            manifest_path.unlink(missing_ok=True)
            for payload in directory.glob("*.payload"):
                payload.unlink(missing_ok=True)

    def _adopt_legacy(self, directory, manifest):
        """Put one legacy manifest into the ledger. False keeps its files for the next pass."""
        key = manifest["key"]
        pending = [name for name in self.stores if name in manifest["pending"]]
        if not pending:
            return True
        if manifest["operation"] == "delete":
            self.ledger.begin(key, "delete", pending=pending)
        elif manifest["operation"] == "copy":
            self.ledger.begin(key, "copy", source=manifest["source"], pending=pending)
        elif manifest["operation"] == "upload":
            revision = {"sha256": manifest["sha256"], "size": manifest["size"], "extra": manifest.get("extra") or {}}
            payload = directory / manifest["payload"]
            accepted = []
            if len(pending) == len(self.stores) and payload.is_file():
                # No store acknowledged this revision, so the payload here is its only copy.
                for name in pending:
                    try:
                        self._put(name, payload, key, revision["extra"], revision["sha256"], revision["size"])
                        accepted.append(name)
                    except ERRORS:
                        logger.warning("Media replica operation retained for retry: provider=%s operation=upload", name)
                if not accepted:
                    return False
            # With one store pending the other already has the revision, and the
            # drainer copies from it. With no payload left, the drainer checks the stores.
            row = self.ledger.begin(key, "upload", pending=pending, **revision)
            for name in accepted:
                self._acknowledge(row, name)
        else:
            raise ValueError("Unknown legacy replication operation")
        return True

    def pending_status(self):
        return self.ledger.status()

    def mirror_existing(self, key, *, verify=False):
        """Backfill one current object without rewriting the AWS original."""
        primary, source_bucket = self.stores["primary"]
        fallback, target_bucket = self.stores["fallback"]
        with self.ledger.lock(key):
            if self.ledger.get(key) is not None:
                # A writer/deleter owns this revision. Its durable retry takes
                # priority over a bulk inventory that may already be stale.
                return "pending"
            try:
                source = primary.head_object(Bucket=source_bucket, Key=key)
            except ClientError as exc:
                if _missing(exc):
                    return "removed"
                raise
            try:
                target = fallback.head_object(Bucket=target_bucket, Key=key)
            except ClientError as exc:
                if not _missing(exc):
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
                        self._record_verification(key, source, target, target["Metadata"]["supoclip-sha256"])
                return "verified" if verify else "unchanged"
            with self._scratch() as temporary:
                self._require_space(source["ContentLength"])
                response = primary.get_object(Bucket=source_bucket, Key=key, IfMatch=source["ETag"])
                with response["Body"] as body, temporary.open("wb") as stream:
                    for chunk in iter(lambda: body.read(1024 * 1024), b""):
                        stream.write(chunk)
                digest, size = fingerprint(temporary)
                if size != source["ContentLength"]:
                    raise OSError("Incomplete source object")
                if primary.head_object(Bucket=source_bucket, Key=key)["ETag"] != source["ETag"]:
                    return "changed"
                extra = {name: source[name] for name in COPIED_HEADERS if name in source}
                extra["Metadata"] = {**extra.get("Metadata", {}), "supoclip-source-etag": source["ETag"]}
                # Nothing is recorded in the ledger: a failure raises and the
                # operator's script, which is restartable, copies the object again.
                self._put("fallback", temporary, key, extra, digest, size)
                if verify:
                    self._verify_remote(fallback, target_bucket, key, digest)
                    target = fallback.head_object(Bucket=target_bucket, Key=key)
                    self._record_verification(key, source, target, digest)
                return "copied"

    def _record_verification(self, key, source, target, digest):
        # Stays on local disk: it only saves the backfill script a second full
        # read of an object it already checked, and can be recomputed anywhere.
        atomic_json(self._directory(key) / "verified.json", {"key": key, "source_etag": source["ETag"],
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
        # In the ledger, so every host routes reads and answers /health/storage from the same probe.
        self.ledger.write_health(report)
        return report
