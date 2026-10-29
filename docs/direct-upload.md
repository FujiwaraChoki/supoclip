# Direct video upload

Large files should go straight to storage, or straight to the API, instead of
through the Next.js `/api/upload` proxy. The web app already does this. A
later iOS build can use the same contract. The shipped 1.0.5 app keeps calling
`POST /api/upload` and does not need this flow.

The bytes are not transcoded here. Task creation still takes the returned
`video_path` as `source.url`.

## Which host to call

The iOS app that already has a website session cookie should call the web
origin. Those routes attach the API credentials:

| Web route | API route |
| --- | --- |
| `POST /api/upload/authorization` | `POST /upload/authorize` |
| `POST /api/upload/parts` | `POST /upload/parts` |
| `POST /api/upload/complete` | `POST /upload/complete` |
| `POST /api/upload/abort` | `POST /upload/abort` |

A client that already talks to the API (API key or the signed
`x-supoclip-user-id` / `x-supoclip-ts` / `x-supoclip-signature` headers) calls
the API routes itself. An API key is `Authorization: Bearer sk_...` or
`x-api-key`.

## 1. Authorize

`POST` JSON:

```json
{"filename": "clip.mp4", "size": 12345, "contentType": "video/mp4"}
```

`size` is an integer number of bytes, not a string. `filename` is only used
to pick a video extension. An empty or oversized file is `400`. Too many
uploads in progress, or too many in the last day, is `429`.

### Object storage is on

Both the web route and `POST /upload/authorize` return:

```json
{
  "storage": "s3",
  "video_path": "upload://s3/{owner}/{id}/video.mp4",
  "uploadId": "multipart-id",
  "partSize": 67108864,
  "parts": [{"partNumber": 1, "url": "https://storage.example/part"}]
}
```

`partSize` is 64 MiB. `parts` covers the whole file, in order, starting at 1.
The last part is shorter when the file is not an exact multiple.

`PUT` each part to its `url` with that slice of the file as the body. Send
`Content-Length` equal to the slice. The signed URL rejects a different
length. Up to three parts at a time. Read the `ETag` response header; keep
the quotes S3 returns.

Part URLs last one hour. The upload session lasts one day. Refresh with
`POST /upload/parts` (or `/api/upload/parts`):

```json
{"video_path": "upload://s3/...", "uploadId": "...", "partNumbers": [2, 3]}
```

`partNumbers` holds one to three integers. The response is
`{"parts": [{"partNumber": 2, "url": "https://..."}]}`.

Finish with `POST /upload/complete` (or `/api/upload/complete`):

```json
{
  "video_path": "upload://s3/...",
  "uploadId": "...",
  "parts": [{"partNumber": 1, "etag": "\"abc\""}, {"partNumber": 2, "etag": "\"def\""}]
}
```

Part numbers must be `1..n` with no gaps. Success is:

```json
{"video_path": "upload://s3/..."}
```

The same `video_path` comes back. Sending complete again after a lost
response is safe and returns that same path. A body that is not the
authorized size is `400` and the multipart upload is aborted.

Cancel with `POST /upload/abort` and
`{"video_path", "uploadId"}`. Success is `{"message": "Upload cancelled"}`.

### Object storage is off

`POST /upload/authorize` returns only:

```json
{"storage": "local"}
```

The web route turns that into a direct API upload when the server can sign
requests:

```json
{
  "directUpload": true,
  "uploadUrl": "https://api.example/upload",
  "headers": {
    "x-supoclip-user-id": "...",
    "x-supoclip-ts": "...",
    "x-supoclip-signature": "..."
  }
}
```

`POST` a multipart body to `uploadUrl`. The file field name is `video`. Send
the returned `headers` as-is, plus `Content-Length`. An optional
`Idempotency-Key` (`8–200` characters: letters, digits, `.`, `_`, `:`, `-`)
makes a retry of the same bytes return the same path. Reusing the key for
different bytes is `409`.

Success, same shape as `POST /api/upload`:

```json
{"message": "Video uploaded successfully", "video_path": "upload://{uuid}.mp4"}
```

When the web server cannot sign API requests, `/api/upload/authorization`
returns `{"directUpload": false, "reason": "signed_backend_auth_required"}`.
The client then posts the file to `/api/upload` instead. That is the 1.0.5
path.

An API client that receives `{"storage": "local"}` posts the same multipart
body to `POST /upload` on the API host with its existing credentials.

## 2. Create the task

```json
{"source": {"url": "<video_path from authorize or from the upload response>"}}
```

Use `POST /api/tasks/create` with the session cookie, or `POST /tasks/` on the
API. Keep the `video_path` string exactly. An `upload://s3/...` path and an
`upload://{uuid}.ext` path are both valid. The same `Idempotency-Key` on this
request, with the same options, returns the task that is already queued or
processing instead of starting a second one.
