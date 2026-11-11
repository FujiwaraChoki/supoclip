"""Outage and recovery contracts; no real cloud or production database calls."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
import hashlib
import json
import logging
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from botocore.exceptions import ClientError
import pytest

from src.media_replication import ReplicatedMediaClient
from src.media_replication_ledger import LockBusy, MemoryLedger

KEY = "media/clips/clip.mp4"


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
    return ReplicatedMediaClient(Store("aws"), Store("r2"), "original", "replica", tmp_path,
                                 failover=True, ledger=MemoryLedger())


def stores(client):
    return client.stores["primary"][0], client.stores["fallback"][0]


def upload(client, tmp_path, body=b"video"):
    path = tmp_path / "clip.mp4"
    path.write_bytes(body)
    client.upload_file(str(path), "original", KEY, ExtraArgs={"ContentType": "video/mp4"})


def sha(body):
    return hashlib.sha256(body).hexdigest()


def another_host(client, tmp_path):
    """A worker elsewhere: the same stores and ledger, none of this host's disk."""
    return ReplicatedMediaClient(*stores(client), "original", "replica", tmp_path / "another-host",
                                 failover=True, ledger=client.ledger)


def scratch_files(client):
    return [path for path in (client.root / "scratch").glob("*")]


def hold(store, body, key=KEY):
    """Put a revision in a store the way a verified upload leaves it."""
    store.objects[key] = body
    store.metadata[key] = {"ContentType": "video/mp4", "Metadata": {"supoclip-sha256": sha(body)}}


def crashed_upload(client, body=b"video"):
    """A writer that recorded its revision and died before any store acknowledged it."""
    return client.ledger.begin(KEY, "upload", pending=["primary", "fallback"], sha256=sha(body),
                               size=len(body), extra={"ContentType": "video/mp4"})


def make_due(client, key=KEY):
    client.ledger.restore({**client.ledger.get(key), "next_attempt_at": datetime.now(timezone.utc)})


def legacy_entry(client, key, manifest, payload=None):
    """Files the earlier disk outbox left behind for one object."""
    directory = client.root / hashlib.sha256(key.encode()).hexdigest()
    directory.mkdir()
    (directory / ".lock").touch()
    if payload is not None:
        (directory / "staged.payload").write_bytes(payload)
        manifest = {**manifest, "payload": "staged.payload", "size": len(payload), "sha256": sha(payload),
                    "extra": {"ContentType": "video/mp4"}}
    (directory / "pending.json").write_text(json.dumps({"key": key, "created_at": time.time(), **manifest}))
    return directory


@pytest.mark.parametrize("failed", ["primary", "fallback"])
def test_one_store_outage_does_not_fail_render_and_retries_survive_restart(mirrored, tmp_path, failed):
    mirrored.stores[failed][0].failure = "InvalidAccessKeyId"
    upload(mirrored, tmp_path)
    assert mirrored.pending_status()["pending"] == 1
    row = mirrored.ledger.get(KEY)
    assert (row["operation"], row["pending"], row["sha256"], row["size"]) == ("upload", [failed], sha(b"video"), 5)
    # What is owed is a ledger row. No copy of the video stays on this host.
    assert [path.name for path in mirrored.root.iterdir()] in ([], ["scratch"])
    mirrored.stores[failed][0].failure = None
    # Simulate a brand-new worker on another host sharing only the ledger and the stores.
    restarted = another_host(mirrored, tmp_path)
    assert restarted.sync_pending()["pending"] == 0
    primary, fallback = stores(restarted)
    assert primary.objects == fallback.objects == {KEY: b"video"}
    assert primary.metadata[KEY] == fallback.metadata[KEY]
    assert fallback.metadata[KEY]["ContentType"] == "video/mp4"
    assert not scratch_files(restarted)


def test_both_stores_down_fail_the_upload_and_leave_nothing_owed(mirrored, tmp_path):
    for store in stores(mirrored):
        store.failure = "ServiceUnavailable"
    with pytest.raises(ClientError):
        upload(mirrored, tmp_path)
    # The caller was told and keeps its file. Nothing may arrive in a store later.
    assert mirrored.ledger.get(KEY) is None
    for store in stores(mirrored):
        store.failure = None
    assert mirrored.sync_pending()["pending"] == 0
    assert all(not store.objects for store in stores(mirrored))


def test_both_stores_down_restore_the_revision_that_was_still_owed(mirrored, tmp_path):
    primary, fallback = stores(mirrored)
    fallback.failure = "ServiceUnavailable"
    upload(mirrored, tmp_path, b"old")
    owed = mirrored.ledger.get(KEY)
    primary.failure = "ServiceUnavailable"
    with pytest.raises(ClientError):
        upload(mirrored, tmp_path, b"new")
    assert mirrored.ledger.get(KEY) == owed
    primary.failure = fallback.failure = None
    assert mirrored.sync_pending()["pending"] == 0
    assert primary.objects == fallback.objects == {KEY: b"old"}


