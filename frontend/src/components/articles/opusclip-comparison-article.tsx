import Link from "next/link";

import { ArticleShell, type ArticleFaq } from "@/components/articles/article-shell";
import type { BlogPost } from "@/lib/blog-posts";
import { getPublicBillingPlans } from "@/lib/billing-plans";
import { SELF_HOSTING_GUIDE_HREF } from "@/lib/marketing-links";
import { GITHUB_URL } from "@/lib/site";

const source = (path: string) => `${GITHUB_URL}/blob/main/${path}`;

const OPUS_SOURCES = {
  plans: "https://help.opus.pro/docs/article/plans-and-credits",
  watermark: "https://help.opus.pro/docs/article/watermark",
};

const faqs: ArticleFaq[] = [
  {
    question: "Is SupoClip better than OpusClip?",
    answer:
      "It depends on what you need. SupoClip is the stronger fit if you want source access, self-hosting, or your own choice of AI models. OpusClip is the stronger fit if you want a managed product with a free tier to try. We don't claim either produces better clips, because we haven't run a controlled test that would support that.",
  },
  {
    question: "Does SupoClip add a watermark?",
    answer:
      "No. SupoClip's rendering pipeline has no watermark step, in the hosted app or a self-hosted install. OpusClip's help center says free-plan exports can include an OpusClip watermark.",
  },
  {
    question: "Does hosted SupoClip have a free plan?",
    answer:
      "No. The hosted app requires a Pro or Scale subscription to process videos. The free route is self-hosting the open-source code, where there are no plan limits; you pay only for your own infrastructure and any AI providers you configure.",
  },
  {
    question: "Which one is faster or makes better clips?",
    answer:
      "We haven't published a controlled test of speed or clip quality, so we don't make that claim. Results depend on your videos and settings; the fair-test steps on this page show how to compare them on your own content.",
  },
];

const toc: Array<[string, string]> = [
  ["verdict", "Short answer"],
  ["comparison", "Side by side"],
  ["pricing", "Pricing model"],
  ["choose-supoclip", "When SupoClip fits"],
  ["choose-opusclip", "When OpusClip fits"],
  ["test", "Run your own test"],
  ["sources", "Sources"],
  ["faq", "FAQ"],
];

