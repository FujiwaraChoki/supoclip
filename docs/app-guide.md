# App Guide

This guide explains the visible parts of SupoClip and the main user flows.

## Product Summary

SupoClip turns long-form videos into short clips. Users can:

- Submit a YouTube URL
- Upload a video file
- Customize subtitles and styling
- Track processing progress in real time
- Review and edit generated clips
- Download clips for publishing

By default (`REQUIRE_AUTH=false`) SupoClip runs local-first: there is no login, no `/sign-in`/`/sign-up`, and no admin dashboard — every request resolves to a single implicit local user. Setting `REQUIRE_AUTH=true` (hosted deployments) restores multi-tenant auth and, depending on configuration, hosted billing and usage limits.

## Main Screens

### Home: `/`

The homepage is the main task creation screen.

Core behaviors:

- Accepts either a YouTube URL or an uploaded file
- Shows a YouTube thumbnail preview when the URL can be parsed
- Lets users set clip styling defaults before submission
- Starts the job and transitions the user into task tracking

Available creation options on this screen include:

- Source type
  - YouTube
  - Upload
- Font family
- Font size
- Font color
- Caption template
- Include B-roll
- Output format
  - `vertical`
  - `original`
- Subtitle toggle

Additional behavior:

- Loads available fonts from the backend
- Loads caption templates from the backend
- Checks whether B-roll is configured
- Loads the latest task for the signed-in user
- Loads billing summary when monetization is enabled

If landing-only mode is enabled, the homepage can act more like a marketing shell than the full product workspace.

### Task List: `/list`

The task list is the user’s history and control center.

Users can:

- View all tasks
- See status at a glance
- Select multiple tasks
- Cancel active tasks
- Resume errored or cancelled tasks
- Delete tasks in bulk

Status states used across the UI include:

- `queued`
- `processing`
- `completed`
- `error`
- `cancelled`

### Task Detail: `/tasks/[id]`

This is the main post-submission workspace.

When a task is still running:

- The page connects to Server-Sent Events on `GET /tasks/{id}/progress`
- Progress updates appear in real time
- The page refreshes when processing completes

When a task is completed:

- Generated clips are displayed
- Users can preview videos
- Virality scores and reasoning are visible
- Users can edit, split, merge, regenerate, and export clips

Editing actions exposed in the UI map to backend operations:

- Rename or update task metadata
- Delete a clip
- Trim a clip
- Split a clip at a chosen timestamp
- Merge selected clips
- Update captions
- Regenerate a clip
- Apply project-wide style settings to a task
- Export with a platform preset such as TikTok

### Settings: `/settings`

The settings page stores user defaults and exposes billing actions when monetization is enabled. It's organized into Transcription, Hooks, Export, UI (subtitle appearance), Notifications, Advanced, Developer, and Billing sections.

Users can:

- Set default font family
- Set default font size
- Set default font color
- Open checkout or billing portal flows (hosted mode only)
- Sign out (hosted mode only)

The page also loads billing summary data so the user can see plan and usage information.

**Every non-secret setting shows its current effective value** (admin-saved or env-sourced) next to its label — only API keys stay hidden. Changes **auto-save** a moment after you stop editing; there's no separate "Save" button to remember to click. The per-task "Project Settings" panel on a task page works the same way for that task's own styling — settings persist automatically, while the "Apply to All Clips" button remains a separate, explicit action since it re-renders every clip and can take a few minutes.

### Auth and Admin (Hosted Mode Only)

There are currently no `/sign-in`, `/sign-up`, or `/admin` pages in the frontend — those are legacy/hosted-mode concerns that have been removed from the self-hosted app. In hosted mode (`REQUIRE_AUTH=true`), SupoClip uses Better Auth with an email and password flow backed by PostgreSQL through Prisma, and `DISABLE_SIGN_UP=true` disables sign-up for new users.

### Feedback

The app includes feedback submission plumbing via frontend API routes and backend handling. This can be wired to Discord webhooks when configured.

## Core User Workflows

### 1. Create a task

1. Open `/` (no sign-in required by default).
2. Choose YouTube or upload mode.
3. Configure caption and styling preferences, including the Retention tab's target clip length and clip count (both are hints the AI selection step aims for, not hard guarantees).
4. Submit the task.

The frontend sends the request through its API routes, and the backend creates a task record plus a queued background job.

