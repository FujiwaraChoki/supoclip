import Link from "next/link";

import { ArticleShell, CodeBlock, type ArticleFaq } from "@/components/articles/article-shell";
import type { BlogPost } from "@/lib/blog-posts";
import { OPUSCLIP_COMPARISON_HREF } from "@/lib/marketing-links";
import { GITHUB_URL } from "@/lib/site";

const docs = (path: string) => `${GITHUB_URL}/blob/main/${path}`;

const faqs: ArticleFaq[] = [
  {
    question: "Is self-hosted SupoClip free?",
    answer:
      "The code is free under AGPL-3.0 and self-host mode has no generation limits or paywall. You still pay for your own hardware or server, and for any cloud transcription or LLM usage you configure. A Whisper + Ollama setup avoids per-request AI fees but needs enough local compute.",
  },
  {
    question: "Can SupoClip run without any cloud AI services?",
    answer:
      "Yes, for clip analysis. Set TRANSCRIPTION_PROVIDER=whisper to transcribe locally and LLM=ollama:<model> to select clips with a local model. Downloading YouTube links still needs internet access; uploaded files do not. Optional B-roll uses the Pexels API.",
  },
  {
    question: "Do I need a GPU?",
    answer:
      "No. The .env.example template sets BACKEND_CPU_ONLY=true, which installs CPU-only PyTorch. CPU-only Whisper transcription is slower than a cloud API, so long videos take longer on modest hardware.",
  },
  {
    question: "Does it work on Apple Silicon?",
    answer:
      "Yes, with a caveat: the backend and worker images run as linux/amd64 under emulation. BACKEND_CPU_ONLY=true avoids NVIDIA downloads, but it does not enable Apple GPU acceleration.",
  },
  {
    question: "How do I keep my instance private?",
    answer:
      "Create your account, then set DISABLE_SIGN_UP=true and recreate the frontend container. Compose binds every port to 127.0.0.1, so nothing is public until you add a reverse proxy.",
  },
];

const toc: Array<[string, string]> = [
  ["stack", "What you run"],
  ["requirements", "Requirements"],
  ["install", "Install"],
  ["local", "Fully local AI"],
  ["verify", "Verify"],
  ["production", "Production checklist"],
  ["upgrade", "Upgrades & backups"],
  ["faq", "FAQ"],
];

