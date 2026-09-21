<p align="center">
  <a href="https://www.supoclip.com">
    <img src="assets/banner.png" alt="SupoClip" width="100%" />
  </a>
</p>

<h3 align="center">Fuck OpusClip.</h3>

<p align="center">
  ... because good video clips shouldn't come with ugly watermarks or platform lock-in.
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-blue.svg" alt="License: AGPL-3.0" /></a>
  <a href="https://www.supoclip.com"><img src="https://img.shields.io/badge/hosted-supoclip.com-black.svg" alt="Hosted at supoclip.com" /></a>
  <a href="docs/README.md"><img src="https://img.shields.io/badge/docs-docs%2F-green.svg" alt="Documentation" /></a>
</p>

<p align="center">
  <a href="https://apps.apple.com/us/app/supoclip/id6784760040">
    <img src="frontend/public/app-store-badge.svg" alt="Download SupoClip on the App Store" height="40" />
  </a>
</p>

---

SupoClip is an open-source, AI-powered video clipping tool. Give it a long video — a podcast, a talk, a stream VOD — and it finds the most viral-worthy moments, scores them, and renders them as vertical 9:16 clips with face-centered cropping, word-synced subtitles, hook titles, and optional B-roll. Run it yourself, customize it, inspect it — or use the hosted version and skip the setup.

## Ways to Use SupoClip

