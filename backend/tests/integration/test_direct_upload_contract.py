"""The direct-upload responses an iOS client can rely on."""

from unittest.mock import Mock

from botocore.exceptions import ClientError
from sqlalchemy import text

from src.media_storage import PART_BYTES
from tests.fixtures.factories import create_user
from tests.integration.test_media_deletion_transactions import media_backend


async def test_s3_authorize_part_refresh_and_complete_keep_one_video_path(
    client, db_session, media_backend, auth_headers
):
    storage = media_backend
    await create_user(db_session, user_id="user-1")
    await db_session.execute(text("DELETE FROM media_upload_sessions WHERE user_id = 'user-1'"))
    await db_session.commit()
    storage.client.create_multipart_upload = Mock(return_value={"UploadId": "ios-upload"})
    storage.client.generate_presigned_url = Mock(return_value="https://storage.test/part")
    storage.client.list_parts = Mock(return_value={
        "Parts": [{"PartNumber": 1, "Size": 1}],
        "IsTruncated": False,
    })
    storage.client.complete_multipart_upload = Mock()
    storage.client.head_object = Mock(return_value={
        "ContentLength": 1,
        "Metadata": {"expected-size": "1"},
    })
    storage.client.abort_multipart_upload = Mock()

    response = await client.post(
        "/upload/authorize",
        json={"filename": "clip.mov", "size": 1, "contentType": "video/quicktime"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    authorization = response.json()
    assert authorization["storage"] == "s3"
    assert authorization["uploadId"] == "ios-upload"
    assert authorization["partSize"] == PART_BYTES
    assert authorization["video_path"].startswith("upload://s3/")
    assert authorization["video_path"].endswith("/video.mov")
    assert authorization["parts"] == [{"partNumber": 1, "url": "https://storage.test/part"}]
    signed = storage.client.generate_presigned_url.call_args.kwargs["Params"]
    assert signed["ContentLength"] == 1
    assert signed["PartNumber"] == 1

    payload = {"video_path": authorization["video_path"], "uploadId": authorization["uploadId"]}
    refreshed = await client.post(
        "/upload/parts",
        json={**payload, "partNumbers": [1]},
        headers=auth_headers,
    )
    assert refreshed.status_code == 200
    assert refreshed.json() == {"parts": [{"partNumber": 1, "url": "https://storage.test/part"}]}

    body = {**payload, "parts": [{"partNumber": 1, "etag": '"abc"'}]}
    completed = await client.post("/upload/complete", json=body, headers=auth_headers)
    assert completed.status_code == 200
    assert completed.json() == {"video_path": authorization["video_path"]}

    storage.client.complete_multipart_upload.side_effect = ClientError(
        {"Error": {"Code": "NoSuchUpload"}}, "CompleteMultipartUpload"
    )
    retried = await client.post("/upload/complete", json=body, headers=auth_headers)
    assert retried.status_code == 200
    assert retried.json() == {"video_path": authorization["video_path"]}


async def test_local_authorize_tells_the_api_client_to_post_the_file(
    client, app, auth_headers
):
    app.state.config.media_storage = "local"
    response = await client.post(
        "/upload/authorize",
        json={"filename": "clip.mp4", "size": 8, "contentType": "video/mp4"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json() == {"storage": "local"}