def test_unverified_store_does_not_count_as_accepted(mirrored, tmp_path):
    primary, fallback = stores(mirrored)
    # The store took the request but does not hold what was sent.
    fallback.upload_file = lambda filename, bucket, key, **kwargs: hold(fallback, b"truncated", key)
    upload(mirrored, tmp_path)
    assert mirrored.ledger.get(KEY)["pending"] == ["fallback"]
    del fallback.upload_file
    assert mirrored.sync_pending()["pending"] == 0
    assert primary.objects == fallback.objects == {KEY: b"video"}


def test_replication_batches_do_not_overlap_across_workers(mirrored, tmp_path):
    primary, fallback = stores(mirrored)
    fallback.failure = "ServiceUnavailable"
    upload(mirrored, tmp_path)
    fallback.failure = None
    with mirrored.ledger.drain_lock():
        assert mirrored.sync_pending()["pending"] == 1
        assert not fallback.objects
    assert mirrored.sync_pending()["pending"] == 0
    assert primary.objects == fallback.objects


def test_second_drainer_returns_at_once_while_the_first_is_still_copying(mirrored, tmp_path):
    primary, fallback = stores(mirrored)
    fallback.failure = "ServiceUnavailable"
    upload(mirrored, tmp_path)
    fallback.failure = None
    started, finish = threading.Event(), threading.Event()
    real_upload = fallback.upload_file

    def slow_upload(*args, **kwargs):
        started.set()
        assert finish.wait(10)
        return real_upload(*args, **kwargs)

    fallback.upload_file = slow_upload
    first = threading.Thread(target=mirrored.sync_pending)
    first.start()
    try:
        assert started.wait(10)
        assert another_host(mirrored, tmp_path).sync_pending()["pending"] == 1
        assert not fallback.objects
    finally:
        finish.set()
        first.join(10)
    assert mirrored.pending_status()["pending"] == 0
    assert primary.objects == fallback.objects


def test_drainer_leaves_a_key_alone_while_its_writer_holds_the_lock(mirrored, tmp_path):
    crashed_upload(mirrored)
    with mirrored.ledger.lock(KEY):
        # Not a crash yet: the writer is still uploading and must not lose its row.
        assert mirrored.sync_pending()["pending"] == 1
    assert mirrored.ledger.get(KEY)["attempts"] == 0


def test_replication_stops_starting_objects_after_its_time_budget(mirrored, tmp_path):
    fallback = stores(mirrored)[1]
    fallback.failure = "ServiceUnavailable"
    upload(mirrored, tmp_path)
    fallback.failure = None
    assert mirrored.sync_pending(max_seconds=0)["pending"] == 1
    assert not fallback.objects
    assert mirrored.sync_pending()["pending"] == 0


def test_failed_retry_backs_off_and_resumes_when_due(mirrored, tmp_path):
    primary, fallback = stores(mirrored)
    fallback.failure = "ServiceUnavailable"
    upload(mirrored, tmp_path)
    assert mirrored.sync_pending()["pending"] == 1
    row = mirrored.ledger.get(KEY)
    assert row["attempts"] == 1 and row["pending"] == ["fallback"]
    assert row["next_attempt_at"] - datetime.now(timezone.utc) > timedelta(seconds=50)
    fallback.failure = None
    # Still waiting out its backoff.
    assert mirrored.sync_pending()["pending"] == 1 and not fallback.objects
    make_due(mirrored)
    assert mirrored.sync_pending()["pending"] == 0
    assert primary.objects == fallback.objects


def test_backfill_adopts_its_download_without_allocating_a_second_video(mirrored, monkeypatch):
    from src import media_replication
    primary, fallback = stores(mirrored)
    primary.objects["media/large.mp4"] = b"complete-video"
    # Backfill owns the file it downloaded and must not double peak disk consumption.
    monkeypatch.setattr(media_replication.shutil, "copyfileobj", Mock(side_effect=AssertionError("duplicate allocation")))
    assert mirrored.mirror_existing("media/large.mp4", verify=True) == "copied"
    assert primary.objects == fallback.objects
    assert not scratch_files(mirrored)


def test_older_pending_revision_cannot_overwrite_new_editor_snapshot(mirrored, tmp_path):
    primary, fallback = stores(mirrored)
    fallback.failure = "ServiceUnavailable"
    upload(mirrored, tmp_path, b"old")
    upload(mirrored, tmp_path, b"new")
    assert mirrored.pending_status()["pending"] == 1
    assert mirrored.ledger.get(KEY)["sha256"] == sha(b"new")
    fallback.failure = None
    mirrored.sync_pending()
    assert primary.objects == fallback.objects == {KEY: b"new"}


