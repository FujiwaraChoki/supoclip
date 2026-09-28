# VideoScale YouTube downloads

VideoScale can be selected explicitly with `YOUTUBE_DOWNLOAD_PROVIDER=videoscale`.
This provider does not automatically fall back to Apify or another paid service.

## Configuration

Set these secrets in the deployment environment, never in source control:

```dotenv
VIDEOSCALE_USERNAME=
VIDEOSCALE_PASSWORD=
VIDEOSCALE_VIDEO_QUALITY=1080
VIDEOSCALE_DOWNLOAD_TIMEOUT_SECONDS=3600
VIDEOSCALE_TRANSFER_TIMEOUT_SECONDS=900
```

The default quality is 720; set 1080 to retain full HD inputs. Existing plan duration
and generation limits remain in effect. Both API and worker services need the new
environment variables. Recreate the services after changing their environment.

## Delivery and validation

The API selects available video and original audio tracks, submits each once,
polls for completion, and returns stored download URLs. SupoClip downloads the
tracks and combines them locally. Audio language and format IDs vary by source.
Completed files must contain both tracks and match the expected source duration.

The observed storage endpoint is `s3.fr-par.scw.cloud/youtube-download/`. Its
returned HTTP links are upgraded to HTTPS before transfer. Unrecognized hosts,
redirects, incomplete files, and files exceeding the configured size cap must be
rejected. API credentials and signed storage URLs must not appear in logs.

## Trial and operations

On 28 September 2026, direct tests from Hetzner retrieved a complete 46m46s source
at 1080p and a 94-minute source at 720p, including original audio. This establishes
these cases only; it is not a provider uptime guarantee or a three-hour benchmark.

The account began with 10 GB of free bandwidth. Monitor the provider usage dashboard
and replenish only with explicit purchase approval. No card or automatic top-up is
needed for the trial. An exhausted balance should fail visibly without charging
another download provider.

Rollback: restore the previous provider environment value and previous deployment
files, then recreate backend and worker. Do not switch during active processing jobs.
