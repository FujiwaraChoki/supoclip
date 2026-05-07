import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { ArrowRight, Captions, Clock, ExternalLink, Github, ScanFace, Sparkles, Wand2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { HOSTED_APP_URL, blogPosts, getSiteUrl } from "@/lib/blog-posts";

export const metadata: Metadata = {
  title: "SupoClip Blog | AI Video Clipping Guides",
  description:
    "SEO guides, product comparisons, and creator workflows for turning long-form video into social-ready shorts with SupoClip.",
  alternates: {
    canonical: `${getSiteUrl()}/blog`,
  },
  openGraph: {
    title: "SupoClip Blog",
    description:
      "Guides and comparisons for AI video clipping, auto captions, vertical reframing, and short-form video workflows.",
    type: "website",
    url: `${getSiteUrl()}/blog`,
    siteName: "SupoClip",
  },
};

export default function BlogIndexPage() {
  const featuredPost = blogPosts[0];

  return (
    <main className="min-h-screen bg-background text-foreground">
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

      <section className="border-b bg-muted/35">
        <div className="mx-auto max-w-6xl px-6 py-16 md:py-20">
          <div className="mb-5 flex flex-wrap gap-2">
            <Badge variant="secondary" className="gap-2">
              <Sparkles className="h-3.5 w-3.5" />
              Creator SEO Guides
            </Badge>
            <a href={HOSTED_APP_URL} target="_blank" rel="noopener noreferrer">
              <Badge variant="outline" className="gap-2">
                <ExternalLink className="h-3.5 w-3.5" />
                Try hosted SupoClip
              </Badge>
            </a>
          </div>
          <div className="max-w-3xl">
            <h1
              className="text-4xl font-extrabold tracking-tight sm:text-5xl"
              style={{ fontFamily: "var(--font-syne), var(--font-geist-sans), system-ui" }}
            >
              Practical guides for turning long videos into better shorts.
            </h1>
            <p className="mt-5 text-base leading-8 text-muted-foreground sm:text-lg">
              Comparisons, workflows, and editing advice for creators who want faster clipping,
              cleaner captions, and more control over their video pipeline.
            </p>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-6 py-12 md:py-16">
        <Link
          href={`/blog/${featuredPost.slug}`}
          className="group grid gap-8 rounded-lg border bg-card p-6 transition-colors hover:border-foreground/30 md:grid-cols-[1fr_0.55fr] md:p-8"
        >
          <article>
            <div className="mb-5 flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
              <Badge variant="outline">{featuredPost.category}</Badge>
              <span className="flex items-center gap-1.5">
                <Clock className="h-3.5 w-3.5" />
                {featuredPost.readingTime}
              </span>
            </div>
            <h2
              className="text-3xl font-bold tracking-tight sm:text-4xl"
              style={{ fontFamily: "var(--font-syne), var(--font-geist-sans), system-ui" }}
            >
              {featuredPost.title}
            </h2>
            <p className="mt-4 max-w-2xl text-base leading-7 text-muted-foreground">
              {featuredPost.description}
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <span className="inline-flex items-center gap-2 text-sm font-semibold">
                Read article
                <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
              </span>
              <span className="inline-flex items-center gap-2 text-sm text-muted-foreground">
                <Github className="h-4 w-4" />
                Open source
              </span>
            </div>
          </article>

          <div className="rounded-lg border bg-muted/45 p-5">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
              First guide
            </p>
            <div className="mt-6 space-y-4">
              {[
                { icon: Wand2, label: "Open-source setup" },
                { icon: Sparkles, label: "AI clip scoring" },
                { icon: ScanFace, label: "Face-aware 9:16 export" },
                { icon: Captions, label: "Captioned social clips" },
              ].map(({ icon: Icon, label }) => (
                <div key={label} className="flex items-center justify-between gap-4 border-b pb-3 text-sm last:border-b-0">
                  <span className="flex items-center gap-2">
                    <Icon className="h-4 w-4 text-muted-foreground" />
                    {label}
                  </span>
                  <span className="font-mono text-xs text-muted-foreground">Included</span>
                </div>
              ))}
            </div>
          </div>
        </Link>
      </section>
    </main>
  );
}