def test_reads_prefer_latest_revision_when_primary_writes_fail_but_reads_work(mirrored, tmp_path):
    upload(mirrored, tmp_path, b"old")
    primary, fallback = stores(mirrored)
    primary.upload_file = Mock(side_effect=ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject"))
    upload(mirrored, tmp_path, b"new-version")
    restored = tmp_path / "restored.mp4"
    # Any host sees the same routing, not only the one that took the upload.
    another_host(mirrored, tmp_path).download_file("original", KEY, str(restored))
    assert restored.read_bytes() == b"new-version"
    assert "r2.example" in mirrored.generate_presigned_url("get_object", Params={"Bucket": "original", "Key": KEY})


def test_delete_tombstone_supersedes_pending_upload_without_resurrection(mirrored, tmp_path):
    primary, fallback = stores(mirrored)
    fallback.failure = "ServiceUnavailable"
    upload(mirrored, tmp_path)
    with pytest.raises(ClientError):
        mirrored.delete_object(Bucket="original", Key=KEY)
    row = mirrored.ledger.get(KEY)
    assert (row["operation"], row["pending"]) == ("delete", ["fallback"])
    fallback.failure = None
    # An object left behind in the replica must be removed, not copied back.
    hold(fallback, b"video")
    mirrored.sync_pending()
    assert primary.objects == fallback.objects == {}
    assert mirrored.pending_status()["pending"] == 0


def test_delete_on_both_stores_leaves_nothing_owed(mirrored, tmp_path):
    upload(mirrored, tmp_path)
    assert mirrored.delete_object(Bucket="original", Key=KEY) == {}
    assert mirrored.ledger.get(KEY) is None
    assert all(not store.objects for store in stores(mirrored))


@pytest.mark.parametrize("holder", ["primary", "fallback"])
def test_writer_crash_before_acknowledgement_is_settled_from_the_store_that_has_it(mirrored, holder):
    crashed_upload(mirrored)
    hold(mirrored.stores[holder][0], b"video")
    assert mirrored.sync_pending()["pending"] == 0
    primary, fallback = stores(mirrored)
    assert primary.objects == fallback.objects == {KEY: b"video"}
    assert primary.metadata[KEY]["ContentType"] == fallback.metadata[KEY]["ContentType"] == "video/mp4"


def test_writer_crash_before_any_store_received_the_revision_clears_the_row(mirrored):
    primary, fallback = stores(mirrored)
    hold(fallback, b"an-earlier-revision")
    crashed_upload(mirrored)
    assert mirrored.sync_pending()["pending"] == 0
    # The upload never happened, so there is nothing to copy in either direction.
    assert not primary.objects and fallback.objects == {KEY: b"an-earlier-revision"}
    assert primary.uploads == fallback.uploads == 0


def test_writer_crash_is_not_judged_while_a_store_cannot_be_checked(mirrored):
    primary, fallback = stores(mirrored)
    crashed_upload(mirrored)
    primary.failure = "ServiceUnavailable"
    assert mirrored.sync_pending()["pending"] == 1
    row = mirrored.ledger.get(KEY)
    assert row["pending"] == ["primary", "fallback"] and row["attempts"] == 1
    # The unreachable store was the one holding it.
    primary.failure = None
    hold(primary, b"video")
    make_due(mirrored)
    assert mirrored.sync_pending()["pending"] == 0
    assert fallback.objects == {KEY: b"video"}


def test_pending_copy_is_dropped_when_its_source_object_is_gone(mirrored):
    mirrored.ledger.begin(KEY, "copy", source="fallback", pending=["primary"])
    assert mirrored.sync_pending()["pending"] == 0
    assert all(not store.objects for store in stores(mirrored))


def test_pending_upload_is_dropped_when_its_source_object_is_gone(mirrored, tmp_path):
    primary, fallback = stores(mirrored)
    fallback.failure = "ServiceUnavailable"
    upload(mirrored, tmp_path)
    fallback.failure = None
    del primary.objects[KEY]
    assert mirrored.sync_pending()["pending"] == 0
    assert not fallback.objects


def test_changed_source_is_never_copied_as_the_recorded_revision(mirrored, tmp_path):
    primary, fallback = stores(mirrored)
    fallback.failure = "ServiceUnavailable"
    upload(mirrored, tmp_path)
    fallback.failure = None
    primary.objects[KEY] = b"other"
    assert mirrored.sync_pending()["pending"] == 1
    assert not fallback.objects and not scratch_files(mirrored)


def test_stale_scratch_from_a_crashed_pass_is_removed(mirrored):
    import os
    scratch = mirrored.root / "scratch"
    scratch.mkdir()
    (scratch / "crashed.part").write_bytes(b"half a video")
    (scratch / "current.part").write_bytes(b"in use")
    old = time.time() - 2 * 3600
    os.utime(scratch / "crashed.part", (old, old))
    mirrored.sync_pending()
    assert [path.name for path in scratch.iterdir()] == ["current.part"]


@pytest.mark.parametrize("code", ["InvalidAccessKeyId", "AccessDenied", "ServiceUnavailable", "404"])
def test_playback_selects_r2_when_aws_is_unavailable(mirrored, tmp_path, code):
    upload(mirrored, tmp_path)
    stores(mirrored)[0].failure = code
    url = mirrored.generate_presigned_url("get_object", Params={"Bucket": "original", "Key": KEY}, ExpiresIn=60)
    assert url.startswith("https://r2.example/replica/")
    target = tmp_path / "restored.mp4"
    mirrored.download_file("original", KEY, str(target))
    assert target.read_bytes() == b"video"


def test_primary_remains_preferred_after_recovery(mirrored, tmp_path):
    upload(mirrored, tmp_path)
    primary, fallback = stores(mirrored)
    primary.failure = "AccessDenied"
    params = {"Bucket": "original", "Key": KEY}
    assert "r2.example" in mirrored.generate_presigned_url("get_object", Params=params)
    primary.failure = None
    assert "aws.example" in mirrored.generate_presigned_url("get_object", Params=params)
    assert params["Bucket"] == "original"


def test_recent_outage_probe_skips_primary_timeouts_and_recovers(mirrored, tmp_path):
    upload(mirrored, tmp_path)
    primary, fallback = stores(mirrored)
    health = {"checked_at": time.time(), "providers": {
        "primary": {"status": "unhealthy"}, "fallback": {"status": "healthy"}}}
    mirrored.ledger.write_health(health)
    primary.head_object = Mock(side_effect=AssertionError("Repeated primary timeout"))
    params = {"Bucket": "original", "Key": KEY}
    # The probe ran on the cron host; a host that never ran it routes the same way.
    assert "r2.example" in another_host(mirrored, tmp_path).generate_presigned_url("get_object", Params=params)
    assert mirrored.create_multipart_upload(**params)["UploadId"].startswith("r2:")
    primary.head_object = Mock(return_value={"ContentLength": 5})
    health["providers"]["primary"]["status"] = "healthy"
    mirrored.ledger.write_health(health)
    assert "aws.example" in mirrored.generate_presigned_url("get_object", Params=params)


def test_stale_outage_probe_does_not_hide_primary_recovery(mirrored, tmp_path):
    upload(mirrored, tmp_path)
    mirrored.ledger.write_health({"checked_at": time.time() - 181,
        "providers": {"primary": {"status": "unhealthy"}, "fallback": {"status": "healthy"}}})
    params = {"Bucket": "original", "Key": KEY}
    assert "aws.example" in mirrored.generate_presigned_url("get_object", Params=params)


def test_newest_draft_takes_priority_over_cached_provider_health(mirrored, tmp_path):
    primary, fallback = stores(mirrored)
    fallback.failure = "ServiceUnavailable"
    upload(mirrored, tmp_path, b"newest-version")
    fallback.failure = None
    fallback.objects[KEY] = b"old-version"
    mirrored.ledger.write_health({"checked_at": time.time(),
        "providers": {"primary": {"status": "unhealthy"}, "fallback": {"status": "healthy"}}})
    target = tmp_path / "restored.mp4"
    mirrored.download_file("original", KEY, str(target))
    assert target.read_bytes() == b"newest-version"


def test_unreadable_ledger_keeps_playback_on_the_default_order(mirrored, tmp_path, monkeypatch, caplog):
    from src import media_replication
    upload(mirrored, tmp_path)
    monkeypatch.setattr(media_replication, "_ledger_warning_at", float("-inf"))
    credential = uuid.uuid4().hex
    mirrored.ledger.routing = Mock(side_effect=ConnectionError(f"postgresql://app:{credential}@database.internal/supoclip"))
    params = {"Bucket": "original", "Key": KEY}
    with caplog.at_level(logging.WARNING, logger="src.media_replication"):
        assert mirrored._read_names(KEY) == ["primary", "fallback"]
        assert "aws.example" in mirrored.generate_presigned_url("get_object", Params=params)
        stores(mirrored)[0].failure = "ServiceUnavailable"
        assert "r2.example" in mirrored.generate_presigned_url("get_object", Params=params)
    # Said once, not for every request, and without where the database lives.
    assert len(caplog.records) == 1
    assert credential not in caplog.text and "database.internal" not in caplog.text


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
    mirrored.ledger.routing = Mock()
    stores(mirrored)[0].failure = "AccessDenied"
    with pytest.raises(ClientError):
        mirrored.head_object(Bucket="original", Key=KEY)
    mirrored.ledger.routing.assert_not_called()


def test_bucket_scope_is_preserved_before_network_access(tmp_path):
    ledger = Mock()
    client = ReplicatedMediaClient(Store("aws"), Store("r2"), "original", "replica", tmp_path, failover=True, ledger=ledger)
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"video")
    params = {"Bucket": "someone-else", "Key": KEY}
    calls = [lambda: client.head_object(**params), lambda: client.delete_object(**params),
             lambda: client.upload_file(str(path), "someone-else", KEY),
             lambda: client.complete_multipart_upload(**params, UploadId="upload-1")]
    for call in calls:
        with pytest.raises(ValueError, match="outside"):
            call()
    # The database is network access too.
    assert not ledger.mock_calls