export function OpusClipComparisonArticle({ post }: { post: BlogPost }) {
  const [pro, scale] = getPublicBillingPlans();
  const rows: Array<[string, React.ReactNode, React.ReactNode]> = [
    [
      "How you run it",
      <>Hosted app, iOS app, or self-hosted with <a href={source("docker-compose.yml")}>Docker Compose</a></>,
      "Hosted service",
    ],
    [
      "Source code",
      <>Public, <a href={source("LICENSE")}>AGPL-3.0</a></>,
      "Not covered by the sources below",
    ],
    [
      "Pricing model",
      <>Hosted: monthly plans with a fixed number of videos. Self-hosted: no plan limits.</>,
      <>Plans with credits for processing minutes, plus a free plan (<a href={OPUS_SOURCES.plans}>help center</a>)</>,
    ],
    [
      "Watermark",
      <>None; the <a href={`${GITHUB_URL}/tree/main/backend/src/media`}>render pipeline</a> has no watermark step</>,
      <>Free-plan exports can include one (<a href={OPUS_SOURCES.watermark}>help center</a>)</>,
    ],
    [
      "AI model choice",
      <>OpenAI, Google, Anthropic, OpenRouter, or local Ollama (<a href={source("docs/configuration.md")}>config</a>)</>,
      "Not covered by the sources below",
    ],
    [
      "Transcription",
      "AssemblyAI, local Whisper, or YouTube captions",
      "Not covered by the sources below",
    ],
    [
      "Automation",
      <>REST API with per-user keys and an <a href={source("mcp/README.md")}>MCP server</a></>,
      "Not covered by the sources below",
    ],
  ];

  return (
    <ArticleShell post={post} breadcrumb="SupoClip vs OpusClip" toc={toc} faqs={faqs}>
      <p>
        OpusClip helped define AI clipping: upload a long video, get short vertical clips back. SupoClip does the same
        job as an open-source project you can use hosted or run yourself. This comparison sticks to what each product
        documents. SupoClip claims link to our source code. OpusClip claims link to OpusClip&apos;s own help center.
        Where a source doesn&apos;t cover something, the table says so rather than guessing.
      </p>
      <p>
        This page doesn&apos;t rate clip quality, caption accuracy, or processing speed. To compare those, see{" "}
        <a href="#test">how to run your own test</a>.
      </p>

      <h2 id="verdict">The short answer</h2>
      <ul>
        <li><strong>Choose SupoClip</strong> if you want to self-host, inspect or modify the code, pick your own AI models (including fully local ones), or export without a watermark.</li>
        <li><strong>Choose OpusClip</strong> if you want a managed product you never have to deploy, and want to start on a free plan.</li>
      </ul>

      <h2 id="comparison">Side by side</h2>
      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full min-w-[600px] text-left text-sm leading-6">
          <caption className="sr-only">SupoClip and OpusClip comparison with sources</caption>
          <thead className="bg-muted">
            <tr><th scope="col" className="p-3">Area</th><th scope="col" className="p-3">SupoClip</th><th scope="col" className="p-3">OpusClip</th></tr>
          </thead>
          <tbody>
            {rows.map(([area, supoclip, opusclip]) => (
              <tr key={area} className="border-t">
                <th scope="row" className="p-3 align-top">{area}</th>
                <td className="p-3 align-top text-muted-foreground">{supoclip}</td>
                <td className="p-3 align-top text-muted-foreground">{opusclip}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-sm">
        &ldquo;Not covered by the sources below&rdquo; means the cited OpusClip help-center articles don&apos;t address it. It
        doesn&apos;t mean OpusClip lacks the feature. Check <a href="https://www.opus.pro/">opus.pro</a> for anything not listed here.
      </p>

      <h2 id="pricing">Pricing model: credits vs. plans vs. your own infrastructure</h2>
      <p>
        OpusClip&apos;s <a href={OPUS_SOURCES.plans}>plans and credits article</a> describes paid plans that use credits for
        processing minutes, plus a free plan. Its <a href={OPUS_SOURCES.watermark}>watermark article</a> says free-plan exports can
        carry an OpusClip watermark. Plan prices and credit amounts change, so check OpusClip&apos;s current pricing page
        instead of relying on numbers copied into a blog post.
      </p>
      <p>
        SupoClip offers two routes. The hosted app has <strong>{pro.name}</strong> at ${pro.priceMonthly}/month for{" "}
        {pro.generationLimit} videos and <strong>{scale.name}</strong> at ${scale.priceMonthly}/month for {scale.generationLimit} videos.
        There is no free hosted plan; processing requires a subscription. The self-hosted route is free software with no plan
        limits. You pay for the server and any AI APIs you configure, and the{" "}
        <Link href={SELF_HOSTING_GUIDE_HREF}>self-hosting guide</Link> shows a fully local setup with no per-request AI fees.
      </p>

      <h2 id="choose-supoclip">When SupoClip is the better fit</h2>
      <ul>
        <li><strong>You need control over where video goes.</strong> Self-hosting keeps the app, database, and rendered clips on your infrastructure. With Whisper and Ollama, transcription and clip selection run locally too.</li>
        <li><strong>You have a large back catalog.</strong> Self-hosted installs have no monthly video cap. Throughput depends on your hardware and provider limits.</li>
        <li><strong>You want to change how clips are chosen.</strong> The selection prompt, scoring, caption templates, and rendering are all in the repository.</li>
        <li><strong>You&apos;re building clipping into another tool.</strong> API keys, a REST API, and an MCP server let scripts and AI agents create and download clips.</li>
      </ul>

      <h2 id="choose-opusclip">When OpusClip is the better fit</h2>
      <ul>
        <li><strong>You don&apos;t want to operate anything.</strong> Self-hosting means managing Docker, provider keys, updates, and backups.</li>
        <li><strong>You want to try before paying.</strong> OpusClip documents a free plan (with a possible watermark). Hosted SupoClip has no free tier.</li>
        <li><strong>You rely on features not covered here.</strong> If a specific OpusClip workflow matters to you, confirm it on opus.pro and test whether SupoClip covers it before switching.</li>
      </ul>

      <h2 id="test">How to run your own fair test</h2>
      <p>The comparison that matters most is one on your own content:</p>
      <ol>
        <li>Pick two or three recordings that represent your channel: one easy (single speaker), one hard (multiple speakers, screen shares).</li>
        <li>Run each through both tools with default settings, and note the time from submission to finished clips.</li>
        <li>Shuffle the clips and review them without knowing which tool made each. Score whether each clip makes sense without context, has a clear hook, frames the speaker correctly, and has accurate captions.</li>
        <li>Count the edits each clip needs before you&apos;d post it. Editing time is a cost the subscription price doesn&apos;t show.</li>
      </ol>
      <p>
        Want to see SupoClip output first? The <Link href="/demo">demo page</Link> shows real clips next to links to the
        original timestamps.
      </p>

      <h2 id="sources">Sources</h2>
      <ul>
        <li>OpusClip help center: <a href={OPUS_SOURCES.plans}>Plans and credits</a> and <a href={OPUS_SOURCES.watermark}>Watermark</a>. Plans can change; confirm current details on <a href="https://www.opus.pro/">opus.pro</a>.</li>
        <li>SupoClip: <a href={GITHUB_URL}>public repository</a>, including <a href={source("docker-compose.yml")}>docker-compose.yml</a>, <a href={source("docs/configuration.md")}>configuration docs</a>, and <a href={source("backend/src/services/billing_service.py")}>billing rules</a>. Hosted prices are from the pricing section of supoclip.com.</li>
      </ul>
    </ArticleShell>
  );
}
