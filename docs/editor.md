# Clip studio

Open **Edit** on a generated clip to enter `/tasks/[id]/edit?clip=<id>`.
The studio keeps generated clips intact. Its draft stores cuts, timed words,
caption styling, framing, audio gain, and video effects separately from the media.

## Editing

- Select a clip in the left sidebar. Drafts autosave to the server and are also
  cached locally for recovery after an interrupted save.
- Use the thumbnail/waveform timeline to seek and drag the trim handles, or enter
  exact in/out seconds. Split, duplicate, remove, and reorder segments. Playback
  follows the resulting segment order; looping can be disabled.
- Correct individual words and their timestamps in **Captions**. Highlights apply
  to individual occurrences, including non-English words. The script editor keeps
  unchanged words anchored when adding or replacing text; review inserted timings.
- Captions start from the clip's caption template, including its animation
  (karaoke, pop, fade, bounce), outline, and active-word pill; the controls in
  **Captions** adjust colours, size, and position on top of it. The AI hook title
  is editable there too and shows at the top for the first few seconds.
- Drag the preview vertically to position captions. Use **Framing** to drag the
  subject, choose fit/fill, zoom, and select vertical, square, or original format.
- **Audio** controls exported gain/muting. Playback speed changes preview only.
- **Effects** affect the video independently of the caption layer.
- Undo/redo covers draft changes. **Restore original** resets the editing baseline
  and is itself undoable. Original generated files remain on the results page.
- Caption styles can be saved on the current device and applied to all task clips.
- Select multiple clips to combine their saved cuts and words into a **new** clip.
  Selection order controls sequence. The first clip supplies caption, framing,
  effect, and audio settings for the combined draft. Input clips stay intact.

Shortcuts: Space plays/pauses; arrows step frames; Shift+arrows jump one second;
I/O set boundaries; S splits; Ctrl/Cmd+Z undoes; Ctrl/Cmd+Shift+Z redoes;
Ctrl/Cmd+S saves. Shortcuts are inactive in text fields and dialogs.

## Dynamic edits

When dynamic edits are on for the account ([dynamic-edits.md](dynamic-edits.md)),
a clip's draft carries its effects in `document.fx`: the style, the beats, the
cues (S time) and what you removed. Clips generated with effects open with them.

- **Edits** tab: intensity (Off, Subtle, Bold, Max), the switches, music mood,
  bed volume and duck depth, retention options and reactions. Changing them
  re-resolves on the server (`/fx/resolve`, pure and fast) and lands as one
  undoable change. A clip without beats offers **Plan edits**, which asks the AI
  worker for them (counts against `EDIT_PLAN_DAILY_LIMIT`); **Re-plan with AI**
  asks again. The beat list seeks, deletes and re-kinds beats. **Remove all
  edits** clears the effects. **Analyse face** appears when face-anchored effects
  need a face track the clip doesn't have yet (combined clips, for example).
- **Effects** tab: grade and grain, next to brightness, contrast and saturation.
- **Captions** tab: the three animated styles (Highlighter, Emoji Drop, Behind Head) appear in the template
  picker; a draft that already uses an older animated style keeps it.
- **Timeline**: the FX lane shows each effect, coloured by family (sound,
  picture, overlay, time). Click selects, drag moves (the effect becomes yours and
  survives a re-resolve), double-click opens it, Delete removes it (an automatic
  one is remembered as removed), and double-clicking an empty spot adds a sound.
  The Music lane shows the duck envelope. Durations show the final length after
  speed ramps and freezes.

The preview draws effects with the same renderer the server uses, through the
time warp, with sound. Safari and Firefox measure text slightly differently from
the server, so the preview is approximate there.

## Export

The export dialog shows dimensions, edited duration, audio state, and approximate
file size. TikTok, Reels, and Shorts presets set target bitrates; the selected
framing controls the output aspect ratio.

**Background export** uses the worker and produces H.264/AAC MP4. Jobs keep running
when the editor closes and reappear in the export queue. Progress, cancellation,
failures, and completed downloads are available there. Export all clips queues
saved drafts sequentially, preparing uncached source media as needed.