def test_client_does_not_need_the_database_until_it_is_used(tmp_path, monkeypatch):
    from src import media_replication
    created = Mock(return_value=MemoryLedger())
    monkeypatch.setattr(media_replication, "PostgresLedger", created)
    client = ReplicatedMediaClient(Store("aws"), Store("r2"), "original", "replica", tmp_path, failover=True)
    created.assert_not_called()
    assert client.pending_status() == {"pending": 0, "oldest_pending_seconds": 0}
    assert client.ledger is client.ledger
    created.assert_called_once_with()


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
    row = mirrored.ledger.get("media/sources/video.mp4")
    assert (row["operation"], row["source_store"], row["pending"]) == ("copy", "fallback", ["primary"])
    primary.failure = None
    # Until the copy lands, reads go to the store the browser uploaded to.
    assert mirrored._read_names("media/sources/video.mp4") == ["fallback", "primary"]
    mirrored.sync_pending()
    assert primary.objects == fallback.objects == {"media/sources/video.mp4": b"browser-upload"}
    assert primary.metadata["media/sources/video.mp4"]["Metadata"]["expected-size"] == "14"
    assert mirrored.pending_status()["pending"] == 0 and not scratch_files(mirrored)


def test_existing_objects_are_copied_verified_and_never_rewrite_primary(mirrored):
    primary, fallback = stores(mirrored)
    key = "media/clips/historical.mp4"
    primary.objects[key] = b"historical-video"
    mirrored.ledger.begin = Mock(wraps=mirrored.ledger.begin)
    assert mirrored.mirror_existing(key, verify=True) == "copied"
    # Backfill must never leave work that a drainer could apply to the original:
    # it records nothing and writes only to the replica.
    mirrored.ledger.begin.assert_not_called()
    assert mirrored.pending_status()["pending"] == 0
    assert primary.uploads == 0
    assert fallback.objects == primary.objects
    assert fallback.metadata[key]["Metadata"]["supoclip-sha256"] == sha(b"historical-video")
    assert mirrored.mirror_existing(key, verify=True) == "verified"
    assert fallback.uploads == 1


