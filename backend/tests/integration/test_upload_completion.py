"""Completing a direct upload is safe to repeat, and only with the same parts."""

import asyncio

import pytest
from botocore.exceptions import ClientError
from sqlalchemy import text

from src.api.routes import media
from src.config import Config, set_config_override
from src.media_storage import PART_BYTES, MediaStorage
from tests.fixtures.factories import create_user

SIZE = PART_BYTES + 5  # two parts


class MultipartS3:
    """Just enough multipart S3 to complete, lose a response, and retry."""

    def __init__(self):
        self.uploads = {}
        self.objects = {}
        self.deleted = []
        self.completed = 0

    @staticmethod
    def _gone(operation):
        return ClientError({"Error": {"Code": "NoSuchUpload"}}, operation)

    def create_multipart_upload(self, Bucket, Key, ContentType, Metadata):
        upload_id = f"upload-{len(self.uploads) + len(self.objects) + 1}"
        self.uploads[upload_id] = {"key": Key, "metadata": Metadata, "parts": {}}
        return {"UploadId": upload_id}

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        return f"https://storage.test/{Params['UploadId']}/{Params['PartNumber']}"

    def put_part(self, upload_id, number, size):
        etag = f'"etag-{upload_id}-{number}"'
        self.uploads[upload_id]["parts"][number] = (etag, size)
        return etag

    def list_parts(self, Bucket, Key, UploadId, MaxParts, PartNumberMarker=None):
        if UploadId not in self.uploads:
            raise self._gone("ListParts")
        parts = self.uploads[UploadId]["parts"]
        return {"Parts": [{"PartNumber": n, "ETag": e, "Size": s} for n, (e, s) in sorted(parts.items())],
                "IsTruncated": False}

    def complete_multipart_upload(self, Bucket, Key, UploadId, MultipartUpload):
        upload = self.uploads.get(UploadId)
        if upload is None:
            raise self._gone("CompleteMultipartUpload")
        for part in MultipartUpload["Parts"]:
            stored = upload["parts"].get(part["PartNumber"])
            if stored is None or stored[0].strip('"') != part["ETag"].strip('"'):
                raise ClientError({"Error": {"Code": "InvalidPart"}}, "CompleteMultipartUpload")
        self.completed += 1
        self.objects[Key] = (sum(size for _, size in upload["parts"].values()), upload["metadata"])
        del self.uploads[UploadId]

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        size, metadata = self.objects[Key]
        return {"ContentLength": size, "Metadata": metadata}

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)
        self.objects.pop(Key, None)

    def abort_multipart_upload(self, Bucket, Key, UploadId):
        if self.uploads.pop(UploadId, None) is None:
            raise self._gone("AbortMultipartUpload")


@pytest.fixture
async def s3(tmp_path, monkeypatch, db_session):
    config = Config()
    config.media_storage = "s3"
    config.media_s3_bucket = "private-media"
    config.temp_dir = str(tmp_path)
    config.backend_auth_secret = "test-backend-auth-secret"
    config.allow_unsigned_backend_auth = False
    set_config_override(config)
    fake = MultipartS3()
    monkeypatch.setattr(MediaStorage, "client", property(lambda self: fake))
    await create_user(db_session, user_id="user-1")
    yield fake
    set_config_override(None)


