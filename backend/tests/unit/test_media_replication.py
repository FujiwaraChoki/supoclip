"""Outage and recovery contracts; no real cloud or production database calls."""
from datetime import datetime, timezone
import fcntl
from io import BytesIO
import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from botocore.exceptions import ClientError
import pytest

from src.media_replication import ReplicatedMediaClient


class Body(BytesIO):
    def iter_chunks(self, chunk_size):
        while chunk := self.read(chunk_size):
            yield chunk


class Store:
    def __init__(self, name):
        self.name = name
        self.objects = {}
        self.metadata = {}
        self.failure = None
        self.uploads = 0
        self.multipart_calls = []

    def _check(self):
        if self.failure:
            raise ClientError({"Error": {"Code": self.failure}}, "TestOperation")

    def upload_file(self, filename, bucket, key, ExtraArgs=None, **kwargs):
        self._check()
        self.uploads += 1
        self.objects[key] = Path(filename).read_bytes()
        self.metadata[key] = ExtraArgs or {}

    def head_object(self, *, Bucket, Key):
        self._check()
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {"ContentLength": len(self.objects[Key]), "ETag": '"' + hashlib.md5(self.objects[Key]).hexdigest() + '"',
                "LastModified": datetime.now(timezone.utc), **self.metadata.get(Key, {})}

    def get_object(self, *, Bucket, Key, **kwargs):
        head = self.head_object(Bucket=Bucket, Key=Key)
        if kwargs.get("IfMatch") and kwargs["IfMatch"] != head["ETag"]:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "GetObject")
        return {**head, "Body": Body(self.objects[Key])}

    def download_file(self, bucket, key, filename, **kwargs):
        self.head_object(Bucket=bucket, Key=key)
        Path(filename).write_bytes(self.objects[key])

    def delete_object(self, *, Bucket, Key):
        self._check()
        self.objects.pop(Key, None)

    def put_object(self, *, Bucket, Key, Body, **kwargs):
        self._check()
        self.objects[Key] = Body
        self.metadata[Key] = kwargs

    def generate_presigned_url(self, method, Params, **kwargs):
        return f"https://{self.name}.example/{Params['Bucket']}/{Params['Key']}"

    def get_paginator(self, name):
        def pages(**kwargs):
            self._check()
            yield {"Contents": [{"Key": k, "Size": len(v), "LastModified": datetime.now(timezone.utc)}
                                for k, v in self.objects.items() if k.startswith(kwargs["Prefix"])]}
        return SimpleNamespace(paginate=pages)

    def create_multipart_upload(self, **kwargs):
        self._check()
        return {"UploadId": "upload-1"}

    def list_parts(self, **kwargs):
        self._check()
        self.multipart_calls.append(kwargs)
        return {"Parts": []}

    def complete_multipart_upload(self, **kwargs):
        self._check()
        self.multipart_calls.append(kwargs)
        self.objects[kwargs["Key"]] = b"browser-upload"
        self.metadata[kwargs["Key"]] = {"Metadata": {"expected-size": "14"}}
        return {"ETag": "etag"}

    def abort_multipart_upload(self, **kwargs):
        self._check()
        self.multipart_calls.append(kwargs)


@pytest.fixture
def mirrored(tmp_path):
    return ReplicatedMediaClient(Store("aws"), Store("r2"), "original", "replica", tmp_path, failover=True)


def stores(client):
    return client.stores["primary"][0], client.stores["fallback"][0]


def upload(client, tmp_path, body=b"video"):
    path = tmp_path / "clip.mp4"
    path.write_bytes(body)
    client.upload_file(str(path), "original", "media/clips/clip.mp4", ExtraArgs={"ContentType": "video/mp4"})


@pytest.mark.parametrize("failed", ["primary", "fallback"])
def test_one_store_outage_does_not_fail_render_and_retries_survive_restart(mirrored, tmp_path, failed):
    mirrored.stores[failed][0].failure = "InvalidAccessKeyId"
    upload(mirrored, tmp_path)
    assert mirrored.pending_status()["pending"] == 1
    payloads = list(mirrored.root.glob("*/*.payload"))
    assert len(payloads) == 1 and payloads[0].read_bytes() == b"video"
    mirrored.stores[failed][0].failure = None
    # Simulate a brand-new worker process sharing only the durable volume.
    restarted = ReplicatedMediaClient(*stores(mirrored), "original", "replica", tmp_path, failover=True)
    assert restarted.sync_pending()["pending"] == 0
    primary, fallback = stores(restarted)
    assert primary.objects == fallback.objects == {"media/clips/clip.mp4": b"video"}
    assert not list(restarted.root.glob("*/*.payload"))