**On this device** checks browser encoder support before starting. The canvas
renderer is shared by preview and browser export. AAC is preferred; browsers
without AAC encoding use Opus in MP4 and show a compatibility notice. Background
export is the most compatible choice. Keep the editor open for a browser export.

The server uses FFmpeg/libass with the same caption renderer as clip generation,
while browser export uses canvas/WebCodecs. Both consume the same cut, word, style,
framing, audio, and effects document. Font rasterization and color filtering can
differ slightly between rendering engines, and power-word emphasis and emojis are
only drawn by the server export.

With dynamic edits, the background export renders `document.fx` through the
same three-process chain as generation (ffmpeg, the deno compositor, ffmpeg).
The browser export draws effects too, but refuses documents with a time warp,
reactions or behind-the-head accents (the export panel says why); those need the
background export. The browser export has no loudness stage or 720p cap.

## Persistence and operation

No additional service is required. Deploy the frontend, API, and worker together
and restart the worker so it registers `prepare_editor`, `export_editor`, and
`combine_editor` (and, for dynamic edits, `editor_fx_analysis` on the video
worker and `edit_fx_plan_job` on the AI worker).

Editor state is in PostgreSQL, one session per task, clip and clip filename:

- `editor_sessions`: preparation state and media metadata, the initial editing
  baseline, and the current draft with its optimistic revision number.
- `editor_jobs`: export and combine jobs with the document they render, their
  status, progress, and cancel request.

Editor media is separate. `clean.mp4`, thumbnails and export MP4s are stored
under `media/editor/<session>/` in the bucket when object storage is on, and
under `TEMP_DIR/editor/<session>/` otherwise. With object storage that directory
is only a cache on whichever machine rendered last, so the API and workers do
not need to share it. With local storage they must share `TEMP_DIR`, on one
machine.

Drafts are covered by the database backup. Sessions written as files by earlier
releases (`draft.json`, `original.json`, `asset.json`, `job-*.json`) are imported
the first time the clip is opened; after that the files are ignored. Undo history
and saved caption presets are local to the browser; the current draft is shared
across devices. A changed original filename creates a new editing baseline rather
than applying old offsets to different media.

Preparation may redownload a YouTube source that generation cleanup removed.
Unavailable sources show a retryable error; the editor does not overlay a second
set of captions on an already captioned clip. Preparation and export require a
working ARQ worker and Redis connection.

## API

All routes require ownership of both the task and clip.

| Route suffix under `/tasks/{task}/clips/{clip}/editor` | Method | Purpose |
| --- | --- | --- |
| `/` (without trailing slash) | GET | Preparation state, original, draft, media metadata, recent exports |
| `/prepare` | POST | Queue source preparation |
| `/` (without trailing slash) | PATCH | Save `{basis, revision, document}`; stale writes return 409 |
| `/media/clean.mp4` | GET | Private preview with byte-range support |
| `/media/thumbnails.jpg` | GET | Timeline thumbnail strip |
| `/exports` | POST | Queue an immutable `{basis, preset, document}` snapshot |
| `/exports/{job}` | GET / DELETE | Poll / request cancellation |
| `/exports/{job}/file` | GET | Download a completed export |
| `/fx/resolve` | POST | `{document, style}` → `{document}`: re-resolve effects (sync, no media work). 409 `NEEDS_PLAN` when the clip has no beats |
| `/fx/plan` | POST | Queue beat tagging on the AI worker → `{job_id}` (202) |
| `/fx/plan/{job}` | GET | `{status, beats?, error?}`; a request with no answer after 3 × `EDIT_PLAN_TIMEOUT_SECONDS` reports `WORKER_UNAVAILABLE` and is refunded |
| `/fx/analysis` | POST | Queue face tracking and the person matte for the session (202) |
| `/media/matte.mp4` | GET | The person matte for behind-the-head previews |
| `/fx/reaction/{cue}` | GET | A reaction GIF clip for the preview; 404 when unavailable |

Effects routes need the `dynamic_edits` flag only when they turn effects on.

`POST /tasks/{task}/editor/combine` takes ordered `clip_ids` and returns a background
job ID plus its anchor clip ID. Completion includes the new `clip_id`.

Legacy committed trim/split/merge/caption APIs remain available to API/MCP clients.
