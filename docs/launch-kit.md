# Launch Kit

Everything needed to announce SupoClip and measure whether each channel produces
activated users (people who export a clip), not just visits.

- [Pages to link](#pages-to-link)
- [Claims: what we can and can't say](#claims-what-we-can-and-cant-say)
- [Campaign links](#campaign-links)
- [Channel copy](#channel-copy)
- [Demo video script](#demo-video-script)
- [Launch checklist](#launch-checklist)
- [Measuring results](#measuring-results)

## Pages to link

| Page | URL | Use it for |
|------|-----|------------|
| Home | `https://www.supoclip.com/` | Product Hunt, general announcements |
| Demo | `https://www.supoclip.com/demo` | Social posts: real output, with timestamps back to the source |
| Self-hosting guide | `https://www.supoclip.com/blog/self-host-supoclip-docker` | r/selfhosted, developer communities |
| SupoClip vs OpusClip | `https://www.supoclip.com/blog/supoclip-vs-opusclip` | "OpusClip alternative" threads and questions |
| Open-source clipper | `https://www.supoclip.com/open-source-video-clipper` | Open-source communities |
| GitHub | `https://github.com/FujiwaraChoki/supoclip` | Hacker News, developer audiences |

**Visual assets in the repo**

- `assets/banner.jpg`: README cover / social header
- `frontend/public/blog/supoclip-editor.webp`: real editor screenshot (sample test footage)
- `frontend/public/blog/supoclip-open-source-clipping.webp`: illustration (AI-generated, label it as such)
- `frontend/public/clips/demo-{1,2}.mp4` + `.jpg`: demo clip previews

> **Rights note:** the demo clips come from a third-party interview
> ([source](https://www.youtube.com/watch?v=Vv3CEAS_w34)). They're shown on
> supoclip.com with attribution and links to the source. **Don't upload them as
> native posts to TikTok, Reels, Shorts, or X.** For native video, record the
> demo below using footage we own.

## Claims: what we can and can't say

Use these claims freely. Each one is backed by the code or the site:

| Claim | Source |
|-------|--------|
| Open source under AGPL-3.0 | `LICENSE` |
| Runs hosted, on iOS, or self-hosted with Docker Compose | `docker-compose.yml`, App Store listing |
| Self-hosted installs have no plan limits | `billing_service.py` (`self_host` plan) |
| No watermark on exports | No watermark step anywhere in `backend/src` |
| Bring your own AI: OpenAI, Google, Anthropic, OpenRouter, or local Ollama | `docs/configuration.md` |
| Transcription can run locally with Whisper | `TRANSCRIPTION_PROVIDER=whisper` |
| Face-aware 9:16 reframing, word-synced captions, hook titles | `backend/src/media/` |
| Export presets for TikTok, Reels, and Shorts | `backend/src/clip_editor.py` |
| REST API with per-user keys, plus an MCP server | `docs/api-reference.md`, `mcp/README.md` |
| Hosted plans: Pro $10/mo (50 videos), Scale $50/mo (300 videos) | Pricing section on supoclip.com |

**Don't say:**

- "Free" without qualification. Hosted SupoClip has **no free plan**. Say "free to self-host."
- Any speed, accuracy, or quality number ("10x faster", "95% accurate", "better clips than OpusClip"). We haven't run a benchmark that supports one.
- That virality scores predict views. They rank candidates for review.
- OpusClip prices or credit amounts. They change; link to the [comparison](https://www.supoclip.com/blog/supoclip-vs-opusclip) instead.
- "Fully offline." Downloading YouTube links needs internet access, and optional B-roll uses Pexels.

## Campaign links

Referrers are captured automatically, so a link posted on Hacker News is
attributed to `news.ycombinator.com` without tags. Add UTM tags where referrers
get stripped (email, DMs, apps, QR codes) or where you want to compare
individual posts within one platform.

**Convention:** all lowercase, underscores, no spaces. SupoClip lowercases
values on capture anyway, so `Reddit` and `reddit` are counted together.

| Parameter | Meaning | Values |
|-----------|---------|--------|
| `utm_source` | Platform | `producthunt`, `reddit`, `x`, `linkedin`, `youtube`, `newsletter`, `github` |
| `utm_medium` | Channel type | `launch`, `community`, `social`, `email`, `video`, `referral` |
| `utm_campaign` | Initiative | `launch_2026_10` |
| `utm_content` | Specific placement | `r_selfhosted`, `thread_1`, `readme_badge`, … |
| `ref` | Partner/creator code | e.g. `ref=creatorname` |

**Ready-made links**

```text
Product Hunt   https://www.supoclip.com/?utm_source=producthunt&utm_medium=launch&utm_campaign=launch_2026_10
r/selfhosted   https://www.supoclip.com/blog/self-host-supoclip-docker?utm_source=reddit&utm_medium=community&utm_campaign=launch_2026_10&utm_content=r_selfhosted
r/opensource   https://www.supoclip.com/open-source-video-clipper?utm_source=reddit&utm_medium=community&utm_campaign=launch_2026_10&utm_content=r_opensource
X thread       https://www.supoclip.com/demo?utm_source=x&utm_medium=social&utm_campaign=launch_2026_10&utm_content=thread_1
LinkedIn       https://www.supoclip.com/demo?utm_source=linkedin&utm_medium=social&utm_campaign=launch_2026_10
Newsletter     https://www.supoclip.com/?utm_source=newsletter&utm_medium=email&utm_campaign=launch_2026_10
YouTube desc.  https://www.supoclip.com/demo?utm_source=youtube&utm_medium=video&utm_campaign=launch_2026_10
OpusClip Q&A   https://www.supoclip.com/blog/supoclip-vs-opusclip?utm_source=reddit&utm_medium=community&utm_campaign=launch_2026_10&utm_content=opusclip_thread
```

Hacker News: link the GitHub repo or `https://www.supoclip.com/` **without** tags.

## Channel copy

Drafts to adapt, not paste. Read each community's self-promotion rules before
posting, and reply to comments for the first few hours.

### Show HN

> **Show HN: SupoClip – open-source AI clipper that turns long videos into Shorts**
>
> SupoClip takes a YouTube link or uploaded video, transcribes it, has an LLM
> pick self-contained moments, and renders 9:16 clips with word-synced captions,
> a hook title, and face-aware framing.
>
> It's AGPL-3.0 and runs with `docker compose up`. You choose the models:
> OpenAI, Google, Anthropic, OpenRouter, or Ollama, plus AssemblyAI or local
> Whisper for transcription, so it can run without cloud AI. There's also a
> REST API and an MCP server, so agents can create and download clips.
>
> Stack: Next.js, FastAPI, ARQ workers on Redis, Postgres, FFmpeg.
> There's a paid hosted version for people who don't want to run it.
>
> Repo: https://github.com/FujiwaraChoki/supoclip
> Demo: https://www.supoclip.com/demo
>
> I'd love feedback on clip selection quality and the self-hosting experience.

### Product Hunt

- **Tagline (≤60 chars):** `Open-source AI clipper: long videos into captioned Shorts`
- **Description (≤260 chars):** `SupoClip finds the best moments in long videos and turns them into vertical, captioned clips for Shorts, Reels, and TikTok. Use the hosted app or self-host it for free with your own AI models, including fully local Whisper + Ollama.`
- **First comment:** why it's open source, what self-hosting unlocks, the hosted plans, and one specific question for feedback.
- **Gallery:** the demo video (below), the editor screenshot, the banner.

### r/selfhosted

> **SupoClip: self-hosted AI video clipper (long videos → vertical clips), Docker Compose, works with Whisper + Ollama**
>
> I build SupoClip, an AGPL-3.0 alternative to tools like OpusClip. It turns
> podcasts, streams, and talks into captioned 9:16 clips.
>
> - `docker compose up -d --build`: frontend, API, worker, Postgres, Redis
> - `TRANSCRIPTION_PROVIDER=whisper` + `LLM=ollama:<model>` keeps transcription and clip selection on your hardware
> - No plan limits or watermark when self-hosted
> - Ports bind to 127.0.0.1 by default; `DISABLE_SIGN_UP=true` locks it to your account
>
> The setup guide, including a production checklist, is here: [link with r_selfhosted tags]
> Known rough edges: CPU-only Whisper is slow on long videos, and Apple Silicon
> runs the backend under amd64 emulation.

### X thread

1. We open-sourced our AI video clipper. Paste a long video, get vertical, captioned clips with hook titles. Self-host it free, or use the hosted app. 🧵
2. Here's real output from a long-form interview, with links back to each source timestamp: [demo link]
3. Bring your own AI: OpenAI, Google, Anthropic, OpenRouter, or fully local Whisper + Ollama.
4. No watermark. No plan limits when self-hosted. AGPL-3.0: [GitHub link]
5. Comparing tools? Our SupoClip vs OpusClip comparison links a source for every claim: [comparison link]

Attach the demo video (own footage) to tweet 1, not the third-party demo clips.

### LinkedIn

> Most long-form video gets watched once. We built SupoClip to turn
> recordings like podcasts, webinars, and talks into short vertical clips with
> captions, so one recording becomes a week of posts.
>
> It's open source, so teams with data requirements can run it on their own
> infrastructure with their own AI models. There's also a hosted version.
>
> See real output: [demo link]

### Newsletter blurb

> **SupoClip is open source.** Turn long videos into captioned Shorts, Reels,
> and TikToks. Use the hosted app, or self-host it free with your own AI models.
> [See the demo →]

## Demo video script

Target: 60–75 seconds, 1080×1920 for social or 1920×1080 for Product Hunt and
YouTube. Record with **footage we own** (e.g. a SupoClip team talk or a
podcast episode with written permission).

| Time | Shot | Voiceover / caption |
|------|------|---------------------|
| 0–3s | Finished clip playing full screen, hook title visible | "This clip was made automatically from a 40-minute video." |
| 3–10s | Paste the YouTube URL on the home screen, pick a caption template, click Generate | "Paste a link or upload a file." |
| 10–20s | Task page: progress messages, then clips appearing (speed up the wait and **label it** "sped up") | "It transcribes, picks the strongest moments, and renders vertical clips." |
| 20–35s | Clip wall sorted by score; open one; show the score breakdown | "Each clip is scored on hook, engagement, value, and shareability, so you know what to review first." |
| 35–50s | Editor: trim, change caption style, preview | "Adjust timing and captions before exporting." |
| 50–60s | Export with the TikTok preset; download completes | "Export for TikTok, Reels, or Shorts. No watermark." |
| 60–70s | Terminal: `docker compose up -d --build`, then GitHub repo | "It's open source. Run it yourself, or use the hosted app." |
| End card | Logo + `supoclip.com/demo` | |

Don't show a processing time unless it's the real, unedited duration, and say
which hardware or plan produced it.

## Launch checklist

**Before launch day**

- [ ] Run the `user_acquisition` migration in production (see `DEPLOY_NOTES.md` pattern: `npx prisma migrate deploy` against the production DB), then deploy.
- [ ] Open a tagged link in a private window, sign up, export a clip, and confirm `signup_completed`, `clip_exported`, and `first_clip_exported` appear in DataFast with UTM metadata.
- [ ] Confirm the same test user has a `user_acquisition` row with `first_clip_exported_at` set (query below), then delete the test account.
- [ ] Re-check the two OpusClip help-center pages cited in the comparison still say what we quote.
- [ ] Submit the updated sitemap in Google Search Console and request indexing for `/demo`, the self-hosting guide, and the comparison.
- [ ] Record and caption the demo video; export one landscape and one vertical cut.

**Launch day**

- [ ] Post Product Hunt and Show HN early in the US morning, a few hours apart. Then post Reddit, X, and LinkedIn.
- [ ] Stay in the comments. Answer setup questions with links to the specific guide section.
- [ ] Watch worker health and queue depth; a launch spike is mostly hosted sign-ups.

**After 7 and 30 days**

- [ ] Run the activation query and compare channels on *export rate*, not sign-ups.
- [ ] Turn the most common questions into FAQ entries on the relevant guide.

## Measuring results

### How attribution works

1. On the first visit with a campaign signal (UTM tags, `?ref=`/`?via=`, or an
   external referrer), the browser stores it for 90 days. A later signal does
   not replace it (first touch). A direct visit is replaced by the first real
   source.
2. Within 24 hours of sign-up, it's saved once to the `user_acquisition` table.
   Older accounts are never re-attributed.
3. The first time the user downloads or exports a clip (task page or editor),
   `first_clip_exported_at` is set atomically and the browser sends
   `first_clip_exported` to DataFast, using the stored attribution even on a
   different device.

### DataFast goals

| Goal | When | Metadata |
|------|------|----------|
| `signup_completed` | Account created | `auth_method`, UTM fields, `ref`, `referrer_host` |
| `task_created` | Video submitted | processing options |
| `clip_exported` | Every download/export | `surface` (`task`, `editor`, `editor_queue`), `format`, UTM fields |
| `first_clip_exported` | First export per attributed user | as above + `hours_since_signup` |

### Activation by source (SQL)

```sql
-- docker exec -it supoclip-postgres psql -U supoclip -d supoclip
SELECT
  COALESCE(a.utm_source, a.referrer_host, '(direct)')            AS source,
  COALESCE(a.utm_campaign, '-')                                   AS campaign,
  COUNT(*)                                                        AS signups,
  COUNT(*) FILTER (WHERE u.plan IN ('pro', 'scale')
                     AND u.subscription_status IN ('active', 'trialing')) AS paying_now,
  COUNT(a.first_clip_exported_at)                                 AS exported_a_clip,
  ROUND(100.0 * COUNT(a.first_clip_exported_at) / COUNT(*), 1)    AS export_rate_pct,
  ROUND((PERCENTILE_CONT(0.5) WITHIN GROUP (
    ORDER BY EXTRACT(EPOCH FROM a.first_clip_exported_at - u."createdAt") / 3600
  ))::numeric, 1)                                                 AS median_hours_to_export
FROM user_acquisition a
JOIN users u ON u.id = a.user_id
WHERE a.created_at >= '2026-10-01'
GROUP BY 1, 2
ORDER BY signups DESC;
```

### Which landing pages convert

```sql
SELECT
  a.landing_path,
  COUNT(*)                        AS signups,
  COUNT(a.first_clip_exported_at) AS exported_a_clip
FROM user_acquisition a
WHERE a.created_at >= '2026-10-01'
GROUP BY 1
ORDER BY signups DESC
LIMIT 20;
```

Hosted processing requires a paid plan, so the hosted funnel is
**sign-up → subscribe → first export**. `paying_now` shows how many attributed
sign-ups currently pay. A low export rate alongside high `paying_now` points
to onboarding friction, not channel quality.