def test_backfill_failure_raises_for_the_script_to_rerun(mirrored):
    primary, fallback = stores(mirrored)
    key = "media/clips/historical.mp4"
    primary.objects[key] = b"historical-video"
    fallback.upload_file = Mock(side_effect=ClientError({"Error": {"Code": "ServiceUnavailable"}}, "PutObject"))
    with pytest.raises(ClientError):
        mirrored.mirror_existing(key, verify=True)
    assert mirrored.pending_status()["pending"] == 0 and not scratch_files(mirrored)
    del fallback.upload_file
    assert mirrored.mirror_existing(key, verify=True) == "copied"


def test_backfill_yields_to_a_live_write_or_delete(mirrored, tmp_path):
    primary, fallback = stores(mirrored)
    fallback.failure = "ServiceUnavailable"
    upload(mirrored, tmp_path)
    fallback.failure = None
    assert mirrored.mirror_existing(KEY, verify=True) == "pending"
    assert not fallback.objects


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
    assert mirrored.ledger.routing(KEY) == (None, report)
    primary.failure = "InvalidAccessKeyId"
    report = mirrored.health_check("media")
    assert report["status"] == "degraded"
    assert report["providers"]["fallback"]["status"] == "healthy"
    assert "credentials" not in json.dumps(report)
    # Every host reads the probe from the ledger, not from the cron host's disk.
    assert mirrored.ledger.routing(KEY)[1] == report
    assert not (mirrored.root / "health.json").exists()