export function SelfHostingGuideArticle({ post }: { post: BlogPost }) {
  return (
    <ArticleShell post={post} breadcrumb="Self-host SupoClip" toc={toc} faqs={faqs}>
      <p>
        SupoClip is the open-source AI video clipper behind the hosted app at supoclip.com. The same repository
        ships a Docker Compose stack you can run on a laptop, a home server, or a VPS. This guide follows that stack
        exactly as it exists in the <a href={GITHUB_URL}>repository</a>. Where a step depends on your provider or
        hardware, we say so instead of guessing.
      </p>
      <p>
        Prefer not to run infrastructure? The <Link href="/sign-up">hosted app</Link> runs the same pipeline. Still
        deciding between tools? Read our <Link href={OPUSCLIP_COMPARISON_HREF}>SupoClip vs OpusClip comparison</Link> first.
      </p>

      <h2 id="stack">What you are running</h2>
      <p>
        <code>docker compose up</code> starts these containers, all defined in <a href={docs("docker-compose.yml")}>docker-compose.yml</a>:
      </p>
      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full min-w-[520px] text-left text-sm leading-6">
          <caption className="sr-only">Services in the SupoClip Docker Compose stack</caption>
          <thead className="bg-muted"><tr><th scope="col" className="p-3">Service</th><th scope="col" className="p-3">Role</th><th scope="col" className="p-3">Host port</th></tr></thead>
          <tbody>
            {[
              ["frontend", "Next.js web app, auth, and API proxy", "FRONTEND_PORT (3107 in .env.example; 3001 if unset)"],
              ["backend", "FastAPI API; docs at /docs", "8000"],
              ["worker", "ARQ worker: download, transcribe, analyze, render", "none"],
              ["postgres", "PostgreSQL 15: users, tasks, clips", "none"],
              ["redis", "Redis 7: job queue and live progress", "6379"],
              ["mcp", "MCP server for AI clients (optional to keep running)", "9100"],
            ].map(([service, role, port]) => (
              <tr key={service} className="border-t"><th scope="row" className="p-3 align-top font-mono text-xs">{service}</th><td className="p-3 align-top text-muted-foreground">{role}</td><td className="p-3 align-top text-muted-foreground">{port}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      <p>
        Every published port is bound to <code>127.0.0.1</code>, so a fresh install is reachable only from the machine
        itself. Generated clips, uploads, Postgres, and Redis are stored in named Docker volumes, so they survive container restarts.
        If you don&apos;t use MCP, stop that container with <code>docker compose stop mcp</code>.
      </p>

      <h2 id="requirements">Requirements</h2>
      <ul>
        <li><strong>Docker</strong> with Compose v2 (<code>docker compose</code>) or the standalone <code>docker-compose</code>, plus Git.</li>
        <li>
          <strong>A transcription source</strong>, set with <code>TRANSCRIPTION_PROVIDER</code>:
          <code>assemblyai</code> (default, needs <code>ASSEMBLY_AI_API_KEY</code>), <code>whisper</code> (local, no key),
          or <code>youtube_captions</code> (YouTube links only).
        </li>
        <li>
          <strong>An LLM for clip selection</strong>, set with <code>LLM=provider:model</code>: OpenAI, Google, Anthropic,
          OpenRouter (each needs its API key), or a local <code>ollama:</code> model.
        </li>
        <li><strong>Optional:</strong> <code>PEXELS_API_KEY</code> for B-roll overlays.</li>
      </ul>
      <p>
        The first image build downloads Python and Node dependencies. The <a href={docs("QUICKSTART.md")}>quick start</a> estimates
        5–10 minutes; it varies with your connection and CPU.
      </p>

      <h2 id="install">Install with Docker Compose</h2>
      <h3>1. Clone and create your environment file</h3>
      <CodeBlock label="terminal">{`
git clone https://github.com/FujiwaraChoki/supoclip.git
cd supoclip
cp .env.example .env
`}</CodeBlock>
      <h3>2. Pick providers in <code>.env</code></h3>
      <p>
        The template defaults to <code>LLM=openrouter:anthropic/claude-sonnet-5.5</code>. Processing fails unless you set{" "}
        <code>OPENROUTER_API_KEY</code> or point <code>LLM</code> at a provider you have a key for. A minimal cloud setup looks like this:
      </p>
      <CodeBlock label=".env">{`
TRANSCRIPTION_PROVIDER=assemblyai
ASSEMBLY_AI_API_KEY=your_assemblyai_key

LLM=google-gla:gemini-3-flash-preview
GOOGLE_API_KEY=your_google_key

# Replace every placeholder secret, even for local use
BETTER_AUTH_SECRET=generate_with_openssl_rand_base64_32
BACKEND_AUTH_SECRET=generate_another_one
APP_SETTINGS_ENCRYPTION_KEY=and_another_one
`}</CodeBlock>
      <p>
        Generate each secret with <code>openssl rand -base64 32</code>. <code>SELF_HOST=true</code> (the default) keeps billing off and
        removes per-account generation limits.
      </p>
      <h3>3. Build and start</h3>
      <CodeBlock label="terminal">{`
docker compose up -d --build
# or: ./start.sh  (checks .env and Docker first, then does the same)
`}</CodeBlock>
      <p>
        Open <code>http://localhost:3107</code>, create an account, and paste a YouTube link or upload a file.
        API docs are at <code>http://localhost:8000/docs</code>.
      </p>

      <h2 id="local">Option: fully local transcription and clip selection</h2>
      <p>
        To keep transcripts and analysis on your own hardware, combine local Whisper with an Ollama model:
      </p>
      <CodeBlock label=".env">{`
TRANSCRIPTION_PROVIDER=whisper
WHISPER_MODEL_SIZE=medium        # tiny | base | small | medium | large | large-v3

LLM=ollama:gpt-oss:20b
# Empty = http://host.docker.internal:11434/v1 inside the containers.
# If you set it yourself, keep the /v1 suffix.
OLLAMA_BASE_URL=
`}</CodeBlock>
      <p>
        The worker makes the Ollama calls from inside its container, so Ollama must be reachable from there, not just
        from your shell. How you get there depends on where Ollama runs:
      </p>
      <h3>Ollama on the host, with Docker Desktop (macOS, Windows)</h3>
      <p>
        No extra setup. Docker Desktop forwards <code>host.docker.internal</code> to the host, so leave{" "}
        <code>OLLAMA_BASE_URL</code> empty and pull the model with <code>ollama pull gpt-oss:20b</code>.
      </p>
      <h3>Ollama on the host, with Docker Engine (Linux)</h3>
      <p>
        Compose maps <code>host.docker.internal</code> to the Docker bridge gateway, but Ollama listens only on{" "}
        <code>127.0.0.1</code> by default, so the worker&apos;s connection is refused. Make Ollama listen on all interfaces:
      </p>
      <CodeBlock label="terminal">{`
sudo systemctl edit ollama
#   [Service]
#   Environment="OLLAMA_HOST=0.0.0.0:11434"
sudo systemctl restart ollama
ollama pull gpt-oss:20b
`}</CodeBlock>
      <p>
        That also exposes port 11434 on your network interfaces. Ollama has no authentication, so block that port from
        outside the machine with your firewall. If your firewall filters traffic from Docker networks, also allow the
        containers to reach it.
      </p>
      <h3>Ollama in its own container</h3>
      <p>
        Add it to the stack with an override file, then point SupoClip at the service name instead of the host:
      </p>
      <CodeBlock label="docker-compose.override.yml (merge into any override you already have)">{`
services:
  ollama:
    image: ollama/ollama
    volumes:
      - ollama_models:/root/.ollama
    restart: unless-stopped

volumes:
  ollama_models:
`}</CodeBlock>
      <CodeBlock label=".env and terminal">{`
OLLAMA_BASE_URL=http://ollama:11434/v1

docker compose up -d ollama
docker compose exec ollama ollama pull gpt-oss:20b
docker compose up -d backend worker   # pick up the new .env value
`}</CodeBlock>
      <p>
        Whichever setup you choose, test from inside the worker before submitting a video. The check looks for{" "}
        <code>gpt-oss:20b</code> because that is the model in the example <code>LLM</code> value. If you chose another model,
        replace it with the exact name after <code>ollama:</code> in your <code>LLM</code> setting. Ollama lists a model pulled
        without a tag as <code>name:latest</code>, so use that form in the check.
      </p>
      <CodeBlock label="terminal">{`
# Host Ollama:
docker compose exec worker curl -s http://host.docker.internal:11434/api/tags | grep -q '"gpt-oss:20b"' && echo "model ready"
# Ollama container:
docker compose exec worker curl -s http://ollama:11434/api/tags | grep -q '"gpt-oss:20b"' && echo "model ready"
`}</CodeBlock>
      <p>
        <code>model ready</code> means the worker can reach Ollama and the model you searched for is installed. No
        output means either the connection failed or the model is missing, so rerun the command without the{" "}
        <code>grep</code> to see which. Smaller Whisper models trade accuracy for speed,
        and clip quality depends on the LLM. Compare a local model against a hosted one on a recording you know before
        switching your whole backlog.
      </p>

      <h2 id="verify">Verify the install</h2>
      <CodeBlock label="terminal">{`
docker compose ps                       # every service should be "healthy"
curl -f http://localhost:8000/health/db # backend can reach Postgres
docker compose logs -f worker           # watch a job move through the pipeline
`}</CodeBlock>
      <ol>
        <li>Create an account and submit a short video first, so a misconfiguration fails fast.</li>
        <li>Confirm the task page shows progress updates. If it stays queued, check the worker logs.</li>
        <li>Play and download a finished clip.</li>
      </ol>
      <p>
        Stuck tasks are usually a missing provider key or a worker that cannot reach Redis. The{" "}
        <a href={docs("docs/troubleshooting.md")}>troubleshooting guide</a> covers the common cases.
      </p>

      <h2 id="production">Production checklist</h2>
      <p>The Compose file is tuned for getting started. Before putting it on a public domain:</p>
      <ul>
        <li>
          <strong>Build the production frontend.</strong> Compose defaults to the hot-reloading <code>development</code> target
          and mounts <code>frontend/src</code> into the container. Set <code>FRONTEND_BUILD_TARGET=runner</code> and{" "}
          <code>NODE_ENV=production</code> in <code>.env</code>, then drop the source mounts with the override below.
        </li>
        <li>
          <strong>Terminate HTTPS at a reverse proxy</strong> (Caddy, nginx, Traefik) in front of the frontend port.
        </li>
        <li>
          <strong>Expose the backend for uploads.</strong> With <code>BACKEND_AUTH_SECRET</code> set, browsers upload video files
          directly to <code>NEXT_PUBLIC_API_URL</code>. Point that at a public HTTPS backend URL, and add your frontend origin to{" "}
          <code>CORS_ORIGINS</code>.
        </li>
        <li>
          <strong>Set your public origin everywhere:</strong> <code>NEXT_PUBLIC_APP_URL</code>, <code>BETTER_AUTH_URL</code>, and{" "}
          <code>CORS_ORIGINS</code>. <code>NEXT_PUBLIC_*</code> values are baked in at build time, so rebuild the frontend after changing them.
        </li>
        <li>
          <strong>Change the database password.</strong> <code>docker-compose.yml</code> hardcodes <code>supoclip_password</code> in
          four places: <code>POSTGRES_PASSWORD</code> in the <code>postgres</code> service and <code>DATABASE_URL</code> in the{" "}
          <code>frontend</code>, <code>backend</code>, and <code>worker</code> services. Editing <code>POSTGRES_PASSWORD</code> in{" "}
          <code>.env</code> alone changes none of them. Override all four, as shown below.
        </li>
        <li><strong>Set <code>REDIS_PASSWORD</code></strong>; the Redis container enables AUTH when it is non-empty.</li>
        <li><strong>Close sign-ups</strong> with <code>DISABLE_SIGN_UP=true</code> if the instance is just for your team.</li>
        <li>
          <strong>Review the limits:</strong> <code>MAX_VIDEO_DURATION</code> (default 5400 s, 90 minutes) and{" "}
          <code>MAX_VIDEO_UPLOAD_BYTES</code> (about 12 GB).
        </li>
      </ul>
      <CodeBlock label="docker-compose.override.yml (Compose v2.24+)">{`
services:
  frontend:
    volumes: !reset []   # the runner image already contains the build
`}</CodeBlock>
      <p>
        For the database password, put a new value in <code>POSTGRES_PASSWORD</code> in <code>.env</code>. Generate it with{" "}
        <code>openssl rand -hex 24</code>, because hex needs no escaping inside a URL. Then point all four settings at it in the
        same override file:
      </p>
      <CodeBlock label="docker-compose.override.yml (merge into any override you already have)">{`
services:
  postgres:
    environment:
      POSTGRES_PASSWORD: \${POSTGRES_PASSWORD}
  frontend:
    environment:
      DATABASE_URL: postgresql://supoclip:\${POSTGRES_PASSWORD}@postgres:5432/supoclip
  backend:
    environment:
      DATABASE_URL: postgresql+asyncpg://supoclip:\${POSTGRES_PASSWORD}@postgres:5432/supoclip
  worker:
    environment:
      DATABASE_URL: postgresql+asyncpg://supoclip:\${POSTGRES_PASSWORD}@postgres:5432/supoclip
`}</CodeBlock>
      <p>
        Postgres reads <code>POSTGRES_PASSWORD</code> only when it initializes an empty data volume. On an install that already
        has a database, changing that variable does not change the actual password. Rotate it inside Postgres first, using
        the same value as in <code>.env</code>. Then recreate the services so they use the new URLs:
      </p>
      <CodeBlock label="terminal (existing database)">{`
docker compose exec -T postgres psql -U supoclip -d supoclip \\
  -c "ALTER USER supoclip PASSWORD 'your_new_password'"
docker compose up -d
`}</CodeBlock>
      <p>
        <a href={docs("docs/configuration.md")}>docs/configuration.md</a> lists every variable, and{" "}
        <a href={docs("docs/setup.md")}>docs/setup.md</a> covers custom ports and public URLs.
      </p>

      <h2 id="upgrade">Upgrades and backups</h2>
      <p>
        <code>init.sql</code> creates the schema only when the Postgres volume is first initialized. After that, nothing
        updates the schema automatically. If a new version adds files under <code>frontend/prisma/migrations</code> or{" "}
        <code>backend/migrations</code>, apply them yourself before you restart the app. New code running against the old
        schema fails on missing columns and tables. Back up first, and stop the app services so they don&apos;t pick up new
        code early. The development frontend hot-reloads mounted source.
      </p>
      <CodeBlock label="terminal">{`
docker compose exec -T postgres pg_dump -U supoclip supoclip > supoclip-$(date +%F).sql
docker compose stop frontend backend worker
old=$(git rev-parse HEAD)
git pull
git diff --name-only --diff-filter=A "$old" HEAD -- \\
  'backend/migrations/*.sql' 'frontend/prisma/migrations/*/migration.sql'
`}</CodeBlock>
      <p>
        Read each listed file, then apply it in a single transaction. Apply Prisma migrations in timestamp order and backend
        migrations in number order:
      </p>
      <CodeBlock label="terminal">{`
docker compose exec -T postgres psql -U supoclip -d supoclip -v ON_ERROR_STOP=1 -1 \\
  < frontend/prisma/migrations/<new_migration>/migration.sql
docker compose up -d --build
`}</CodeBlock>
      <p>
        A database created by <code>init.sql</code> has no Prisma migration history. Don&apos;t run{" "}
        <code>prisma migrate deploy</code> against it: Prisma would replay every migration from the first one and fail on
        tables that already exist. If you want Prisma to manage migrations from now on, first baseline it with{" "}
        <code>prisma migrate resolve --applied &lt;migration&gt;</code>, but only for migrations whose changes are already in
        your schema. Prisma permanently skips any migration you mark as applied, even if its changes are missing.
      </p>
      <p>
        Back up the <code>clips</code> and <code>uploads</code> volumes too if you need to keep rendered videos. Never run{" "}
        <code>docker compose down -v</code> on an instance you care about: it deletes every volume, including the database.
      </p>
      <p>
        Want to customize the output? Drop <code>.ttf</code> fonts into <code>backend/fonts/</code> and <code>.mp4</code>{" "}
        transitions into <code>backend/transitions/</code>. They appear in the app automatically. For automation, create an
        API key in Settings and use the <a href={docs("docs/api-reference.md")}>REST API</a> or the bundled MCP server.
      </p>
    </ArticleShell>
  );
}
