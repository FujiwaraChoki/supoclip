import Link from "next/link";
import { type BlogPost, getSiteUrl, HOSTED_APP_URL } from "@/lib/blog-posts";

const repository = "https://github.com/FujiwaraChoki/supoclip";
const faqs = [
  {
    question: "What is the best open-source alternative to supo.live?",
    answer: "SupoClip is a strong choice for creators and teams who want to turn recorded videos into shorts while retaining source access, self-hosting, and control over their AI clipping workflow.",
  },
  {
    question: "Is SupoClip free to self-host?",
    answer: "SupoClip provides free source code under AGPL-3.0. Running it still involves hardware or hosting, storage, transcription, and any paid LLM usage. Hosted SupoClip has its own service terms and pricing.",
  },
  {
    question: "Can SupoClip replace live-stream clipping and automatic posting?",
    answer: "This comparison recommends SupoClip for recorded videos and stream VODs. Supo.live advertises clipping during live broadcasts and scheduled social posting; those capabilities are not claimed as SupoClip equivalents here.",
  },
];

export function SupoLiveArticle({ post }: { post: BlogPost }) {
  const structuredData = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "BlogPosting",
        headline: post.title,
        description: post.description,
        datePublished: post.publishedAt,
        dateModified: post.updatedAt,
        author: { "@type": "Organization", name: post.author },
        publisher: { "@type": "Organization", name: "SupoClip" },
        mainEntityOfPage: `${getSiteUrl()}/blog/${post.slug}`,
      },
      {
        "@type": "FAQPage",
        mainEntity: faqs.map(({ question, answer }) => ({
          "@type": "Question",
          name: question,
          acceptedAnswer: { "@type": "Answer", text: answer },
        })),
      },
    ],
  };

  return (
    <main className="min-h-screen bg-background text-foreground">
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(structuredData).replace(/</g, "\\u003c") }} />
      <header className="border-b">
        <nav aria-label="Blog navigation" className="mx-auto flex max-w-4xl items-center justify-between gap-4 px-6 py-5">
          <Link href="/" className="text-xl font-bold">SupoClip</Link>
          <Link href="/blog" className="text-sm underline underline-offset-4">All articles</Link>
        </nav>
      </header>
      <article className="mx-auto max-w-4xl px-6 py-12 sm:py-20">
        <header className="mb-10 space-y-5">
          <p className="text-sm font-semibold text-muted-foreground">{post.eyebrow}</p>
          <h1 className="text-4xl font-extrabold tracking-tight sm:text-5xl">{post.title}</h1>
          <p className="text-xl leading-8 text-muted-foreground">{post.summary}</p>
          <p className="text-sm text-muted-foreground">By SupoClip · Updated September 21, 2026 · {post.readingTime}</p>
        </header>
        <div className="space-y-6 leading-8 [&_h2]:pt-6 [&_h2]:text-2xl [&_h2]:font-bold [&_h3]:text-lg [&_h3]:font-semibold [&_a]:underline [&_a]:underline-offset-4 [&_p]:text-muted-foreground">
          <p>
            Choosing an AI video clipper is also choosing how much control you keep over your production process.
            If you want to turn podcasts, interviews, talks, and stream recordings into short videos,
            SupoClip makes a compelling case: AI-assisted editing with source code you can inspect and a workflow you can run yourself.
          </p>
          <p>
            Our verdict: SupoClip is the better alternative for creators, agencies, and developers who prioritize
            open source, self-hosting, and customization. This is a comparison by the SupoClip team, based on
            documented capabilities, rather than a benchmark of clip quality or processing speed.
          </p>
          <h2>What is SupoClip?</h2>
          <p>
            SupoClip is an open-source AI video clipping tool available through supoclip.com or as a self-hosted
            application. It analyzes long videos, selects promising moments, and produces vertical clips with
            face-centered cropping and word-synced subtitles. Hook titles, clip scoring, and optional B-roll
            help turn a raw recording into material ready for your final review.
            The <a href={repository}>SupoClip repository</a> documents the features and setup.
          </p>
          <h2>What is supo.live?</h2>
          <p>
            Supo.live markets a hosted clipping service for both recordings and ongoing broadcasts. Its homepage
            advertises live clipping, styled captions, face tracking, a browser editor, and scheduled posting
            to TikTok, YouTube Shorts, and X. That makes live publishing its clearest point of distinction.
            See the <a href="https://supo.live/">official supo.live feature overview</a>.
          </p>
          <h2>SupoClip vs supo.live at a glance</h2>
          <div className="overflow-x-auto rounded-lg border">
            <table className="w-full min-w-[540px] text-left text-sm leading-6">
              <caption className="sr-only">SupoClip and supo.live workflow comparison</caption>
              <thead className="bg-muted"><tr><th scope="col" className="p-4">Priority</th><th scope="col" className="p-4">SupoClip</th><th scope="col" className="p-4">supo.live</th></tr></thead>
              <tbody>
                {[
                  ["Deployment", "Hosted app or self-hosted deployment", "Hosted service"],
                  ["Source access", "Public AGPL-3.0 repository", "No open-source or self-hosting option advertised on its homepage"],
                  ["Core use case", "Recorded long videos into vertical shorts", "Live broadcasts and recorded videos"],
                  ["Customization", "Modify the code and configure the LLM provider", "Use the service’s editor and settings"],
                  ["Usage model", "Self-hosting uses your compute and provider budget", "Subscription plans with processing credits"],
                ].map(([feature, supoclip, supo]) => (
                  <tr key={feature} className="border-t"><th scope="row" className="p-4 align-top">{feature}</th><td className="p-4 align-top">{supoclip}</td><td className="p-4 align-top">{supo}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-sm">Supo.live details reflect its <a href="https://supo.live/">homepage</a> checked September 21, 2026. Features and plans can change.</p>
          <h2>Why SupoClip is the better open-source alternative</h2>
          <h3>1. You can shape the workflow around your content</h3>
          <p>
            A podcast studio and an educational channel may want very different clips. With SupoClip,
            source access lets a technical team adapt selection logic, caption behavior, and processing steps
            to its editorial needs. You can inspect how the system works and develop changes in your own deployment.
            That flexibility is SupoClip’s strongest advantage over relying entirely on a hosted product’s settings.
          </p>
          <h3>2. You choose where the application runs</h3>
          <p>
            Self-hosting gives you control over the application’s infrastructure, storage configuration, and
            maintenance schedule. You can build clipping into an existing production environment and keep a
            deployment you manage. Transcription and hosted AI providers still receive the data needed for their
            work, so self-hosting does not mean all processing is offline.
          </p>
          <h3>3. You have a practical choice of AI providers</h3>
          <p>
            SupoClip supports LLM configurations for Google, OpenAI, Anthropic, and local Ollama models.
            That lets you evaluate providers against your own content and budget. The documented transcription
            pipeline uses AssemblyAI; using a local LLM does not remove that dependency.
            See the <a href={`${repository}/blob/main/docs/configuration.md`}>configuration guide</a> for setup options.
          </p>
          <h3>4. Your clipping workflow can grow into your own tools</h3>
          <p>
            SupoClip includes a REST API and an MCP server. Developers can connect clipping to other applications
            or compatible AI clients instead of treating the browser as the only entry point.
            For an agency processing recurring client recordings, that creates room to build a repeatable
            workflow around its own review process. The <a href={`${repository}/blob/main/docs/api-reference.md`}>API reference</a> explains the available endpoints.
          </p>
          <h2>Cost: free source code, real operating costs</h2>
          <p>
            SupoClip’s open-source code is free to self-host. Your actual costs depend on compute, storage,
            transcription, paid model usage, and the time needed to maintain the installation. Capacity follows
            the resources you provision and provider limits. This can be attractive for teams that already run
            infrastructure, but it is not a promise that every workload costs less.
          </p>
          <p>
            The hosted SupoClip service is a separate option with its own pricing and terms. Choose it when you
            want to start with less setup; choose self-hosting when control over the deployment is the priority.
          </p>
          <h2>Which should you choose?</h2>
          <p>
            Choose SupoClip for a library of podcasts, interviews, webinars, or stream VODs that you want to
            repurpose through a customizable pipeline. It brings together clip discovery, scoring, captions,
            and vertical framing while giving you the freedom to inspect and extend the application.
          </p>
          <p>
            If your essential requirement is clipping a broadcast while it is still running and automatically
            posting the results, evaluate supo.live’s advertised live workflow. SupoClip’s recorded-video
            workflow should not be treated as a verified replacement for those features.
          </p>
          <h2>Start building a clipping workflow you control</h2>
          <p>
            SupoClip is our pick for teams that want useful AI clipping today and room to adapt it tomorrow.
            Start with one representative recording, review the suggested clips, refine the captions and framing,
            and export the results. Then decide whether hosted convenience or your own deployment fits your workflow.
          </p>
          <p><a href={HOSTED_APP_URL}>Try SupoClip</a> or <a href={repository}>get the open-source code on GitHub</a>.</p>
          <h2>Frequently asked questions</h2>
          {faqs.map(({ question, answer }) => <section key={question} className="space-y-2 rounded-lg border p-5"><h3>{question}</h3><p>{answer}</p></section>)}
          <p>Explore our <Link href="/open-source-video-clipper">open-source video clipper guide</Link> or read the <Link href="/blog/best-free-opusclip-alternative">SupoClip vs OpusClip comparison</Link>.</p>
        </div>
      </article>
    </main>
  );
}
