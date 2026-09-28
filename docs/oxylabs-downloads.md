# Oxylabs YouTube downloads

Set `YOUTUBE_DOWNLOAD_PROVIDER=oxylabs` on backend and worker. Metadata still uses the existing YouTube metadata provider. This mode does not fall back to Apify or yt-dlp.

## Configuration

- `OXYLABS_USERNAME` and `OXYLABS_PASSWORD`: Web Scraper API credentials (include the dashboard username suffix).
- `OXYLABS_VIDEO_QUALITY=720`: upper quality target; lower-resolution sources remain lower resolution.
- `OXYLABS_DOWNLOAD_TIMEOUT_SECONDS=3600`: submit and poll deadline. Oxylabs also limits downloads to one hour.
- `OXYLABS_TRANSFER_TIMEOUT_SECONDS=900`: additional time for retrieving the result and combining separate tracks if necessary.
- `OXYLABS_STORAGE_BUCKET`, `OXYLABS_STORAGE_PREFIX=youtube`, `OXYLABS_STORAGE_REGION`: private delivery location.
- `OXYLABS_STORAGE_READ_KEY` / `OXYLABS_STORAGE_READ_SECRET`: optional dedicated reader credentials. If omitted, boto3 uses the existing AWS credential chain.
- For S3-compatible storage, set `OXYLABS_STORAGE_ENDPOINT` to the public HTTPS endpoint and provide bucket-scoped `OXYLABS_STORAGE_WRITE_KEY` / `OXYLABS_STORAGE_WRITE_SECRET`. These writer credentials are sent to Oxylabs in its documented storage URL. AWS S3 uses a bucket policy instead, with no AWS credentials sent to Oxylabs.

Keep credentials in the private `.env` file, never in Git. Use separate environments for development and production.

## AWS setup

`scripts/setup-oxylabs-storage.py` records the production bucket setup. Review its account, bucket, application identity, and region before running with an authorized administrator session. It creates no credentials and does not grant access to other buckets.

The bucket uses encryption, blocks public access, requires HTTPS, and expires `youtube/` objects after one day. AWS lifecycle expiry is asynchronous, not an exact 24-hour deletion deadline. Oxylabs can write to that prefix; the existing SupoClip AWS identity can list this bucket and read that prefix. AWS transfer and storage charges are separate from Oxylabs charges.

## Verification and rollout

1. Confirm a funded Web Scraper API plan and its media rate. A $1 trial is suitable for a smoke test, not normal production traffic.
2. Validate Compose configuration and run the targeted backend tests.
3. Run `download_video_via_oxylabs` from a one-off worker container against a short public video. Inspect the result with ffprobe for audio and video streams.
4. Drain active tasks before recreating backend and worker with `YOUTUBE_DOWNLOAD_PROVIDER=oxylabs`.
5. Confirm both services report the selected provider and that the backend health endpoint and worker queue connection are healthy.

The API submission is never automatically retried, avoiding duplicate paid jobs after ambiguous submission failures. Transient polling network errors reuse the existing job. Explicit user retries can submit another paid job. Files are bounded by `MAX_VIDEO_UPLOAD_BYTES`; incomplete transfers are removed. Per-download folders prevent concurrent requests for the same video from overwriting each other.

## Initial live evidence (2026-09-28)

- A 20-second excerpt from previously failing video `S7CrlFLAmEA` was delivered successfully: 1280×720, audio present, 3,539,933 bytes.
- The complete short video `jNQXAC9IVRw` passed the production worker integration: 18.947 seconds, 320×240 source resolution, audio present, 742,760 bytes.
- The dashboard showed $0.02 trial budget consumed after the two requests.

These checks verify download delivery and local media validation. They do not establish full-length download reliability or clipping/transcription success.

## Rollback

Restore the previous provider environment value and recreate backend and worker after draining active jobs. Legacy Apify and yt-dlp support remains available. Production's pre-migration configuration and modified source files were archived privately under `/home/fuji/supoclip-deploy-backups/oxylabs-20260928/`.

## References

- https://developers.oxylabs.io/api-targets/video-and-social-media/youtube/youtube-downloader
- https://developers.oxylabs.io/products/web-scraper-api/features/result-processing-and-storage/cloud-storage
