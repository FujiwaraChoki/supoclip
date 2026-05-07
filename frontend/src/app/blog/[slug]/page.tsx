import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { notFound } from "next/navigation";
import {
  ArrowRight,
  Captions,
  Clock,
  ExternalLink,
  Github,
  ListChecks,
  Play,
  ScanFace,
  ShieldCheck,
  Sparkles,
  Wand2,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { HOSTED_APP_URL, blogPosts, getBlogPost, getBlogPostMetadata, getSiteUrl } from "@/lib/blog-posts";

interface BlogPostPageProps {
  params: Promise<{
    slug: string;
  }>;
}

const comparisonRows = [
  {
    icon: ShieldCheck,
    feature: "Free usage model",
    supoclip: "Open source and self-hostable. Your main costs are your own compute and API keys.",
    opusClip: "Free plan exists, but exports can include an OpusClip watermark.",
  },
  {
    icon: Sparkles,
    feature: "Clip discovery",
    supoclip: "Transcribes long videos, scores segments, and surfaces promising short-form moments.",
    opusClip: "AI clipping workflow built for finding social-ready clips from long videos.",
  },
  {
    icon: ScanFace,
    feature: "Vertical formatting",
    supoclip: "Face-centered 9:16 cropping, subtitles, and platform-ready exports.",
    opusClip: "Offers short-form exports and AI reframing for social platforms.",
  },
  {
    icon: Github,
    feature: "Control",
    supoclip: "Full source access, editable deployment, and no vendor lock-in.",
    opusClip: "Hosted product with managed workflows and subscription tiers.",
  },
];

const faqs = [
  {
    icon: Sparkles,
    question: "What is the best free OpusClip alternative?",
    answer:
      "SupoClip is a strong free OpusClip alternative if you want an open-source AI clip maker that you can self-host, customize, and run with your own video workflow.",
  },
  {
    icon: ShieldCheck,
    question: "Is SupoClip completely free?",
    answer:
      "The SupoClip codebase is free and open source. If you self-host it, you still need to account for your own infrastructure, transcription, and LLM provider costs.",
  },
  {
    icon: Captions,
    question: "Does SupoClip add a watermark?",
    answer:
      "SupoClip is designed for self-hosted control, so watermarking is not a forced platform limitation in the open-source workflow.",
  },
];

const articleBodyClassName =
  "space-y-6 text-base leading-8 text-foreground [&_a]:font-medium [&_a]:text-foreground [&_a]:underline [&_a]:underline-offset-4 [&_h2]:mt-12 [&_h2]:text-2xl [&_h2]:font-bold [&_h2]:tracking-tight [&_h2]:text-foreground [&_h2]:first:mt-0 [&_li]:mt-2 [&_ol]:ml-5 [&_ol]:list-decimal [&_p]:text-muted-foreground";

export function generateStaticParams() {
  return blogPosts.map((post) => ({ slug: post.slug }));
}

export async function generateMetadata({ params }: BlogPostPageProps): Promise<Metadata> {
  const { slug } = await params;
  const post = getBlogPost(slug);

  if (!post) {
    return {};
  }

  return getBlogPostMetadata(post);
}

export default async function BlogPostPage({ params }: BlogPostPageProps) {
  const { slug } = await params;
  const post = getBlogPost(slug);

  if (!post) {
    notFound();
  }

  const siteUrl = getSiteUrl();
  const articleUrl = `${siteUrl}/blog/${post.slug}`;
  const articleJsonLd = {
    "@context": "https://schema.org",
    "@type": "BlogPosting",
    headline: post.title,
    description: post.description,
    datePublished: post.publishedAt,
    dateModified: post.updatedAt,
    author: {
      "@type": "Organization",
      name: post.author,
    },
    publisher: {
      "@type": "Organization",
      name: "SupoClip",
      logo: {
        "@type": "ImageObject",
        url: `${siteUrl}/logo.png`,
      },
    },
    mainEntityOfPage: articleUrl,
  };
  const faqJsonLd = {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: faqs.map((faq) => ({
      "@type": "Question",
      name: faq.question,
      acceptedAnswer: {
        "@type": "Answer",
        text: faq.answer,
      },
    })),
  };

  return (
    <main className="min-h-screen bg-background text-foreground">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(articleJsonLd) }}
      />
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(faqJsonLd) }}
      />

      <header className="border-b bg-background/95">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-6">
          <Link href="/" className="flex items-center gap-2.5">
            <Image src="/logo.png" alt="SupoClip" width={24} height={24} className="rounded-lg" />
            <span
              className="text-lg font-bold tracking-tight"
              style={{ fontFamily: "var(--font-syne), var(--font-geist-sans), system-ui" }}
            >
              SupoClip
            </span>
          </Link>
          <div className="flex items-center gap-2">
            <Link href="/blog">
              <Button variant="ghost" size="sm">
                Blog
              </Button>
            </Link>
            <a href={HOSTED_APP_URL} target="_blank" rel="noopener noreferrer">
              <Button variant="ghost" size="sm" className="hidden sm:inline-flex">
                Hosted App
                <ExternalLink className="h-3.5 w-3.5" />
              </Button>
            </a>
            <Link href="/sign-up">
              <Button size="sm">Start Clipping</Button>
            </Link>
          </div>
        </div>
      </header>

      <article>
        <section className="border-b bg-muted/35">
          <div className="mx-auto grid max-w-6xl gap-10 px-6 py-14 md:grid-cols-[1fr_0.42fr] md:py-20">
            <div>
              <div className="mb-5 flex flex-wrap gap-2">
                <Badge variant="secondary" className="gap-2">
                  <Sparkles className="h-3.5 w-3.5" />
                  {post.eyebrow}
                </Badge>
                <a href={HOSTED_APP_URL} target="_blank" rel="noopener noreferrer">
                  <Badge variant="outline" className="gap-2">
                    <Play className="h-3.5 w-3.5" />
                    Try hosted SupoClip
                  </Badge>
                </a>
              </div>
              <h1
                className="max-w-4xl text-4xl font-extrabold tracking-tight sm:text-5xl lg:text-6xl"
                style={{ fontFamily: "var(--font-syne), var(--font-geist-sans), system-ui" }}
              >
                {post.title}
              </h1>
              <p className="mt-6 max-w-2xl text-lg leading-8 text-muted-foreground">
                If you like the idea of OpusClip but want a free, open-source path, SupoClip gives
                you the core workflow: find strong moments in long videos, reframe them vertically,
                add captions, and export clips for Shorts, Reels, and TikTok.
              </p>
              <div className="mt-8 flex flex-wrap items-center gap-4 text-sm text-muted-foreground">
                <span>{post.author}</span>
                <span>Updated {new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(new Date(post.updatedAt))}</span>
                <span className="flex items-center gap-1.5">
                  <Clock className="h-3.5 w-3.5" />
                  {post.readingTime}
                </span>
              </div>
            </div>

            <aside className="rounded-lg border bg-background p-5 md:self-end">
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                Best for
              </p>
              <ul className="mt-5 space-y-3 text-sm">
                {[
                  { icon: Play, label: "Creators clipping YouTube videos into shorts" },
                  { icon: ShieldCheck, label: "Teams that prefer self-hosting" },
                  { icon: Github, label: "Developers who want source-level control" },
                ].map(({ icon: Icon, label }) => (
                  <li key={label} className="flex gap-3">
                    <Icon className="mt-0.5 h-4 w-4 shrink-0 text-green-600" />
                    <span>{label}</span>
                  </li>
                ))}
              </ul>
              <a href={HOSTED_APP_URL} target="_blank" rel="noopener noreferrer" className="mt-5 inline-flex text-sm font-semibold">
                Open hosted version
                <ExternalLink className="ml-2 h-4 w-4" />
              </a>
            </aside>
          </div>
        </section>

        <section className="mx-auto grid max-w-6xl gap-12 px-6 py-12 lg:grid-cols-[minmax(0,1fr)_280px] lg:py-16">
          <div className="min-w-0">
            <div className={articleBodyClassName}>
              <p>
                OpusClip helped define the modern AI clipping category: upload a long video, let AI
                identify moments, and publish short clips faster. That workflow is useful. The
                friction starts when you want a free route, more control, or a pipeline you can run
                yourself.
              </p>

              <p>
                SupoClip is the best free OpusClip alternative for that specific creator: someone
                who wants AI-assisted clip discovery, vertical exports, captions, and control over
                the underlying system. You can also
                {" "}
                <a href={HOSTED_APP_URL} target="_blank" rel="noopener noreferrer">
                  try the hosted SupoClip product
                </a>
                {" "}
                if you want to skip local setup.
              </p>

              <h2 id="quick-verdict">Quick Verdict</h2>
              <p>
                Choose SupoClip if you want an open-source AI video clipper you can self-host.
                Choose OpusClip if you want a managed hosted product and are comfortable with its
                plan limits, watermark rules, and credit model.
              </p>

              <div className="not-prose my-8 grid gap-4 sm:grid-cols-3">
                {[
                  { icon: ShieldCheck, label: "Cost", value: "Free source code" },
                  { icon: Wand2, label: "Workflow", value: "Long video to shorts" },
                  { icon: Github, label: "Control", value: "Self-hostable" },
                ].map(({ icon: Icon, ...stat }) => (
                  <div key={stat.label} className="rounded-lg border bg-muted/35 p-4">
                    <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                      <Icon className="h-4 w-4" />
                      {stat.label}
                    </p>
                    <p className="mt-2 text-lg font-semibold">{stat.value}</p>
                  </div>
                ))}
              </div>

              <h2>Why Creators Look for an OpusClip Alternative</h2>
              <p>
                The usual reasons are simple: creators want lower costs, cleaner exports on a free
                workflow, more control over processing, and fewer limits when they batch through old
                YouTube videos, podcasts, webinars, or livestreams.
              </p>

              <p>
                OpusClip&apos;s own help center says free-plan exports can show an OpusClip
                watermark, and its plans use credits for processing minutes. For casual testing,
                that can be fine. For repeat clipping, it can push creators toward a paid hosted
                workflow.
              </p>

              <h2>What Makes SupoClip Different</h2>
              <p>
                SupoClip is not just a thin wrapper around a hosted editor. It is a complete
                open-source clipping app with a FastAPI backend, a Next.js frontend, background
                workers, transcription, AI scoring, face-aware vertical reframing, caption styling,
                and export presets.
              </p>

              <p>
                That makes the tradeoff clear. You take on setup, but you gain ownership. You can
                inspect the code, adapt it to your workflow, and decide which hosted AI providers
                or local models to use.
              </p>

              <h2 id="supoclip-vs-opusclip">SupoClip vs OpusClip</h2>
            </div>

            <div className="my-8 overflow-hidden rounded-lg border">
              <div className="grid grid-cols-[0.85fr_1fr_1fr] bg-muted/50 text-sm font-semibold">
                <div className="p-4">Feature</div>
                <div className="border-l p-4">SupoClip</div>
                <div className="border-l p-4">OpusClip</div>
              </div>
              {comparisonRows.map(({ icon: Icon, ...row }) => (
                <div key={row.feature} className="grid grid-cols-[0.85fr_1fr_1fr] border-t text-sm">
                  <div className="flex items-start gap-2 p-4 font-medium">
                    <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                    {row.feature}
                  </div>
                  <div className="border-l p-4 leading-6 text-muted-foreground">{row.supoclip}</div>
                  <div className="border-l p-4 leading-6 text-muted-foreground">{row.opusClip}</div>
                </div>
              ))}
            </div>

            <div className={articleBodyClassName}>
              <h2>How SupoClip Works</h2>
              <ol>
                <li>Paste a YouTube URL or upload a video.</li>
                <li>SupoClip transcribes the source and finds candidate moments.</li>
                <li>AI scores clips for hook strength, engagement, value, and shareability.</li>
                <li>The editor creates vertical, captioned clips with face-aware framing.</li>
                <li>You export shorts for YouTube Shorts, TikTok, Instagram Reels, or other feeds.</li>
              </ol>

              <h2>When SupoClip Is the Better Choice</h2>
              <p>
                SupoClip is best when you want a free OpusClip alternative with developer control.
                It is especially useful for creators with a backlog of long videos, agencies that
                want to customize clipping workflows, or technical teams that want AI video
                clipping inside their own stack.
              </p>

              <h2>When OpusClip May Still Be Better</h2>
              <p>
                OpusClip may still be the better fit if you want the convenience of a polished
                hosted service and do not want to think about deployment, infrastructure, model
                keys, queues, or maintenance.
              </p>
            </div>

            <section className="my-10 rounded-lg border bg-foreground p-6 text-background md:p-8">
              <div className="flex flex-col gap-6 md:flex-row md:items-center md:justify-between">
                <div>
                  <p className="flex items-center gap-2 text-sm font-medium text-background/70">
                    <ListChecks className="h-4 w-4" />
                    Ready to try the free route?
                  </p>
                  <h2
                    className="mt-2 text-2xl font-bold tracking-tight"
                    style={{ fontFamily: "var(--font-syne), var(--font-geist-sans), system-ui" }}
                  >
                    Start clipping with SupoClip.
                  </h2>
                </div>
                <div className="flex flex-wrap gap-3">
                  <a href={HOSTED_APP_URL} target="_blank" rel="noopener noreferrer">
                    <Button variant="secondary">
                      Hosted SupoClip
                      <ExternalLink className="h-4 w-4" />
                    </Button>
                  </a>
                  <Link href="/sign-up">
                    <Button variant="secondary">
                      Open app
                      <ArrowRight className="h-4 w-4" />
                    </Button>
                  </Link>
                  <a href="https://github.com/FujiwaraChoki/supoclip" target="_blank" rel="noopener noreferrer">
                    <Button variant="outline" className="border-background/30 bg-transparent text-background hover:bg-background hover:text-foreground">
                      <Github className="h-4 w-4" />
                      GitHub
                    </Button>
                  </a>
                </div>
              </div>
            </section>

            <div className={articleBodyClassName}>
              <h2 id="faq">FAQ</h2>
            </div>

            <div className="mt-6 space-y-4">
              {faqs.map(({ icon: Icon, ...faq }) => (
                <section key={faq.question} className="rounded-lg border p-5">
                  <h3 className="flex items-center gap-2 font-semibold">
                    <Icon className="h-4 w-4 text-muted-foreground" />
                    {faq.question}
                  </h3>
                  <p className="mt-2 text-sm leading-7 text-muted-foreground">{faq.answer}</p>
                </section>
              ))}
            </div>

            <Separator className="my-10" />

            <section className="text-sm leading-7 text-muted-foreground">
              <h2 className="text-base font-semibold text-foreground">Sources</h2>
              <p className="mt-2">
                OpusClip plan and watermark notes are based on the official OpusClip help center:
                {" "}
                <a href="https://help.opus.pro/docs/article/plans-and-credits" className="font-medium text-foreground underline underline-offset-4">
                  plans and credits
                </a>
                {" "}
                and
                {" "}
                <a href="https://help.opus.pro/docs/article/watermark" className="font-medium text-foreground underline underline-offset-4">
                  watermark guidance
                </a>
                .
              </p>
            </section>
          </div>

          <aside className="hidden lg:block">
            <div className="sticky top-8 space-y-4">
              <div className="rounded-lg border p-5">
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                  Article
                </p>
                <div className="mt-4 space-y-3 text-sm">
                  <a href="#quick-verdict" className="block text-muted-foreground hover:text-foreground">
                    Quick verdict
                  </a>
                  <a href="#supoclip-vs-opusclip" className="block text-muted-foreground hover:text-foreground">
                    Comparison
                  </a>
                  <a href="#faq" className="block text-muted-foreground hover:text-foreground">
                    FAQ
                  </a>
                </div>
              </div>
              <div className="rounded-lg border bg-muted/35 p-5">
                <ShieldCheck className="h-5 w-5 text-green-600" />
                <p className="mt-3 text-sm font-semibold">Open-source control</p>
                <p className="mt-2 text-sm leading-6 text-muted-foreground">
                  Use SupoClip when you want the clipping workflow without handing every decision
                  to a hosted platform.
                </p>
              </div>
            </div>
          </aside>
        </section>
      </article>
    </main>
  );
}
