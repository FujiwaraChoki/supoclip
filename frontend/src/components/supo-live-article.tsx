import Link from "next/link";
import Image from "next/image";
import { ArrowRight, Captions, Code2, Film, Github, ScanFace, Server, Sparkles, Upload, Check } from "lucide-react";
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
        "@type": "BreadcrumbList",
        itemListElement: [
          { "@type": "ListItem", position: 1, name: "Home", item: getSiteUrl() },
          { "@type": "ListItem", position: 2, name: "Blog", item: `${getSiteUrl()}/blog` },
          { "@type": "ListItem", position: 3, name: "SupoClip vs supo.live", item: `${getSiteUrl()}/blog/${post.slug}` },
        ],
      },
      {
        "@type": "BlogPosting",
        headline: post.title,
        description: post.description,
        datePublished: post.publishedAt,
        dateModified: post.updatedAt,
        author: { "@type": "Organization", name: post.author },
        publisher: { "@type": "Organization", name: "SupoClip", url: getSiteUrl(), logo: { "@type": "ImageObject", url: `${getSiteUrl()}/logo.png` } },
        image: post.image ? [`${getSiteUrl()}${post.image.src}`, `${getSiteUrl()}/blog/supoclip-editor.webp`] : undefined,
        articleSection: post.category,
        inLanguage: "en",
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
          <Link href="/" className="flex items-center gap-2.5 text-xl font-bold"><Image src="/logo.png" alt="" width={30} height={30} className="rounded-lg" />SupoClip</Link>
          <Link href="/blog" className="text-sm underline underline-offset-4">All articles</Link>
        </nav>
      </header>
      <article className="mx-auto max-w-5xl px-5 py-10 sm:px-8 sm:py-16">
        <nav aria-label="Breadcrumb" className="mb-8 flex flex-wrap gap-2 text-sm text-muted-foreground"><Link href="/">Home</Link><span aria-hidden="true">/</span><Link href="/blog">Blog</Link><span aria-hidden="true">/</span><span>SupoClip vs supo.live</span></nav>
        <header className="mb-10 max-w-3xl space-y-5">
          <p className="inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-semibold uppercase tracking-widest"><Code2 className="h-4 w-4" />{post.eyebrow}</p>
          <h1 className="text-4xl font-extrabold leading-[1.08] tracking-tight sm:text-6xl" style={{ fontFamily: "var(--font-syne), var(--font-geist-sans), sans-serif" }}>{post.title}</h1>
          <p className="text-xl leading-8 text-muted-foreground">{post.summary}</p>
          <p className="text-sm text-muted-foreground">By SupoClip · Updated September 21, 2026 · {post.readingTime}</p>
        </header>
        {post.image && <figure className="mb-10 overflow-hidden rounded-2xl border bg-muted/30">
          <Image src={post.image.src} alt={post.image.alt} width={post.image.width} height={post.image.height} priority sizes="(max-width: 1024px) 100vw, 960px" className="h-auto w-full" />
          <figcaption className="px-5 py-3 text-xs leading-5 text-muted-foreground">One recording, more possibilities. AI-generated editorial illustration of the long-video-to-shorts workflow.</figcaption>
        </figure>}
        <section aria-labelledby="verdict" className="mb-10 rounded-2xl border border-emerald-500/25 bg-emerald-500/5 p-6 sm:p-8">
          <p className="mb-3 text-xs font-semibold uppercase tracking-widest text-emerald-700 dark:text-emerald-400">The quick verdict</p>
          <h2 id="verdict" className="text-2xl font-bold tracking-tight">Choose SupoClip when control matters.</h2>
          <p className="mt-3 max-w-2xl leading-7 text-muted-foreground">Open source. Your deployment. Your workflow. A strong foundation for turning recorded content into captioned shorts.</p>
          <div className="mt-6 grid gap-3 sm:grid-cols-3">{[{ icon: Github, text: "Inspect and extend the code" }, { icon: Server, text: "Self-host on your infrastructure" }, { icon: Captions, text: "Refine every finished clip" }].map(({ icon: Icon, text }) => <div key={text} className="flex items-center gap-3 rounded-xl border bg-background p-4 text-sm font-medium"><Icon className="h-5 w-5 shrink-0 text-emerald-600" />{text}</div>)}</div>
        </section>
        <nav aria-label="In this article" className="mb-10 flex flex-wrap gap-x-5 gap-y-2 border-y py-4 text-sm font-medium">{[["comparison", "Compare the tools"], ["workflow", "See the workflow"], ["control", "Why open source"], ["cost", "Understand the costs"], ["faq", "FAQ"]].map(([id, label]) => <a key={id} href={`#${id}`} className="underline-offset-4 hover:underline">{label}</a>)}</nav>
        <div className="mx-auto max-w-3xl space-y-6 leading-8 [&_h2]:pt-6 [&_h2]:text-2xl [&_h2]:font-bold [&_h3]:text-lg [&_h3]:font-semibold [&_a]:underline [&_a]:underline-offset-4 [&_p]:text-muted-foreground">
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
          <h2 id="comparison" className="scroll-mt-8">SupoClip vs supo.live at a glance</h2>
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
          <h2 id="workflow" className="scroll-mt-8">From one recording to a repeatable workflow</h2>
          <p>Let AI handle the first pass, then use your editorial judgment. Every clip should make sense on its own, retain the speaker’s context, and earn its place in your feed.</p>
          <figure className="rounded-2xl border bg-muted/25 p-5 sm:p-7">
            <ol className="grid gap-4 sm:grid-cols-2">
              {[
                { icon: Upload, title: "Bring a recording", detail: "Upload a video or use a supported YouTube link." },
                { icon: Sparkles, title: "Discover the moments", detail: "Transcription and AI scoring create a shortlist." },
                { icon: ScanFace, title: "Make the cut yours", detail: "Review timing, vertical framing, and captions." },
                { icon: Film, title: "Export your shorts", detail: "Prepare the final clips for your social channels." },
              ].map(({ icon: Icon, title, detail }, i) => <li key={title} className="rounded-xl border bg-background p-4"><div className="flex items-center justify-between"><Icon className="h-5 w-5 text-emerald-600" /><span className="font-mono text-xs text-muted-foreground">0{i + 1}</span></div><h3 className="mt-4">{title}</h3><p className="mt-1 text-sm leading-6">{detail}</p></li>)}
            </ol>
            <figcaption className="mt-4 text-xs leading-5 text-muted-foreground">SupoClip’s recorded-video workflow. AI suggests; you make the final editorial decision.</figcaption>
          </figure>
          <figure className="overflow-hidden rounded-2xl border">
            <a href="/blog/supoclip-editor.webp" aria-label="View full-size SupoClip editor screenshot"><Image src="/blog/supoclip-editor.webp" alt="SupoClip editor with clip list, vertical preview, caption styling controls, and a timeline" width={1800} height={1109} sizes="(max-width: 768px) 100vw, 768px" className="h-auto w-full" /></a>
            <figcaption className="bg-muted/25 px-5 py-4 text-sm leading-6 text-muted-foreground">Inside the SupoClip editor: adjust captions, framing, and timing before export. Actual product screenshot using sample test footage; click to enlarge.</figcaption>
          </figure>
          <h2 id="control" className="scroll-mt-8">Why SupoClip is the better open-source alternative</h2>
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
          <figure className="rounded-2xl bg-zinc-950 p-6 text-zinc-50 sm:p-8">
            <figcaption className="mb-6 font-semibold">What you control when you self-host</figcaption>
            <div className="grid gap-4 sm:grid-cols-[1fr_auto_1fr] sm:items-center">
              <div className="rounded-xl border border-emerald-400/40 bg-emerald-400/10 p-5"><Server className="mb-3 h-6 w-6 text-emerald-300" /><h3>Your deployment</h3><ul className="mt-3 space-y-2 text-sm text-zinc-300"><li>Application and source code</li><li>Storage and processing resources</li><li>Editorial customization</li></ul></div>
              <ArrowRight className="mx-auto h-5 w-5 rotate-90 text-zinc-400 sm:rotate-0" aria-hidden="true" />
              <div className="rounded-xl border border-zinc-700 p-5"><Sparkles className="mb-3 h-6 w-6 text-zinc-300" /><h3>Connected AI services</h3><ul className="mt-3 space-y-2 text-sm text-zinc-300"><li>AssemblyAI transcription</li><li>Hosted LLM or local Ollama</li><li>Provider costs and data flows</li></ul></div>
            </div>
            <div className="mt-5 text-xs leading-6 text-zinc-400">Self-hosting gives you deployment control. It does not make the entire pipeline offline.</div>
          </figure>
          <h2 id="cost" className="scroll-mt-8">Cost: free source code, real operating costs</h2>
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
          <section className="rounded-2xl border bg-muted/30 p-6 sm:p-8" aria-label="Start with SupoClip">
            <Image src="/logo.png" alt="SupoClip logo" width={40} height={40} className="mb-4 rounded-lg" />
            <h3>Your next recording deserves a second life.</h3>
            <ul className="my-5 space-y-2 text-sm">{["Start with a recording you know well", "Review the strongest suggested moments", "Refine the captions and export"].map(text => <li key={text} className="flex items-center gap-2"><Check className="h-4 w-4 text-emerald-600" />{text}</li>)}</ul>
            <div className="flex flex-wrap gap-3"><a href={HOSTED_APP_URL} className="inline-flex items-center gap-2 rounded-lg bg-foreground px-5 py-3 text-sm font-semibold !text-background !no-underline">Try SupoClip<ArrowRight className="h-4 w-4" /></a><a href={repository} className="inline-flex items-center gap-2 rounded-lg border px-5 py-3 text-sm font-semibold !no-underline"><Github className="h-4 w-4" />Get the source</a></div>
          </section>
          <h2 id="faq" className="scroll-mt-8">Frequently asked questions</h2>
          {faqs.map(({ question, answer }) => <section key={question} className="space-y-2 rounded-lg border p-5"><h3>{question}</h3><p>{answer}</p></section>)}
          <p>Explore our <Link href="/open-source-video-clipper">open-source video clipper guide</Link> or read the <Link href="/blog/best-free-opusclip-alternative">SupoClip vs OpusClip comparison</Link>.</p>
        </div>
      </article>
    </main>
  );
}