def test_health_counts_replication_still_owed(mirrored, tmp_path, monkeypatch):
    from src import media_replication
    primary, fallback = stores(mirrored)
    monkeypatch.setattr(media_replication, "urlopen", lambda request, timeout: Body(
        (primary if "aws.example" in request.full_url else fallback).objects[request.full_url.split("/", 4)[4]]))
    mirrored.ledger.begin(KEY, "copy", source="primary", pending=["fallback"])
    report = mirrored.health_check("media")
    assert report["status"] == "degraded" and report["replication"]["pending"] == 1


def test_legacy_delete_is_carried_into_the_ledger_and_applied(mirrored):
    primary, fallback = stores(mirrored)
    hold(fallback, b"video")
    directory = legacy_entry(mirrored, KEY, {"operation": "delete", "pending": ["fallback"]})
    mirrored.import_legacy_outbox()
    row = mirrored.ledger.get(KEY)
    assert (row["operation"], row["pending"]) == ("delete", ["fallback"])
    assert not (directory / "pending.json").exists()
    assert mirrored.sync_pending()["pending"] == 0
    assert not fallback.objects


def test_legacy_copy_is_carried_into_the_ledger_and_applied(mirrored):
    primary, fallback = stores(mirrored)
    key = "media/sources/video.mp4"
    fallback.objects[key] = b"browser-upload"
    directory = legacy_entry(mirrored, key, {"operation": "copy", "source": "fallback", "pending": ["primary"]})
    assert mirrored.sync_pending()["pending"] == 0
    assert primary.objects == fallback.objects
    assert not (directory / "pending.json").exists()


def test_legacy_upload_owed_to_one_store_is_copied_from_the_other(mirrored, tmp_path):
    primary, fallback = stores(mirrored)
    hold(primary, b"video")
    directory = legacy_entry(mirrored, KEY, {"operation": "upload", "pending": ["fallback"]}, payload=b"video")
    (directory / "verified.json").write_text("{}")
    mirrored.import_legacy_outbox()
    row = mirrored.ledger.get(KEY)
    assert (row["operation"], row["pending"], row["sha256"], row["size"]) == ("upload", ["fallback"], sha(b"video"), 5)
    assert row["extra"] == {"ContentType": "video/mp4"}
    # The other store has the revision, so the payload on this disk is no longer needed.
    assert sorted(path.name for path in directory.iterdir()) == [".lock", "verified.json"]
    assert another_host(mirrored, tmp_path).sync_pending()["pending"] == 0
    assert primary.objects == fallback.objects == {KEY: b"video"}


def test_legacy_upload_no_store_received_is_sent_from_its_payload(mirrored):
    primary, fallback = stores(mirrored)
    fallback.failure = "ServiceUnavailable"
    directory = legacy_entry(mirrored, KEY, {"operation": "upload", "pending": ["primary", "fallback"]}, payload=b"video")
    mirrored.import_legacy_outbox()
    assert primary.objects == {KEY: b"video"}
    assert primary.metadata[KEY] == {"ContentType": "video/mp4", "Metadata": {"supoclip-sha256": sha(b"video")}}
    assert mirrored.ledger.get(KEY)["pending"] == ["fallback"]
    assert not list(directory.glob("*.payload")) and not (directory / "pending.json").exists()
    fallback.failure = None
    assert mirrored.sync_pending()["pending"] == 0
    assert primary.objects == fallback.objects


def test_legacy_upload_stays_on_disk_while_no_store_accepts_it(mirrored):
    primary, fallback = stores(mirrored)
    primary.failure = fallback.failure = "ServiceUnavailable"
    directory = legacy_entry(mirrored, KEY, {"operation": "upload", "pending": ["primary", "fallback"]}, payload=b"video")
    assert mirrored.sync_pending()["pending"] == 0
    # The payload is the only copy of this revision.
    assert (directory / "staged.payload").read_bytes() == b"video" and (directory / "pending.json").exists()
    assert mirrored.ledger.get(KEY) is None
    primary.failure = fallback.failure = None
    assert mirrored.sync_pending()["pending"] == 0
    assert primary.objects == fallback.objects == {KEY: b"video"}
    assert not (directory / "pending.json").exists() and not list(directory.glob("*.payload"))


def test_legacy_entry_gives_way_to_a_newer_ledger_row(mirrored, tmp_path):
    primary, fallback = stores(mirrored)
    fallback.failure = "ServiceUnavailable"
    upload(mirrored, tmp_path, b"newer")
    owed = mirrored.ledger.get(KEY)
    directory = legacy_entry(mirrored, KEY, {"operation": "delete", "pending": ["primary", "fallback"]})
    mirrored.import_legacy_outbox()
    assert mirrored.ledger.get(KEY) == owed
    assert not (directory / "pending.json").exists()
    assert primary.objects == {KEY: b"newer"}


