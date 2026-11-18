# Preview renders

Most generated clips are never downloaded, exported or published. With the
`preview_renders` feature flag, generation encodes every clip as a cheap
**preview** and renders the **full-quality** clip only when someone needs the
file. A full render costs one encode of a clip-length file, a minute or two.

## What happens

1. **Generation** runs exactly as before up to the final pass: download,
   transcription, AI selection, the stitched source ranges (workflow
   `source_ranges` included), framing (`vertical_layout`), captions, hook title
   and B-roll. The final pass then encodes a preview: the same filter graph,
   shrunk to `PREVIEW_RENDER_SHORT_SIDE` (540×960 for 9:16) by the encoder's
   scaler, with `PREVIEW_RENDER_PRESET`/`PREVIEW_RENDER_CRF`. Captions and
   overlays are composed at full size first, so a preview frames, captions and
   cuts the clip exactly like the full render. 720p plans skip the extra
   downscale pass.
2. The inputs of that final pass are kept: the stitched source
   (`render-source.bin`), the burned captions file (`render-captions.ass`) and
   the plan (`render.json`: output format, framing plan, speaker plan, fonts,
   the plan's resolution cap). With `MEDIA_STORAGE=s3` they go to
   `render-inputs/<task>/<token>/` in the bucket before the clip row is
   written; `generated_clips.render_quality = 'preview'` and
   `render_inputs_path` points at `render.json`. If they can't be stored, that
   clip is rendered at full quality on the spot instead.
3. **On demand** (`request_full_render`), the clip row moves to
   `full_render_status = 'queued'` and a `render_full_clip` job goes to the
   **`supoclip_finals`** queue. The job fetches the inputs and repeats the final
   pass at full quality, at the plan's resolution in the same pass (Pro 720p,
   Creator and `legacy_hd` 1080p, as recorded at generation). It publishes the
   file (with the preview's sidecars: source ranges, B-roll, caption position,
   editor layout), then swaps it into `file_path` (compare-and-swap on the
   preview's path) and sets `render_quality = 'full'`. The preview and its
   inputs go through the deletion outbox. Every reader of `file_path` gets the
   full clip from then on; a second download is free.

A clip only moves from preview to full. Clips and tasks from before the flag
(and tasks created while it is off) are full quality and never change.

## What triggers a full render

| Trigger | Where | Waits? |
|---|---|---|
| Single download | `GET /tasks/{id}/clips/{clip}/file?download=true` | yes |
| Any `/file` request from iOS or an API key / MCP, unless it sends `x-supoclip-accept-preview: 1` | same route | yes (they can't say play vs save) |
| Shared link download | `GET /tasks/shared/{token}/clips/{clip}/file?download=true` | yes |
| ZIP | `GET /tasks/{id}/clips/zip` and `/zip/preflight` | yes, all clips |
| Preset export | `POST .../exports`, `GET .../export?preset=` | yes |
| Publish / schedule | `POST /social/posts` | yes, before the post exists |
| Trim, split, merge, caption edit | legacy edit routes | yes |
| Clip studio | `GET .../editor` | no: started in the background (the studio works from the source) |

The web app plays previews inline (`/file` without `download`).

"Waits" means the request holds until the clip is full quality, up to
`FULL_RENDER_DOWNLOAD_WAIT_SECONDS` (default 300). Past that it answers
**503** with `Retry-After: 10` and

```json
{"detail": {"code": "FULL_QUALITY_PREPARING", "message": "...", "clip_id": "...",
            "render_quality": "preview", "full_render": {"status": "running", "progress": 40, "error": null}}}
```

The render keeps going; asking again later gets the file. A failed render
answers **502** with `code: FULL_QUALITY_FAILED`, and the next request queues it
again.

## API

Clip objects (`GET /tasks/{id}`, `GET /tasks/{id}/clips`) carry two new fields:

- `render_quality`: `"preview"` or `"full"`. Old clips are `"full"`.
- `full_render`: `{status, progress, error}`. `status` is `ready` (full quality),
  `none` (a preview nobody asked for yet), `queued`, `running` or `failed`.
  `progress` (0–100) is an estimate while it runs.

Shared results expose `render_quality` too.

Routes for clients that want to show progress instead of holding a request:

- `POST /tasks/{id}/clips/{clip}/full-render` (202), optional body
  `{"trigger": "download"|"zip"|"export"|"publish"|"studio"}` for analytics.
  Returns `{clip_id, render_quality, full_render}`; idempotent.
- `GET /tasks/{id}/clips/{clip}/full-render`: the same object.
- `POST|GET /tasks/{id}/full-render`: every clip, plus
  `{total, ready, failed, progress, status: "ready"|"preparing"|"failed"}`.

The web app calls these before a download, export, ZIP or post
(`frontend/src/lib/full-quality.ts`) and shows "Preparing full quality… 40%".
The MCP client retries `FULL_QUALITY_PREPARING` 503s on its own.

### iOS app

The current iOS build downloads through the same routes and keeps working:
every `/file` request it makes gets the full-quality clip (rendered first if
needed), because the backend can't tell playing from saving. That also means
playing a preview in the app renders it. Until an app update ships, keep the
flag's `ios_enabled` off so tasks created on iOS render full quality at
generation (tasks created on the web and opened in the app still work). The
update should:

1. Send `x-supoclip-accept-preview: 1` when it *plays* `/file`: it then gets
   the preview. Saving or sharing keeps using `/file?download=true` (or no
   header), which is always full quality.
2. Show a "Preview" label from `render_quality`, and before saving call
   `POST .../full-render` and poll `GET .../full-render` to show progress, or
   handle `503 FULL_QUALITY_PREPARING` with `Retry-After`.

## Workers

`run_workers` starts a third process next to the video worker on every machine
that renders: `FinalRenderWorkerSettings` (`src/workers/final_render_worker.py`)
drains `supoclip_finals` with `FULL_RENDER_MAX_JOBS` slots (default 2). It never
waits behind generation jobs on `supoclip_tasks`, which can hold every slot for
hours. Set `EMBED_FINAL_RENDER_WORKER=0` on a machine that should not take
them. One try per job; a lost job is queued again by the next request after 10
minutes (3 minutes without a heartbeat for a running one).

## Storage and cleanup

- Inputs live under `render-inputs/<task>/<token>/` (bucket) or
  `TEMP_DIR/render-inputs/` (local storage). They are deleted with the clip,
  with the task, after the full render, and when a regeneration replaces the
  clip.
- The stitched source is the largest input, roughly 2× the full clip
  (CRF 16 intermediate). It exists only while the clip is a preview.

## Configuration

| Variable | Default | |
|---|---|---|
| `PREVIEW_RENDER_SHORT_SIDE` | 540 | Preview size (short side) |
| `PREVIEW_RENDER_CRF` | 28 | Preview quality |
| `PREVIEW_RENDER_PRESET` | veryfast | Preview x264 preset |
| `FULL_RENDER_MAX_JOBS` | 2 | Full renders at once per worker machine |
| `FULL_RENDER_DOWNLOAD_WAIT_SECONDS` | 300 | How long a waiting request holds before 503 (API) |
| `EMBED_FINAL_RENDER_WORKER` | 1 | Start the `supoclip_finals` worker in `run_workers` |

## Rollout

The `preview_renders` flag is off on web and iOS, admins bypass. It is read once
when a task is created (`tasks.preview_renders`), so a resumed task renders the
way it started. Turn it on for web in `/admin` → Feature Flags. The
`preview-renders` feature tour (`frontend/src/lib/feature-tours.ts`) shows on a
generation's results when its clips are previews; set its `releasedAt` to the
day the flag opens.

Measure it with the `clip_full_quality_requested` PostHog event
(`task_id`, `clip_id`, `trigger`): full renders requested per clip generated is
the share of clips that are used.
