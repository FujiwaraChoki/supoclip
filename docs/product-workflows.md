# Product workflows

SupoClip now includes the higher-level production workflows around clipping. Advanced timeline/editor controls remain separate.

## Clip generation

- Add a natural-language clip brief, keywords, target count/duration, and an optional source timeframe.
- Choose transcript or multimodal analysis. Multimodal mode combines transcript/audio signals with scene, motion, and face-change signals.
- Select a workspace and reusable brand kit when starting a generation.
- Paste YouTube, Vimeo, Twitch, Google Drive, Dropbox, Loom, Zoom, or StreamYard URLs, or upload a local video.

## Localization and visual workflows

Open **Workflows** on a completed task to:

- Generate translated caption variants.
- Generate dubbed variants through OpenAI speech. Dubbed outputs are marked with `AI voice` metadata.
- Search Pexels, add B-roll, edit each insertion's start time and duration, remove items, and render a non-destructive variant.
- Group clips into collections.
- Download ZIP, SRT, CSV, Final Cut XML, or EDL packages.

Open **Settings → Integrations & workflows** to create brand kits and upload logo/music assets. Selected logos and music are applied during rendering; font/color/caption defaults are applied when the task is created.

## Publishing

The publishing workflow supports YouTube, TikTok, Instagram Reels, and Facebook Pages. SupoClip generates grounded titles, descriptions, and hashtags from each clip transcript, then publishes immediately or schedules through the worker. YouTube uploads use the resumable upload protocol, TikTok uploads are chunked, and expiring YouTube/TikTok access tokens are refreshed before scheduled publishing. TikTok uploads remain in `publishing` until the worker confirms `PUBLISH_COMPLETE`; provider rejections are persisted without creating duplicate uploads.

Configure the provider applications with these callback URLs:

```text
${BACKEND_PUBLIC_URL}/social/oauth/youtube/callback
${BACKEND_PUBLIC_URL}/social/oauth/tiktok/callback
${BACKEND_PUBLIC_URL}/social/oauth/instagram/callback
${BACKEND_PUBLIC_URL}/social/oauth/facebook/callback
```

Required environment variables:

```env
BACKEND_PUBLIC_URL=https://api.example.com
GOOGLE_OAUTH_CLIENT_ID=
GOOGLE_OAUTH_CLIENT_SECRET=
TIKTOK_CLIENT_KEY=
TIKTOK_CLIENT_SECRET=
META_APP_ID=
META_APP_SECRET=
PEXELS_API_KEY=
```

Google must have YouTube Data API v3 enabled. Meta requires a Facebook Page; Instagram publishing additionally requires a connected professional Instagram account. TikTok requires the Content Posting API scopes configured in the app review console.

For local Google OAuth setup, use `http://localhost:8000/social/oauth/youtube/callback` as the authorized redirect URI and set `BACKEND_PUBLIC_URL=http://localhost:8000`.

Google OAuth branding is shared across every OAuth client in a Cloud project. Use a SupoClip-only Google Cloud project before production launch if the existing project has another product name or logo.

## Teams, imports, and automation

- Workspace owners/admins can invite members as admin, editor, member, or viewer. Invitation links bind to the invited email.
- YouTube channel subscriptions are checked every ten minutes. Unseen entries create queued clipping tasks subject to the normal billing/usage guard.
- Scheduled posts are checked every minute.
- Webhooks use `X-SupoClip-Signature: v1=<sha256>` over `<timestamp>.<raw-body>`, and persist delivery/retry state.
- `task.completed` is emitted after a successful worker run.

## Verification baseline

Run these checks before release:

```bash
cd backend && uv run pytest --no-cov tests/unit
cd frontend && pnpm run lint && pnpm exec tsc --noEmit && pnpm run build
cd frontend && pnpm exec prisma validate
docker compose ps
```

The workflow unit suite covers prompt controls, multimodal prompt wiring, external-source validation, YouTube feed parsing, localization/dubbing orchestration, brand media passes, B-roll timing, workspace viewer permissions, all four publishing adapters, TikTok status reconciliation, professional export formats, and webhook signatures.