def test_both_stores_down_retain_completed_output(mirrored, tmp_path):
    for store in stores(mirrored):
        store.failure = "ServiceUnavailable"
    with pytest.raises(ClientError):
        upload(mirrored, tmp_path)
    assert list(mirrored.root.glob("*/*.payload"))[0].read_bytes() == b"video"
    for store in stores(mirrored):
        store.failure = None
    assert mirrored.sync_pending()["pending"] == 0


def test_replication_batches_do_not_overlap_across_workers(mirrored, tmp_path):
    primary, fallback = stores(mirrored)
    fallback.failure = "ServiceUnavailable"
    upload(mirrored, tmp_path)
    fallback.failure = None
    with (mirrored.root / ".sync.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert mirrored.sync_pending()["pending"] == 1
        assert not fallback.objects
    assert mirrored.sync_pending()["pending"] == 0
    assert primary.objects == fallback.objects


def test_replication_stops_starting_objects_after_its_time_budget(mirrored, tmp_path):
    fallback = stores(mirrored)[1]
    fallback.failure = "ServiceUnavailable"
    upload(mirrored, tmp_path)
    fallback.failure = None
    assert mirrored.sync_pending(max_seconds=0)["pending"] == 1
    assert not fallback.objects
    assert mirrored.sync_pending()["pending"] == 0


def test_backfill_adopts_its_download_without_allocating_a_second_video(mirrored, monkeypatch):
    from src import media_replication
    primary, fallback = stores(mirrored)
    primary.objects["media/large.mp4"] = b"complete-video"
    # Copying an upload still needs a durable second copy, but backfill owns
    # its downloaded file already and must not double peak disk consumption.
    monkeypatch.setattr(media_replication.shutil, "copyfileobj", Mock(side_effect=AssertionError("duplicate allocation")))
    assert mirrored.mirror_existing("media/large.mp4", verify=True) == "copied"
    assert primary.objects == fallback.objects
    assert not list(mirrored.root.glob("*/*.part"))


def test_older_pending_revision_cannot_overwrite_new_editor_snapshot(mirrored, tmp_path):
    primary, fallback = stores(mirrored)
    fallback.failure = "ServiceUnavailable"
    upload(mirrored, tmp_path, b"old")
    upload(mirrored, tmp_path, b"new")
    assert len(list(mirrored.root.glob("*/*.payload"))) == 1
    fallback.failure = None
    mirrored.sync_pending()
    assert primary.objects == fallback.objects == {"media/clips/clip.mp4": b"new"}


def test_reads_prefer_latest_revision_when_primary_writes_fail_but_reads_work(mirrored, tmp_path):
    upload(mirrored, tmp_path, b"old")
    primary, fallback = stores(mirrored)
    primary.upload_file = Mock(side_effect=ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject"))
    upload(mirrored, tmp_path, b"new-version")
    restored = tmp_path / "restored.mp4"
    mirrored.download_file("original", "media/clips/clip.mp4", str(restored))
    assert restored.read_bytes() == b"new-version"
    assert "r2.example" in mirrored.generate_presigned_url("get_object", Params={"Bucket": "original", "Key": "media/clips/clip.mp4"})


def test_delete_tombstone_supersedes_pending_upload_without_resurrection(mirrored, tmp_path):
    primary, fallback = stores(mirrored)
    fallback.failure = "ServiceUnavailable"
    upload(mirrored, tmp_path)
    with pytest.raises(ClientError):
        mirrored.delete_object(Bucket="original", Key="media/clips/clip.mp4")
    assert not list(mirrored.root.glob("*/*.payload"))
    fallback.failure = None
    mirrored.sync_pending()
    assert primary.objects == fallback.objects == {}


@pytest.mark.parametrize("code", ["InvalidAccessKeyId", "AccessDenied", "ServiceUnavailable", "404"])
def test_playback_selects_r2_when_aws_is_unavailable(mirrored, tmp_path, code):
    upload(mirrored, tmp_path)
    stores(mirrored)[0].failure = code
    url = mirrored.generate_presigned_url("get_object", Params={"Bucket": "original", "Key": "media/clips/clip.mp4"}, ExpiresIn=60)
    assert url.startswith("https://r2.example/replica/")
    target = tmp_path / "restored.mp4"
    mirrored.download_file("original", "media/clips/clip.mp4", str(target))
    assert target.read_bytes() == b"video"


def test_primary_remains_preferred_after_recovery(mirrored, tmp_path):
    upload(mirrored, tmp_path)
    primary, fallback = stores(mirrored)
    primary.failure = "AccessDenied"
    params = {"Bucket": "original", "Key": "media/clips/clip.mp4"}
    assert "r2.example" in mirrored.generate_presigned_url("get_object", Params=params)
    primary.failure = None
    assert "aws.example" in mirrored.generate_presigned_url("get_object", Params=params)
    assert params["Bucket"] == "original"


def test_recent_outage_probe_skips_primary_timeouts_and_recovers(mirrored, tmp_path):
    upload(mirrored, tmp_path)
    primary, fallback = stores(mirrored)
    health = {"checked_at": time.time(), "providers": {
        "primary": {"status": "unhealthy"}, "fallback": {"status": "healthy"}}}
    (mirrored.root / "health.json").write_text(json.dumps(health))
    primary.head_object = Mock(side_effect=AssertionError("Repeated primary timeout"))
    params = {"Bucket": "original", "Key": "media/clips/clip.mp4"}
    assert "r2.example" in mirrored.generate_presigned_url("get_object", Params=params)
    assert mirrored.create_multipart_upload(**params)["UploadId"].startswith("r2:")
    primary.head_object = Mock(return_value={"ContentLength": 5})
    health["providers"]["primary"]["status"] = "healthy"
    (mirrored.root / "health.json").write_text(json.dumps(health))
    assert "aws.example" in mirrored.generate_presigned_url("get_object", Params=params)


def test_stale_outage_probe_does_not_hide_primary_recovery(mirrored, tmp_path):
    upload(mirrored, tmp_path)
    (mirrored.root / "health.json").write_text(json.dumps({"checked_at": time.time() - 181,
        "providers": {"primary": {"status": "unhealthy"}, "fallback": {"status": "healthy"}}}))
    params = {"Bucket": "original", "Key": "media/clips/clip.mp4"}
    assert "aws.example" in mirrored.generate_presigned_url("get_object", Params=params)


def test_newest_draft_takes_priority_over_cached_provider_health(mirrored, tmp_path):
    primary, fallback = stores(mirrored)
    fallback.failure = "ServiceUnavailable"
    upload(mirrored, tmp_path, b"newest-version")
    fallback.failure = None
    fallback.objects["media/clips/clip.mp4"] = b"old-version"
    (mirrored.root / "health.json").write_text(json.dumps({"checked_at": time.time(),
        "providers": {"primary": {"status": "unhealthy"}, "fallback": {"status": "healthy"}}}))
    target = tmp_path / "restored.mp4"
    mirrored.download_file("original", "media/clips/clip.mp4", str(target))
    assert target.read_bytes() == b"newest-version"


def test_a_missing_replica_does_not_hide_aws_credential_failure(mirrored):
    stores(mirrored)[0].failure = "InvalidAccessKeyId"
    with pytest.raises(ClientError, match="InvalidAccessKeyId"):
        mirrored.head_object(Bucket="original", Key="media/missing.mp4")


def test_fallback_uploaded_video_can_be_materialized_before_it_has_sidecars(mirrored, tmp_path):
    from src.config import Config
    from src.media_storage import MediaStorage, owner_key
    config = Config()
    config.media_storage = "s3"
    config.media_s3_bucket = "original"
    config.temp_dir = str(tmp_path)
    key = f"media/sources/{owner_key('owner')}/upload/video.mp4"
    primary, fallback = stores(mirrored)
    primary.failure = "InvalidAccessKeyId"
    fallback.objects[key] = b"new-video-without-transcript"
    storage = MediaStorage(config, mirrored)
    restored = storage.local_path(storage.reference(key))
    assert restored.read_bytes() == b"new-video-without-transcript"


def test_fallback_can_be_disabled_during_staged_rollout(mirrored, tmp_path):
    upload(mirrored, tmp_path)
    mirrored.failover = False
    stores(mirrored)[0].failure = "AccessDenied"
    with pytest.raises(ClientError):
        mirrored.head_object(Bucket="original", Key="media/clips/clip.mp4")


def test_bucket_scope_is_preserved_before_network_access(mirrored):
    for method in [mirrored.head_object, mirrored.delete_object]:
        with pytest.raises(ValueError, match="outside"):
            method(Bucket="someone-else", Key="media/clips/clip.mp4")


def test_multipart_upload_stays_on_selected_provider_and_queues_mirror(mirrored):
    primary, fallback = stores(mirrored)
    primary.failure = "InvalidAccessKeyId"
    result = mirrored.create_multipart_upload(Bucket="original", Key="media/sources/video.mp4")
    assert result["UploadId"] == "r2:upload-1"
    params = {"Bucket": "original", "Key": "media/sources/video.mp4", "UploadId": result["UploadId"]}
    assert "r2.example/replica/" in mirrored.generate_presigned_url("upload_part", Params=params)
    mirrored.list_parts(**params)
    assert fallback.multipart_calls[-1]["UploadId"] == "upload-1"
    assert fallback.multipart_calls[-1]["Bucket"] == "replica"
    mirrored.complete_multipart_upload(**params)
    assert mirrored.pending_status()["pending"] == 1
    primary.failure = None
    mirrored.sync_pending()
    assert primary.objects == fallback.objects == {"media/sources/video.mp4": b"browser-upload"}
    assert primary.metadata["media/sources/video.mp4"]["Metadata"]["expected-size"] == "14"


def test_existing_objects_are_copied_verified_and_never_rewrite_primary(mirrored):
    primary, fallback = stores(mirrored)
    key = "media/clips/historical.mp4"
    primary.objects[key] = b"historical-video"
    saved = []
    original_save = mirrored._save
    def save(directory, manifest):
        saved.append(json.loads(json.dumps(manifest)))
        original_save(directory, manifest)
    mirrored._save = save
    assert mirrored.mirror_existing(key, verify=True) == "copied"
    # Every crash-recoverable manifest must target only the replica, including
    # the first one written before any network call or verification.
    assert all("primary" not in manifest["pending"] for manifest in saved)
    assert primary.uploads == 0
    assert fallback.objects == primary.objects
    assert mirrored.mirror_existing(key, verify=True) == "verified"
    assert fallback.uploads == 1


def test_backfill_updates_changed_mutable_objects(mirrored):
    primary, fallback = stores(mirrored)
    key = "media/editor/draft.json"
    primary.objects[key] = b"old-draft"
    mirrored.mirror_existing(key, verify=True)
    primary.objects[key] = b"new-draft"
    assert mirrored.mirror_existing(key, verify=True) == "copied"
    assert fallback.objects[key] == b"new-draft"


def test_inventory_includes_objects_written_only_to_r2_during_aws_outage(mirrored):
    primary, fallback = stores(mirrored)
    primary.objects["media/old.mp4"] = b"old"
    fallback.objects["media/new.mp4"] = b"new"
    pages = mirrored.get_paginator("list_objects_v2").paginate(Bucket="original", Prefix="media/")
    assert {obj["Key"] for page in pages for obj in page["Contents"]} == {"media/old.mp4", "media/new.mp4"}


def test_health_probes_signed_delivery_and_reports_partial_failure(mirrored, monkeypatch):
    from src import media_replication
    primary, fallback = stores(mirrored)
    # Return the exact random body uploaded by the health probe.
    def fetch(request, timeout):
        store = primary if "aws.example" in request.full_url else fallback
        key = request.full_url.split("/", 4)[4]
        return Body(store.objects[key])
    monkeypatch.setattr(media_replication, "urlopen", fetch)
    report = mirrored.health_check("media")
    assert report["status"] == "healthy"
    assert primary.objects == fallback.objects == {}
    primary.failure = "InvalidAccessKeyId"
    report = mirrored.health_check("media")
    assert report["status"] == "degraded"
    assert report["providers"]["fallback"]["status"] == "healthy"
    assert "credentials" not in json.dumps(report)
