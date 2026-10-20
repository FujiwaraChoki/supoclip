import Link from "next/link";
import { getTranslations } from "next-intl/server";

import { ArticleShell, CodeBlock, type ArticleFaq } from "@/components/articles/article-shell";
import type { BlogPost } from "@/lib/blog-posts";
import { OPUSCLIP_COMPARISON_HREF } from "@/lib/marketing-links";
import { GITHUB_URL } from "@/lib/site";

const docs = (path: string) => `${GITHUB_URL}/blob/main/${path}`;
const services = ["frontend", "backend", "worker", "postgres", "redis", "mcp"] as const;
const tocIds = ["stack", "requirements", "install", "local", "verify", "production", "upgrade", "faq"] as const;

export async function SelfHostingGuideArticle({ post }: { post: BlogPost }) {
  const t = await getTranslations("blog");
  const slug = "self-host-supoclip-docker";
  const code = (chunks: React.ReactNode) => <code>{chunks}</code>;
  const strong = (chunks: React.ReactNode) => <strong>{chunks}</strong>;
  const faqs: ArticleFaq[] = [0, 1, 2, 3, 4].map((index) => ({
    question: t(`${slug}.faqs.${index}.question`),
    answer: t(`${slug}.faqs.${index}.answer`),
  }));
  const toc: Array<[string, string]> = tocIds.map((id) => [id, t(`${slug}.toc.${id}`)]);

  return (
    <ArticleShell post={post} breadcrumb={t(`${slug}.breadcrumb`)} toc={toc} faqs={faqs}>
      <p>
        {t.rich(`${slug}.intro`, { repo: (chunks) => <a href={GITHUB_URL}>{chunks}</a> })}
      </p>
      <p>
        {t.rich(`${slug}.introChoice`, {
          hosted: (chunks) => <Link href="/sign-up">{chunks}</Link>,
          comparison: (chunks) => <Link href={OPUSCLIP_COMPARISON_HREF}>{chunks}</Link>,
        })}
      </p>

      <h2 id="stack">{t(`${slug}.stack.heading`)}</h2>
      <p>
        {t.rich(`${slug}.stack.lead`, {
          code,
          compose: (chunks) => <a href={docs("docker-compose.yml")}>{chunks}</a>,
        })}
      </p>
      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full min-w-[520px] text-left text-sm leading-6">
          <caption className="sr-only">{t(`${slug}.stack.caption`)}</caption>
          <thead className="bg-muted">
            <tr>
              <th scope="col" className="p-3">{t(`${slug}.stack.service`)}</th>
              <th scope="col" className="p-3">{t(`${slug}.stack.role`)}</th>
              <th scope="col" className="p-3">{t(`${slug}.stack.port`)}</th>
            </tr>
          </thead>
          <tbody>
            {services.map((service) => (
              <tr key={service} className="border-t">
                <th scope="row" className="p-3 align-top font-mono text-xs">{service}</th>
                <td className="p-3 align-top text-muted-foreground">{t(`${slug}.stack.rows.${service}.role`)}</td>
                <td className="p-3 align-top text-muted-foreground">{t(`${slug}.stack.rows.${service}.port`)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p>{t.rich(`${slug}.stack.after`, { code })}</p>

      <h2 id="requirements">{t(`${slug}.requirements.heading`)}</h2>
      <ul>
        <li>{t.rich(`${slug}.requirements.li1`, { strong, code })}</li>
        <li>{t.rich(`${slug}.requirements.li2`, { strong, code })}</li>
        <li>{t.rich(`${slug}.requirements.li3`, { strong, code })}</li>
        <li>{t.rich(`${slug}.requirements.li4`, { strong, code })}</li>
      </ul>
      <p>
        {t.rich(`${slug}.requirements.note`, { quickstart: (chunks) => <a href={docs("QUICKSTART.md")}>{chunks}</a> })}
      </p>

      <h2 id="install">{t(`${slug}.install.heading`)}</h2>
      <h3>{t(`${slug}.install.clone`)}</h3>
      <CodeBlock label={t(`${slug}.install.labelTerminal`)}>{`
git clone https://github.com/FujiwaraChoki/supoclip.git
cd supoclip
cp .env.example .env
`}</CodeBlock>
      <h3>{t.rich(`${slug}.install.providersHeading`, { code })}</h3>
      <p>{t.rich(`${slug}.install.providersBody`, { code })}</p>
      <CodeBlock label={t(`${slug}.install.labelEnv`)}>{`
TRANSCRIPTION_PROVIDER=assemblyai
ASSEMBLY_AI_API_KEY=your_assemblyai_key

LLM=google-gla:gemini-3-flash-preview
GOOGLE_API_KEY=your_google_key

# Replace every placeholder secret, even for local use
BETTER_AUTH_SECRET=generate_with_openssl_rand_base64_32
BACKEND_AUTH_SECRET=generate_another_one
APP_SETTINGS_ENCRYPTION_KEY=and_another_one
`}</CodeBlock>
      <p>{t.rich(`${slug}.install.secrets`, { code })}</p>
      <h3>{t(`${slug}.install.build`)}</h3>
      <CodeBlock label={t(`${slug}.install.labelTerminal`)}>{`
docker compose up -d --build
# or: ./start.sh  (checks .env and Docker first, then does the same)
`}</CodeBlock>
      <p>{t.rich(`${slug}.install.open`, { code })}</p>

      <h2 id="local">{t(`${slug}.local.heading`)}</h2>
      <p>{t(`${slug}.local.intro`)}</p>
      <CodeBlock label={t(`${slug}.install.labelEnv`)}>{`
TRANSCRIPTION_PROVIDER=whisper
WHISPER_MODEL_SIZE=medium        # tiny | base | small | medium | large | large-v3

LLM=ollama:gpt-oss:20b
# Empty = http://host.docker.internal:11434/v1 inside the containers.
# If you set it yourself, keep the /v1 suffix.
OLLAMA_BASE_URL=
`}</CodeBlock>
      <p>{t(`${slug}.local.worker`)}</p>
      <h3>{t(`${slug}.local.desktopHeading`)}</h3>
      <p>{t.rich(`${slug}.local.desktop`, { code })}</p>
      <h3>{t(`${slug}.local.linuxHeading`)}</h3>
      <p>{t.rich(`${slug}.local.linux`, { code })}</p>
      <CodeBlock label={t(`${slug}.install.labelTerminal`)}>{`
sudo systemctl edit ollama
#   [Service]
#   Environment="OLLAMA_HOST=0.0.0.0:11434"
sudo systemctl restart ollama
ollama pull gpt-oss:20b
`}</CodeBlock>
      <p>{t(`${slug}.local.expose`)}</p>
      <h3>{t(`${slug}.local.containerHeading`)}</h3>
      <p>{t(`${slug}.local.container`)}</p>
      <CodeBlock label={t(`${slug}.local.labelOverride`)}>{`
services:
  ollama:
    image: ollama/ollama
    volumes:
      - ollama_models:/root/.ollama
    restart: unless-stopped

volumes:
  ollama_models:
`}</CodeBlock>
      <CodeBlock label={t(`${slug}.local.labelEnvTerminal`)}>{`
OLLAMA_BASE_URL=http://ollama:11434/v1

docker compose up -d ollama
docker compose exec ollama ollama pull gpt-oss:20b
docker compose up -d backend worker   # pick up the new .env value
`}</CodeBlock>
      <p>{t.rich(`${slug}.local.whichever`, { code })}</p>
      <CodeBlock label={t(`${slug}.install.labelTerminal`)}>{`
# Host Ollama:
docker compose exec worker curl -s http://host.docker.internal:11434/api/tags | grep -q '"gpt-oss:20b"' && echo "model ready"
# Ollama container:
docker compose exec worker curl -s http://ollama:11434/api/tags | grep -q '"gpt-oss:20b"' && echo "model ready"
`}</CodeBlock>
      <p>{t.rich(`${slug}.local.ready`, { code })}</p>

      <h2 id="verify">{t(`${slug}.verify.heading`)}</h2>
      <CodeBlock label={t(`${slug}.install.labelTerminal`)}>{`
docker compose ps                       # every service should be "healthy"
curl -f http://localhost:8000/health/db # backend can reach Postgres
docker compose logs -f worker           # watch a job move through the pipeline
`}</CodeBlock>
      <ol>
        <li>{t(`${slug}.verify.li1`)}</li>
        <li>{t(`${slug}.verify.li2`)}</li>
        <li>{t(`${slug}.verify.li3`)}</li>
      </ol>
      <p>
        {t.rich(`${slug}.verify.stuck`, { guide: (chunks) => <a href={docs("docs/troubleshooting.md")}>{chunks}</a> })}
      </p>

      <h2 id="production">{t(`${slug}.production.heading`)}</h2>
      <p>{t(`${slug}.production.intro`)}</p>
      <ul>
        {(["li1", "li2", "li3", "li4", "li5", "li6", "li7", "li8"] as const).map((key) => (
          <li key={key}>{t.rich(`${slug}.production.${key}`, { strong, code })}</li>
        ))}
      </ul>
      <CodeBlock label={t(`${slug}.production.labelReset`)}>{`
services:
  frontend:
    volumes: !reset []   # the runner image already contains the build
`}</CodeBlock>
      <p>{t.rich(`${slug}.production.passwordIntro`, { code })}</p>
      <CodeBlock label={t(`${slug}.production.labelPassword`)}>{`
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
      <p>{t.rich(`${slug}.production.existing`, { code })}</p>
      <CodeBlock label={t(`${slug}.production.labelExisting`)}>{`
docker compose exec -T postgres psql -U supoclip -d supoclip \\
  -c "ALTER USER supoclip PASSWORD 'your_new_password'"
docker compose up -d
`}</CodeBlock>
      <p>
        {t.rich(`${slug}.production.docs`, {
          config: (chunks) => <a href={docs("docs/configuration.md")}>{chunks}</a>,
          setup: (chunks) => <a href={docs("docs/setup.md")}>{chunks}</a>,
        })}
      </p>

      <h2 id="upgrade">{t(`${slug}.upgrade.heading`)}</h2>
      <p>{t.rich(`${slug}.upgrade.intro`, { code })}</p>
      <CodeBlock label={t(`${slug}.install.labelTerminal`)}>{`
docker compose exec -T postgres pg_dump -U supoclip supoclip > supoclip-$(date +%F).sql
docker compose stop frontend backend worker
old=$(git rev-parse HEAD)
git pull
git diff --name-only --diff-filter=A "$old" HEAD -- \\
  'backend/migrations/*.sql' 'frontend/prisma/migrations/*/migration.sql'
`}</CodeBlock>
      <p>{t(`${slug}.upgrade.apply`)}</p>
      <CodeBlock label={t(`${slug}.install.labelTerminal`)}>{`
docker compose exec -T postgres psql -U supoclip -d supoclip -v ON_ERROR_STOP=1 -1 \\
  < frontend/prisma/migrations/<new_migration>/migration.sql
docker compose up -d --build
`}</CodeBlock>
      <p>
        {t.rich(`${slug}.upgrade.prisma`, { code })}{" "}
        <code>prisma migrate resolve --applied &lt;migration&gt;</code>
        {t(`${slug}.upgrade.prismaAfter`)}
      </p>
      <p>{t.rich(`${slug}.upgrade.volumes`, { code })}</p>
      <p>
        {t.rich(`${slug}.upgrade.customize`, {
          code,
          api: (chunks) => <a href={docs("docs/api-reference.md")}>{chunks}</a>,
        })}
      </p>
    </ArticleShell>
  );
}