**Batch uploads**: dropping or selecting more than one file in Upload mode queues all of them — they upload and get created as separate tasks one at a time (sequentially, so a large batch doesn't saturate the upload endpoint), each with its own per-item status in the queue list, then all land in `/list` where the worker processes them independently.

### 2. Monitor progress

1. Open the task page.
2. Watch live progress from the backend SSE stream — a stage stepper (Download → Transcribe → Analyze → Render → Done) plus a percentage bar, and per-clip "rendering clip i/N" status as each clip is produced.
3. Wait for the status to move from `queued` to `processing` to `completed`.
4. The task list (`/list`) also shows a live thumbnail (for YouTube sources), inline progress bar, and current stage message for any in-progress task, refreshing automatically every few seconds.

### 3. Review clips

Once completed, the task page shows:

- Clip duration
- Transcript excerpt
- AI reasoning
- Virality scoring
- Video preview and download access

### 4. Edit clips

Users can refine clips after generation:

- Trim start or end offsets
- Split long clips into smaller ones
- Merge multiple clips
- Rewrite captions
- Reapply task-wide subtitle styling
- Export platform-specific versions

### 5. Manage billing

If monetization is enabled:

- Free and paid-plan limits can affect whether task creation is allowed
- The homepage and settings page surface billing state
- Users can open checkout or the customer portal from the frontend

## Supported Inputs

### YouTube

The app can accept standard YouTube URLs, including:

- `youtube.com/watch?v=...`
- `youtu.be/...`
- Embed-style YouTube links

The system attempts to extract the video ID on the frontend for previews and on the backend for downloading.

### File upload

Users can upload local video files through the frontend upload API. The backend stores the upload in the temporary working area before processing.

## Customization Features

### Fonts

Available fonts come from backend-managed files. The frontend fetches the list from `/api/fonts`.

Custom font upload is also supported through the media API. In monetized setups, access rules may differ from self-host mode.

### Caption templates

Caption templates are exposed by the backend and loaded into the frontend task creation and editing flows.

### Hook titles

Every clip gets an AI-written on-screen headline ("hook") burned in for its first few seconds. From the task detail page you can:

- Style the hook (font, size, color, background box, position, drop shadow) with a live CSS-approximation preview.
- Pick an animation style: Fade + Pop, Fade, Slide Down, Zoom Punch, Bounce, or Pulse.
- Compare hooks (per clip, via the "Compare Hooks" button): generate a few AI-written alternative hook titles for that clip, preview them side-by-side, and pick a winner — or write your own custom hook text. Applying a hook re-renders just that clip (the hook text is burned into the same frame as the crop/captions, so it can't be swapped without a re-render).
- Optionally override the hook's content-type label (Question, Bold Statement, Data/Stats, Story, Contrast, Callout, Warning, or None) when applying a hook.

`GET /api/tasks/hook-options` lists the current selectable hook types and animation styles.

### B-roll

B-roll is optional and depends on `PEXELS_API_KEY`. The frontend checks whether it is available before presenting it as a real option.

### Output format

The home screen exposes at least:

- Vertical output
- Original-aspect output

The rendering path then uses backend processing rules to produce the final clip file.

## Hosted Versus Self-Hosted Behavior

### Self-host mode

When `SELF_HOST=true`:

- Monetization is disabled
- Billing-related friction is minimized
- The app behaves like an open-source self-hosted tool

### Hosted mode

When `SELF_HOST=false`:

- Billing summary endpoints matter
- Usage limits can block new task creation
- Stripe checkout and portal flows are active
- Subscription emails can be sent via Amazon SES

## User Roles

### Standard user

Can:

- Sign in
- Create and manage their own tasks
- Edit and export their own clips
- Save personal style defaults

### Admin user

Can also:

- Access `/admin`
- Promote or demote admin status for other users
- Monitor global task activity

## Practical Support Notes

If a user says:

- "My clips never show up"
  - Check worker health, Redis, and task status
- "I can’t see my fonts"
  - Check `/api/fonts`, mounted font files, and auth mode
- "I can’t create new tasks"
  - Check billing summary, plan limits, and API keys

## Related Reading

- [Setup](./setup.md)
- [Architecture](./architecture.md)
- [API Reference](./api-reference.md)
- [Troubleshooting](./troubleshooting.md)