async def _uploaded(client, s3, auth_headers):
    """Authorize a two-part upload and send both parts. Returns the complete payload."""
    response = await client.post(
        "/upload/authorize",
        json={"filename": "talk.mp4", "size": SIZE, "contentType": "video/mp4"},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    authorization = response.json()
    upload_id = authorization["uploadId"]
    parts = [
        {"partNumber": 1, "etag": s3.put_part(upload_id, 1, PART_BYTES)},
        {"partNumber": 2, "etag": s3.put_part(upload_id, 2, 5)},
    ]
    return {"video_path": authorization["video_path"], "uploadId": upload_id, "parts": parts}


async def _state(db_session, payload):
    return (await db_session.execute(text(
        "SELECT state FROM media_upload_sessions WHERE upload_id = :id"
    ), {"id": payload["uploadId"]})).scalar_one()


async def test_retry_after_a_lost_completion_response_returns_the_same_path(client, s3, auth_headers, db_session):
    payload = await _uploaded(client, s3, auth_headers)

    first = await client.post("/upload/complete", json=payload, headers=auth_headers)
    # The client never saw that answer and sends the same request again.
    retry = await client.post("/upload/complete", json=payload, headers=auth_headers)

    assert first.status_code == 200, first.text
    assert retry.status_code == 200, retry.text
    assert retry.json() == first.json() == {"video_path": payload["video_path"]}
    assert s3.completed == 1
    assert s3.deleted == []
    assert await _state(db_session, payload) == "completed"


async def test_retry_recovers_when_the_api_died_after_storage_completed(
    client, s3, auth_headers, db_session, monkeypatch,
):
    payload = await _uploaded(client, s3, auth_headers)
    original = media.finish_upload

    async def crash(*_args, **_kwargs):
        raise RuntimeError("worker restarted")

    monkeypatch.setattr(media, "finish_upload", crash)
    with pytest.raises(RuntimeError):
        await client.post("/upload/complete", json=payload, headers=auth_headers)
    assert s3.completed == 1
    assert await _state(db_session, payload) == "uploading"

    monkeypatch.setattr(media, "finish_upload", original)
    retry = await client.post("/upload/complete", json=payload, headers=auth_headers)

    assert retry.status_code == 200, retry.text
    assert retry.json() == {"video_path": payload["video_path"]}
    assert await _state(db_session, payload) == "completed"


async def test_retry_naming_other_parts_is_a_conflict_and_keeps_the_object(client, s3, auth_headers, db_session):
    payload = await _uploaded(client, s3, auth_headers)
    assert (await client.post("/upload/complete", json=payload, headers=auth_headers)).status_code == 200

    tampered = {**payload, "parts": [payload["parts"][0], {"partNumber": 2, "etag": '"something-else"'}]}
    conflict = await client.post("/upload/complete", json=tampered, headers=auth_headers)

    assert conflict.status_code == 409, conflict.text
    assert s3.deleted == []
    assert await _state(db_session, payload) == "completed"
    # The original request still gets its answer.
    again = await client.post("/upload/complete", json=payload, headers=auth_headers)
    assert again.status_code == 200, again.text


async def test_concurrent_retries_of_one_completion_both_get_the_path(client, s3, auth_headers, db_session):
    payload = await _uploaded(client, s3, auth_headers)
    # Same parts; one client strips the ETag quotes.
    unquoted = {**payload, "parts": [{**part, "etag": part["etag"].strip('"')} for part in payload["parts"]]}

    first, second = await asyncio.gather(
        client.post("/upload/complete", json=payload, headers=auth_headers),
        client.post("/upload/complete", json=unquoted, headers=auth_headers),
    )

    assert (first.status_code, second.status_code) == (200, 200), (first.text, second.text)
    assert first.json() == second.json() == {"video_path": payload["video_path"]}
    assert s3.completed == 1
    assert s3.deleted == []
    assert await _state(db_session, payload) == "completed"


async def test_storage_refusing_the_parts_lets_a_corrected_list_complete(client, s3, auth_headers):
    payload = await _uploaded(client, s3, auth_headers)
    wrong = {**payload, "parts": [payload["parts"][0], {"partNumber": 2, "etag": '"stale"'}]}

    with pytest.raises(ClientError):
        await client.post("/upload/complete", json=wrong, headers=auth_headers)
    corrected = await client.post("/upload/complete", json=payload, headers=auth_headers)

    assert corrected.status_code == 200, corrected.text
    assert s3.completed == 1


async def test_completion_after_abort_is_refused(client, s3, auth_headers):
    payload = await _uploaded(client, s3, auth_headers)
    session = {"video_path": payload["video_path"], "uploadId": payload["uploadId"]}
    assert (await client.post("/upload/abort", json=session, headers=auth_headers)).status_code == 200

    response = await client.post("/upload/complete", json=payload, headers=auth_headers)

    assert response.status_code == 400
    assert s3.completed == 0


async def test_another_account_cannot_complete_or_probe_an_upload(client, s3, auth_headers, db_session):
    from tests.integration.test_upload_idempotency import _headers_for

    payload = await _uploaded(client, s3, auth_headers)
    await create_user(db_session, user_id="user-2", email="user-2@example.com")

    response = await client.post("/upload/complete", json=payload, headers=_headers_for("user-2"))

    assert response.status_code == 400
    assert s3.completed == 0
