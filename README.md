# Fuck OpusClip.

... because good video clips shouldn't come with ugly watermarks or platform lock-in.

<p align="center">
  <a href="https://www.supoclip.com">
    <img src="assets/banner.png" alt="SupoClip Banner" width="100%" />
  </a>
</p>

SupoClip gives you AI-powered video clipping capabilities in an open-source package you can run yourself, customize, and inspect. Use the hosted version when you want the convenience of managed infrastructure, or self-host when you want full control.

---

<p align="center">
  <a href="https://www.atlascloud.ai/?utm_source=github&utm_medium=link&utm_campaign=supoclip">
    <img src="assets/atlas-cloud-logo.png" alt="Atlas Cloud" width="200">
  </a>
</p>

> 🎁 **[Atlas Cloud](https://www.atlascloud.ai/?utm_source=github&utm_medium=link&utm_campaign=supoclip)** is a full-modal, OpenAI-compatible AI inference platform. SupoClip's transcript analysis runs on any LLM, so you can plug Atlas in as a drop-in backend and pick from DeepSeek, Qwen, GLM, Kimi, MiniMax, Claude, GPT and Gemini through a single API — no per-vendor setup. Budget-friendly plans: [coding plan](https://www.atlascloud.ai/console/coding-plan).

```env
# Use Atlas Cloud for AI analysis (OpenAI-compatible)
LLM=atlas:deepseek-ai/deepseek-v4-pro
ATLASCLOUD_API_KEY=your_atlascloud_api_key
# Optional, defaults to https://api.atlascloud.ai/v1
# ATLASCLOUD_BASE_URL=https://api.atlascloud.ai/v1
```

`deepseek-ai/deepseek-v4-pro` is a reasoning model; SupoClip already requests enough tokens for the structured JSON analysis.

<details>
<summary>All Atlas Cloud chat models (59)</summary>

- **Anthropic (Claude):** `anthropic/claude-haiku-4.5-20251001`, `anthropic/claude-opus-4.8`, `anthropic/claude-sonnet-4.6`
- **OpenAI (GPT):** `openai/gpt-5.4`, `openai/gpt-5.5`
- **Google (Gemini):** `google/gemini-3.1-flash-lite`, `google/gemini-3.1-pro-preview`, `google/gemini-3.5-flash`
- **Alibaba Qwen:** `qwen/qwen2.5-7b-instruct`, `Qwen/Qwen3-235B-A22B-Instruct-2507`, `qwen/qwen3-235b-a22b-thinking-2507`, `qwen/qwen3-30b-a3b`, `Qwen/Qwen3-30B-A3B-Instruct-2507`, `qwen/qwen3-30b-a3b-thinking-2507`, `qwen/qwen3-32b`, `qwen/qwen3-8b`, `Qwen/Qwen3-Coder`, `qwen/qwen3-coder-next`, `qwen/qwen3-max-2026-01-23`, `Qwen/Qwen3-Next-80B-A3B-Instruct`, `Qwen/Qwen3-Next-80B-A3B-Thinking`, `Qwen/Qwen3-VL-235B-A22B-Instruct`, `qwen/qwen3-vl-235b-a22b-thinking`, `qwen/qwen3-vl-30b-a3b-instruct`, `qwen/qwen3-vl-30b-a3b-thinking`, `qwen/qwen3-vl-8b-instruct`, `qwen/qwen3.5-122b-a10b`, `qwen/qwen3.5-27b`, `qwen/qwen3.5-35b-a3b`, `qwen/qwen3.5-397b-a17b`, `qwen/qwen3.6-35b-a3b`, `qwen/qwen3.6-plus`
- **DeepSeek:** `deepseek-ai/deepseek-ocr`, `deepseek-ai/deepseek-r1-0528`, `deepseek-ai/DeepSeek-V3-0324`, `deepseek-ai/DeepSeek-V3.1`, `deepseek-ai/DeepSeek-V3.1-Terminus`, `deepseek-ai/deepseek-v3.2`, `deepseek-ai/DeepSeek-V3.2-Exp`, `deepseek-ai/deepseek-v4-flash`, `deepseek-ai/deepseek-v4-pro`
- **Moonshot (Kimi):** `moonshotai/Kimi-K2-Instruct`, `moonshotai/Kimi-K2-Instruct-0905`, `moonshotai/Kimi-K2-Thinking`, `moonshotai/kimi-k2.5`, `moonshotai/kimi-k2.6`
- **Zhipu GLM:** `zai-org/GLM-4.6`, `zai-org/glm-4.7`, `zai-org/glm-5`, `zai-org/glm-5-turbo`, `zai-org/glm-5.1`, `zai-org/glm-5v-turbo`
- **MiniMax:** `MiniMaxAI/MiniMax-M2`, `minimaxai/minimax-m2.1`, `minimaxai/minimax-m2.5`, `minimaxai/minimax-m2.7`
- **xAI:** `xai/grok-4.3`
- **Kuaishou KAT:** `kwaipilot/kat-coder-pro-v2`
- **Other:** `owl`

</details>

---

> For the hosted version, sign up for the waitlist here: [SupoClip Hosted](https://www.supoclip.com)

## Why SupoClip Exists

### The OpusClip Problem

OpusClip is undeniably powerful. It's an AI video clipping tool that can turn long-form content into viral short clips with features like:

- AI-powered clip generation from long videos
- Automated captions with 97%+ accuracy
- Virality scoring to predict viral potential
- Multi-language support (20+ languages)
- Brand templates and customization

**But here's the catch:**

- **Usage limits**: Processing minutes are capped by plan
- **Watermarks**: Some exports can include platform branding
- **Processing limits**: Even paid plans have strict minute limits
- **Vendor lock-in**: Your content and workflows are tied to their platform

### The SupoClip Solution

SupoClip provides the same core functionality with more control:

→ ✅ **Self-Hostable** - Run it on your own infrastructure

→ ✅ **No Watermarks** - Your content stays yours

→ ✅ **Open Source** - Full transparency, community-driven development

→ ✅ **Hosted Option** - Use SupoClip without managing servers

→ ✅ **Unlimited Usage** - Process as many videos as your hardware can handle

→ ✅ **Customizable** - Modify and extend the codebase to fit your needs

## Quick Start

### Prerequisites

- Docker and Docker Compose
- An AssemblyAI API key (for transcription) - [Get one here](https://www.assemblyai.com/)
- An LLM provider for AI analysis - OpenAI, Google, Anthropic, Atlas Cloud, or Ollama

### 1. Clone and Configure

```bash
git clone https://github.com/FujiwaraChoki/supoclip.git
cd supoclip
```

Create a `.env` file in the root directory:

```env
# Required: Video transcription
ASSEMBLY_AI_API_KEY=your_assemblyai_api_key

# Required: Choose ONE LLM provider and set its API key
# Option A: Google Gemini (recommended - fast & cost-effective)
LLM=google-gla:gemini-3-flash-preview
GOOGLE_API_KEY=your_google_api_key

# Option B: OpenAI GPT-5.2 (best reasoning)
# LLM=openai:gpt-5.2
# OPENAI_API_KEY=your_openai_api_key

# Option C: Anthropic Claude
# LLM=anthropic:claude-4-sonnet
# ANTHROPIC_API_KEY=your_anthropic_api_key

# Option D: Ollama (local/self-hosted)
# LLM=ollama:gpt-oss:20b
# OLLAMA_BASE_URL=  # Optional; defaults to localhost locally, host.docker.internal in Docker
# OLLAMA_API_KEY=your_ollama_api_key  # Optional (Ollama Cloud)

# Option E: Atlas Cloud (OpenAI-compatible; 59+ models via one API)
# LLM=atlas:deepseek-ai/deepseek-v4-pro
# ATLASCLOUD_API_KEY=your_atlascloud_api_key
# ATLASCLOUD_BASE_URL=  # Optional; defaults to https://api.atlascloud.ai/v1

# Optional: Auth secret (change in production)
BETTER_AUTH_SECRET=change_this_in_production

# Optional: DataFast analytics
# Track your deployed domain in DataFast
# NEXT_PUBLIC_DATAFAST_WEBSITE_ID=dfid_xxxxx
# NEXT_PUBLIC_DATAFAST_DOMAIN=your-domain.com
# NEXT_PUBLIC_DATAFAST_ALLOW_LOCALHOST=false

# Optional: Resend for waitlist confirmation emails
# RESEND_API_KEY=your_resend_api_key

# Optional: YouTube metadata provider
# `yt_dlp` preserves the existing metadata behavior
# `youtube_data_api` uses the official API first, then falls back to yt-dlp
# YOUTUBE_METADATA_PROVIDER=yt_dlp
# YOUTUBE_DATA_API_KEY=your_youtube_data_api_key
```

### 2. Start the Services

```bash
docker-compose up -d
```

This starts:
- **Frontend**: http://localhost:3000
- **Backend API**: http://localhost:8000 (docs at /docs)
- **PostgreSQL**: localhost:5432
- **Redis**: localhost:6379

### 3. Wait for Initialization

First-time startup takes a few minutes. Check progress with:

```bash
docker-compose logs -f
```

Wait until you see health checks passing for all services.

### 4. Access the App

Open http://localhost:3000 in your browser, create an account, and start clipping!

If you enable DataFast, also verify that:
- `/js/script.js` loads from your own app domain
- `/api/events` requests are proxied through your app domain
- custom goals appear after successful sign-up, sign-in, task creation, billing, feedback, or waitlist actions

### Troubleshooting

**Backend fails to start with API key error:**
- Make sure you've set the correct LLM provider AND its corresponding API key in `.env`
- Default is `google-gla:gemini-3-flash-preview` which requires `GOOGLE_API_KEY`
- If using `openai:gpt-5.2`, you MUST set `OPENAI_API_KEY`
- If using `ollama:*`, run Ollama and optionally set `OLLAMA_BASE_URL`
  (`http://localhost:11434/v1` for local backend runs, `http://host.docker.internal:11434/v1` for Docker)
- Rebuild after changing `.env`: `docker-compose up -d --build`

**Videos stay queued / never process:**
- Check worker logs: `docker-compose logs -f worker`
- Ensure Redis is healthy: `docker-compose logs redis`
- Verify API keys are correct

**YouTube titles or duration lookup is failing:**
- `YOUTUBE_METADATA_PROVIDER=yt_dlp` keeps the old metadata path
- `YOUTUBE_METADATA_PROVIDER=youtube_data_api` requires YouTube Data API v3 enabled in Google Cloud
- Prefer `YOUTUBE_DATA_API_KEY`; if it is unset, the backend will try `GOOGLE_API_KEY`
- The backend will automatically fall back to the other metadata provider if the primary one fails
- `videos.list` costs 1 quota unit per request

**Performance tuning (default is fast mode):**
- `DEFAULT_PROCESSING_MODE=fast|balanced|quality`
- `FAST_MODE_MAX_CLIPS=4` to cap clip count in fast mode
- `FAST_MODE_TRANSCRIPT_MODEL=nano` for fastest transcript model
- View aggregate metrics: `GET /tasks/metrics/performance`

**Prisma errors on Windows:**
- Run `docker-compose down -v` to clear volumes
- Run `docker-compose up -d --build` to rebuild

**Frontend shows database errors:**
- Wait for PostgreSQL to fully initialize (check logs)
- The database is automatically created on first run

**Font picker is empty / cannot select or upload fonts:**
- Add fonts to `backend/fonts/` – see [backend/fonts/README.md](backend/fonts/README.md) for TikTok Sans and custom fonts
- Ensure `BACKEND_AUTH_SECRET` is set in `.env` when using the hosted/monetized setup
- Font upload is Pro-only when monetization is enabled; self-hosted users can upload freely

**Subscription emails are not sending:**
- Set `RESEND_API_KEY` and `RESEND_FROM_EMAIL` in `.env`
- `RESEND_FROM_EMAIL` must be a verified sender/domain in your Resend account
- The backend sends the “thank you for subscribing” email on `checkout.session.completed`
- The backend sends the “sorry to see you go” email on `customer.subscription.deleted`

## Testing

SupoClip now has a layered automated test setup:

- `pytest` for backend unit and integration tests
- `Vitest` and Testing Library for frontend route and component coverage
- `Playwright` for a small seeded browser smoke suite

Repo-level entrypoints:

```bash
make test
make test-backend
make test-frontend
make test-e2e
make test-ci
```

App-level entrypoints:

```bash
cd backend && uv sync --all-groups && .venv/bin/pytest
cd frontend && npm install && npm run test:coverage
cd frontend && npm run test:e2e
```

Local test runs expect PostgreSQL and Redis to be available. The easiest path is to start the stack with `docker-compose up -d`, then run the commands above. CI runs the same layers in GitHub Actions with Postgres and Redis service containers.

## Documentation

Detailed documentation now lives in [`docs/`](docs/README.md).

Start with:

- [`docs/setup.md`](docs/setup.md)
- [`docs/configuration.md`](docs/configuration.md)
- [`docs/app-guide.md`](docs/app-guide.md)
- [`docs/architecture.md`](docs/architecture.md)
- [`docs/api-reference.md`](docs/api-reference.md)
- [`docs/development.md`](docs/development.md)
- [`docs/troubleshooting.md`](docs/troubleshooting.md)

## Hosted Billing Emails

When you run SupoClip with monetization enabled (`SELF_HOST=false`), subscription lifecycle emails are sent through Resend by the backend:

- `checkout.session.completed` sends the thank-you-for-subscribing email
- `customer.subscription.deleted` sends the sorry-to-see-you-go email

Required env vars for this flow:

- `RESEND_API_KEY`
- `RESEND_FROM_EMAIL`
- `BACKEND_AUTH_SECRET`
- `STRIPE_SECRET_KEY`
- `STRIPE_WEBHOOK_SECRET`
- `STRIPE_PRICE_ID`

### Local Development (Without Docker)

See [CLAUDE.md](CLAUDE.md) for detailed development instructions.

## License

SupoClip is released under the AGPL-3.0 License. See [LICENSE](LICENSE) for details.