def test_legacy_import_is_repeatable_and_never_fails_the_pass(mirrored, caplog):
    primary, fallback = stores(mirrored)
    broken = mirrored.root / "0000"
    broken.mkdir()
    (broken / "pending.json").write_text("{not json")
    legacy_entry(mirrored, KEY, {"operation": "delete", "pending": ["primary", "fallback"]})
    busy = "media/clips/busy.mp4"
    legacy_entry(mirrored, busy, {"operation": "delete", "pending": ["primary", "fallback"]})
    with mirrored.ledger.lock(busy):
        mirrored.import_legacy_outbox()
    assert mirrored.ledger.get(KEY)["operation"] == "delete" and mirrored.ledger.get(busy) is None
    first = mirrored.ledger.get(KEY)
    mirrored.import_legacy_outbox()
    # The second pass picks up the entry that was locked and repeats nothing else.
    assert mirrored.ledger.get(KEY) == first and mirrored.ledger.get(busy)["operation"] == "delete"
    assert mirrored.sync_pending()["pending"] == 0
    assert (broken / "pending.json").exists()
    assert KEY not in caplog.text


def test_a_source_that_was_overwritten_without_a_row_is_copied_as_it_is(mirrored, tmp_path):
    """A row must not wait forever for a revision its source no longer holds."""
    primary, fallback = stores(mirrored)
    fallback.failure = "ServiceUnavailable"
    upload(mirrored, tmp_path, b"recorded revision")
    fallback.failure = None
    # Another writer replaced the object and its ledger row was lost.
    primary.objects["media/clips/clip.mp4"] = b"what the store holds now"
    primary.metadata["media/clips/clip.mp4"] = {"ContentType": "video/mp4"}
    mirrored.sync_pending()
    row = mirrored.ledger.get("media/clips/clip.mp4")
    assert row["operation"] == "copy" and row["source_store"] == "primary" and row["pending"] == ["fallback"]
    assert mirrored.sync_pending()["pending"] == 0
    assert fallback.objects["media/clips/clip.mp4"] == b"what the store holds now"