| | |
|---|---|
| **Hosted web app** | [www.supoclip.com](https://www.supoclip.com) — no infrastructure to run |
| **iOS app** | [SupoClip on the App Store](https://apps.apple.com/us/app/supoclip/id6784760040) — the same hosted pipeline, from your iPhone |
| **Self-host** | Docker Compose setup below — AGPL-3.0, unlimited usage on your own hardware |
| **MCP server** | [`mcp/`](mcp/) — use SupoClip from Claude, Cursor, and other MCP clients |
| **REST API** | API keys from `/settings/api-keys` authenticate the backend directly — see the [API reference](docs/api-reference.md) |

## Why SupoClip Exists

OpusClip is genuinely good at what it does — AI clip selection, accurate captions, virality scoring. But your usage is metered by plan, some exports carry platform branding, and your content and workflows live on their servers under their terms.

SupoClip gives you the same core pipeline without the leash:

- **Self-hostable** — run it on your own hardware, process as much as it can handle
- **No watermarks** — your content stays yours
- **Open source** — AGPL-3.0, full transparency, fork and extend it however you like
- **Hosted option** — when you'd rather not manage servers, the cloud version is there

## Features

- **AI clip selection** — an LLM (Gemini, GPT, Claude, or a local Ollama model) picks the most clip-worthy segments from the transcript; set a target clip count and length per video, or leave it on auto
- **Batch processing** — drop multiple videos at once, pick a preset once, and they queue up and process one after another; pause, resume, cancel, or retry a failed video without losing the rest, and the queue survives a restart
- **Content policy detection** — sensitive words are flagged and asterisked in captions (never in audio) using an editable, per-category word list, with an optional local-LLM pass for euphemisms a keyword list would miss
- **Per-clip SEO metadata** — an AI-generated title, description, and tags for every clip, auto-generated in one call right after clip detection (toggle in Settings, on by default; manual "Regenerate" always available), fully editable and never regenerated over a manual edit. Shown per-clip on the project page with copy-one/copy-all buttons, a stale badge when the clip's been re-cut since generation, and an export-all-metadata download
- **Virality scoring** — every clip gets hook, engagement, value, and shareability scores
- **Smart vertical cropping** — face detection keeps the speaker centered in the 9:16 frame
- **Word-synced subtitles** — AssemblyAI word-level timestamps, custom fonts, caption templates with animation styles
- **Hook titles** — an AI-written headline burned into the top of each clip's opening seconds, with selectable animation styles and per-clip A/B comparison to generate and pick between alternative hooks
- **B-roll & transitions** — optional Pexels stock footage overlays and transition effects
- **Built-in editor** — trim, split, and merge clips, adjust caption size with a live preview, then export with platform presets (TikTok, Instagram Reels, YouTube Shorts, Facebook Reels, Threads), each with its own duration cap, safe-area margins, and loudness target, and clips auto-capped at 300MB without sacrificing quality unless needed
- **Safe Zone Overlay** — an optional preview-only guide showing where each platform's own UI (username, captions, like/comment/share rail) will sit over your clip, per-platform or all at once, so you can see if your hook/captions would get covered before exporting; toggle persists per project
- **Export All Clips** — export every clip in a project in one click, with per-clip progress, automatic one-time retry on failure, and a final success/failure report
- **Emoji reactions** — drop emoji reactions at any point on a clip's timeline, pick an animation style, duration, and position, and they're burned into the re-rendered clip in full color
- **Reusable settings templates** — save a project's font/caption/hook/B-roll/cleanup/export settings as a named template, then replace or merge it onto any other project
- **Real-time progress** — a stage-by-stage pipeline view (download → transcribe → analyze → render) with per-clip status, elapsed time, and an ETA once one is actually known, streamed live to the browser
- **Optional GPU-accelerated rendering** — enable hardware encoding in Settings; it's automatically disabled with an explanation if no supported GPU is detected
- **Recoverable deletes** — deleting a project moves it to Trash (the source video is never touched); restore it or delete it forever
- **Light/dark theme** — follows your system preference by default, toggle persists across sessions
- **Built as a platform** — a tool tab bar sits above the product screens; Clipping and Ranking are the first two tools, with the same tab-bar shell ready to host future tools (a voiceover/animation tool, etc.)
- **Ranking tool** — point at a folder of short clips (via a folder picker or drag-and-drop; flat, no nesting, MP4/MOV/MKV/WEBM, at least 5 clips), auto-select 5 at random (preferring clips you haven't used in a previous ranking from that folder), swap any of them, and write a short line of text per rank — reused automatically next time that same clip comes up. Renders one 9:16 compilation with all 5 ranks always visible on the left, each rank's text revealing (with a subtle bounce) as its clip plays and then staying on screen, #1 in gold; non-9:16 clips fill the frame with a blurred version of themselves by default rather than cropping the action out. An optional transition SFX plays before each cut and is timed so its final play ends exactly when the video does — no black frame. Four templates ship (Rapid Fire, Countdown, Ranking List, and the folder-workflow's Classic Ranking); exportable with the same platform presets as Clipping

## Quick Start

You need Docker, an [AssemblyAI](https://www.assemblyai.com/) API key for transcription, and one LLM provider key (Google, OpenAI, Anthropic, or a local Ollama).

```bash
git clone https://github.com/FujiwaraChoki/supoclip.git
cd supoclip
```

Create a `.env` in the root with your keys:

```env
ASSEMBLY_AI_API_KEY=your_assemblyai_api_key
LLM=google-gla:gemini-3-flash-preview
GOOGLE_API_KEY=your_google_api_key
```

Then start everything:

```bash
docker-compose up -d
```

First startup takes a few minutes; watch it with `docker-compose logs -f`. Once healthy, open [http://localhost:3001](http://localhost:3001) and start clipping — SupoClip runs local-first by default, with no login required. The backend API lives at [http://localhost:8000](http://localhost:8000) with interactive docs at `/docs`.

To use a different LLM provider, self-host with Ollama, or configure the optional pieces (B-roll, analytics, emails, YouTube metadata), see the [configuration guide](docs/configuration.md). If something misbehaves, the [troubleshooting guide](docs/troubleshooting.md) covers the common failure modes.

## Local LLM Setup

Content-policy detection and metadata generation (title/description/tag suggestions) run on a **local Ollama model by default** — free, private, and unlimited — with Gemini Flash-Lite as an optional opt-in fallback for machines without a GPU.

**Install Ollama:**

| OS | Command |
|----|---------|
| Linux | `curl -fsSL https://ollama.com/install.sh \| sh` |
| macOS | `brew install ollama` (or download the app from [ollama.com](https://ollama.com/download)) |
| Windows | `winget install --id Ollama.Ollama -e` (or download `OllamaSetup.exe` from ollama.com) |

Then pull a model:

```bash
ollama pull llama3.2:3b   # balanced default
ollama pull gemma2:2b     # fastest, lowest VRAM
ollama pull qwen2.5:3b    # most reliable JSON-mode output
```

Set `OLLAMA_KEEP_ALIVE=30s` in the environment Ollama runs in so it unloads the model after 30 seconds idle, freeing VRAM for video rendering between LLM calls (see [CLAUDE.md](CLAUDE.md#local-llm-ollama) for the per-OS mechanism).

**Provider choice**: in Settings → LLM Provider, choose `Ollama (local)`, `Gemini`, or `Hybrid` (Ollama-first, Gemini fallback). Gemini reuses your existing Google API key — set `GOOGLE_API_KEY` in `.env` or Settings and it's available as a fallback the moment Ollama is unreachable, provided you've opted into Hybrid/Gemini mode.

**Content policy behavior**: flagged words are asterisked in captions (first letter preserved, e.g. "cocaine" → "c\*\*\*\*\*e") — audio is never censored. Sensitivity is configurable per project (Off/Low/Medium/High) and the word lists per category (sex, drugs, violence, profanity) are user-editable in Settings.

**Batch workflow**: drop multiple videos into "New Clip" to queue them for sequential processing. Pick a preset (or your last-used one) once — it applies to the whole batch. Progress, pause/resume/cancel, and retry are all per-item as well as for the whole queue; a queue survives an app/backend restart and offers to resume on reopen.

## Documentation

Everything beyond this page lives in [`docs/`](docs/README.md):

| Guide | What it covers |
|---|---|
| [Setup](docs/setup.md) | Docker-first install, local development, first-run checklist |
| [Configuration](docs/configuration.md) | Every environment variable, operating modes, provider options |
| [App Guide](docs/app-guide.md) | Screens, workflows, admin features, hosted vs self-host |
| [Architecture](docs/architecture.md) | How the frontend, API, worker, queue, and pipeline fit together |
| [API Reference](docs/api-reference.md) | Backend endpoints, API keys, admin and billing routes |
| [Development](docs/development.md) | Running locally without Docker, testing, contributing workflow |
| [Troubleshooting](docs/troubleshooting.md) | Fixes for the common ways things go wrong |

## Development & Testing

The stack runs locally without Docker too — see [development](docs/development.md) for the backend (`uv` + FastAPI + ARQ worker) and frontend (Next.js + pnpm) commands. The test suite spans pytest, Vitest, and Playwright, all reachable from the repo root:

```bash
make test
```

## License

SupoClip is released under the [AGPL-3.0 License](LICENSE).

Contributions are accepted under the terms in [CONTRIBUTING.md](CONTRIBUTING.md), including a license grant that allows the project owner to sublicense and relicense contributed code.