class LedgerContract:
    """Run against both ledgers: the tests above trust MemoryLedger to behave like PostgreSQL."""

    def test_begin_replaces_the_row_with_a_new_identity(self, ledger):
        assert ledger.get(KEY) is None
        first = ledger.begin(KEY, "upload", pending=["primary", "fallback"], sha256="a" * 64, size=5,
                             extra={"ContentType": "video/mp4", "Metadata": {"expected-size": "5"}})
        assert ledger.get(KEY) == first
        assert first["attempts"] == 0 and first["source_store"] is None
        assert first["extra"] == {"ContentType": "video/mp4", "Metadata": {"expected-size": "5"}}
        ledger.defer(KEY, op_id=first["op_id"])
        second = ledger.begin(KEY, "copy", source="fallback", pending=["primary"])
        assert second["op_id"] != first["op_id"]
        assert (second["operation"], second["source_store"], second["pending"]) == ("copy", "fallback", ["primary"])
        assert (second["attempts"], second["sha256"], second["size"], second["extra"]) == (0, None, None, {})
        assert second["created_at"] >= first["created_at"]
        assert ledger.status()["pending"] == 1

    def test_confirm_only_counts_for_the_revision_it_was_for(self, ledger):
        old = ledger.begin(KEY, "upload", pending=["primary", "fallback"], sha256="a" * 64, size=1)
        new = ledger.begin(KEY, "upload", pending=["primary", "fallback"], sha256="b" * 64, size=2)
        # A late acknowledgement of the replaced revision changes nothing.
        assert ledger.confirm(KEY, "primary", op_id=old["op_id"]) == ["primary", "fallback"]
        assert ledger.get(KEY) == new
        assert ledger.confirm(KEY, "primary", op_id=new["op_id"]) == ["fallback"]
        assert ledger.confirm(KEY, "primary", op_id=new["op_id"]) == ["fallback"]
        assert ledger.get(KEY)["pending"] == ["fallback"]
        assert ledger.confirm(KEY, "fallback", op_id=new["op_id"]) == []
        assert ledger.get(KEY) is None
        assert ledger.confirm(KEY, "fallback", op_id=new["op_id"]) == []

    def test_restore_puts_back_exactly_what_was_there(self, ledger):
        previous = ledger.begin(KEY, "upload", pending=["fallback"], sha256="a" * 64, size=1, extra={"ContentType": "video/mp4"})
        ledger.defer(KEY, op_id=previous["op_id"])
        previous = ledger.get(KEY)
        replacement = ledger.begin(KEY, "delete", pending=["primary", "fallback"])
        ledger.restore(previous)
        assert ledger.get(KEY) == previous
        ledger.clear(KEY, op_id=replacement["op_id"])
        assert ledger.get(KEY) == previous
        ledger.clear(KEY)
        assert ledger.get(KEY) is None
        ledger.clear(KEY)

    def test_due_is_oldest_first_and_defer_backs_off(self, ledger):
        first = ledger.begin("media/a.mp4", "delete", pending=["primary"])
        second = ledger.begin("media/b.mp4", "delete", pending=["primary"])
        assert [row["object_key"] for row in ledger.due(10)] == ["media/a.mp4", "media/b.mp4"]
        assert [row["object_key"] for row in ledger.due(1)] == ["media/a.mp4"]
        ledger.defer("media/a.mp4", op_id="someone-else")
        assert len(ledger.due(10)) == 2
        ledger.defer("media/a.mp4", op_id=first["op_id"])
        assert ledger.due(10) == [second]
        deferred = ledger.get("media/a.mp4")
        assert deferred["attempts"] == 1
        assert 50 < (deferred["next_attempt_at"] - datetime.now(timezone.utc)).total_seconds() < 70
        ledger.defer("media/a.mp4", op_id=first["op_id"])
        assert 110 < (ledger.get("media/a.mp4")["next_attempt_at"] - datetime.now(timezone.utc)).total_seconds() < 130
        # Capped at an hour however often it has failed.
        ledger.restore({**deferred, "attempts": 40})
        ledger.defer("media/a.mp4", op_id=first["op_id"])
        capped = ledger.get("media/a.mp4")
        assert capped["attempts"] == 41
        assert 3590 < (capped["next_attempt_at"] - datetime.now(timezone.utc)).total_seconds() < 3610
        ledger.restore({**capped, "next_attempt_at": datetime.now(timezone.utc) - timedelta(seconds=5)})
        assert [row["object_key"] for row in ledger.due(10)] == ["media/a.mp4", "media/b.mp4"]

    def test_status_counts_rows_and_ages_the_oldest(self, ledger):
        assert ledger.status() == {"pending": 0, "oldest_pending_seconds": 0}
        row = ledger.begin("media/a.mp4", "delete", pending=["primary"])
        ledger.begin("media/b.mp4", "delete", pending=["primary"])
        ledger.restore({**row, "created_at": datetime.now(timezone.utc) - timedelta(seconds=90)})
        status = ledger.status()
        assert status["pending"] == 2 and 89 <= status["oldest_pending_seconds"] <= 95

    def test_routing_returns_the_row_and_the_health_report_together(self, ledger):
        assert ledger.routing(KEY) == (None, None)
        row = ledger.begin(KEY, "copy", source="fallback", pending=["primary"])
        assert ledger.routing(KEY) == (row, None)
        report = {"status": "degraded", "checked_at": time.time(), "replication": {"pending": 1},
                  "providers": {"primary": {"status": "unhealthy", "error": "AccessDenied"}, "fallback": {"status": "healthy"}}}
        ledger.write_health(report)
        assert ledger.routing(KEY) == (row, report)
        assert ledger.routing("media/other.mp4") == (None, report)
        ledger.write_health({**report, "status": "healthy"})
        assert ledger.routing(KEY)[1]["status"] == "healthy"

    def test_lock_excludes_a_second_holder_until_released(self, ledger):
        assert issubclass(LockBusy, BlockingIOError)
        with ledger.lock(KEY):
            with pytest.raises(LockBusy):
                with ledger.lock(KEY, blocking=False):
                    pass
            with ledger.lock("media/other.mp4", blocking=False):
                pass
        with ledger.lock(KEY, blocking=False):
            pass

    def test_lock_is_released_when_the_holder_fails(self, ledger):
        with pytest.raises(RuntimeError):
            with ledger.lock(KEY):
                raise RuntimeError("upload failed")
        with ledger.lock(KEY, blocking=False):
            pass

    def test_blocking_lock_waits_for_the_holder(self, ledger):
        acquired = threading.Event()

        def waiter():
            with ledger.lock(KEY):
                acquired.set()

        thread = threading.Thread(target=waiter)
        with ledger.lock(KEY):
            thread.start()
            assert not acquired.wait(0.3)
        thread.join(10)
        assert acquired.is_set()

    def test_one_drainer_at_a_time_without_blocking_any_object(self, ledger):
        with ledger.drain_lock():
            with pytest.raises(LockBusy):
                with ledger.drain_lock():
                    pass
            # An object that happens to be called "drain" is unaffected.
            with ledger.lock("drain", blocking=False):
                pass
        with ledger.drain_lock():
            pass


class TestMemoryLedger(LedgerContract):
    @pytest.fixture
    def ledger(self):
        return MemoryLedger()
