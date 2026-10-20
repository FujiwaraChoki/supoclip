import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { getLocale, getTranslations } from "next-intl/server";
import { ArrowRight, Captions, ExternalLink, Github, Info, ScanFace, Sparkles, Type, Youtube } from "lucide-react";

import { RelatedGuides } from "@/components/marketing/related-guides";
import { SiteFooter } from "@/components/marketing/site-footer";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { intlLocale } from "@/i18n/config";
import { DEMO_CLIPS, DEMO_PUBLISHED_AT, DEMO_SOURCE, getDemoSourceMomentUrl } from "@/lib/demo";
import { SELF_HOSTING_GUIDE_HREF } from "@/lib/marketing-links";
import { GITHUB_URL, getSiteUrl } from "@/lib/site";

const pipelineIcons = [Type, Sparkles, Captions, ScanFace];

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("seo");
  const title = t("demo.metaTitle");
  const description = t("demo.metaDescription");
  const url = `${getSiteUrl()}/demo`;

  return {
    title: { absolute: title },
    description,
    alternates: { canonical: url },
    openGraph: {
      title,
      description,
      url,
      siteName: "SupoClip",
      type: "website",
      videos: DEMO_CLIPS.map((clip) => ({ url: `${getSiteUrl()}${clip.src}`, width: 540, height: 960, type: "video/mp4" })),
    },
    twitter: { card: "summary_large_image", title, description },
  };
}

export default async function DemoPage() {
  const t = await getTranslations("seo");
  const locale = await getLocale();
  const siteUrl = getSiteUrl();
  const lengths = new Intl.ListFormat(intlLocale(locale), { style: "long", type: "conjunction" }).format(
    DEMO_CLIPS.map((clip) => t("demo.seconds", { count: clip.previewSeconds })),
  );
  const structuredData = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "BreadcrumbList",
        itemListElement: [
          { "@type": "ListItem", position: 1, name: "SupoClip", item: siteUrl },
          { "@type": "ListItem", position: 2, name: t("demo.badge"), item: `${siteUrl}/demo` },
        ],
      },
      ...DEMO_CLIPS.map((clip) => ({
        "@type": "VideoObject",
        name: clip.hook,
        description: t("demo.videoDescription", { title: DEMO_SOURCE.title, range: clip.range, duration: clip.duration }),
        thumbnailUrl: `${siteUrl}${clip.poster}`,
        contentUrl: `${siteUrl}${clip.src}`,
        uploadDate: DEMO_PUBLISHED_AT,
        duration: `PT${clip.previewSeconds}S`,
        isBasedOn: DEMO_SOURCE.url,
      })),
    ],
  };

  return (
    <main className="min-h-screen bg-background text-foreground">
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(structuredData).replace(/</g, "\\u003c") }} />
      <header className="border-b bg-background/95">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-6">
          <Link href="/" className="flex items-center gap-2.5">
            <Image src="/logo.png" alt="SupoClip" width={24} height={24} className="rounded-lg" />
            <span className="text-lg font-bold tracking-tight">SupoClip</span>
          </Link>
          <nav className="flex items-center gap-2" aria-label={t("chrome.primaryNav")}>
            <Link href="/blog"><Button variant="ghost" size="sm">{t("chrome.blog")}</Button></Link>
            <Link href="/sign-up"><Button size="sm">{t("demo.startClipping")}</Button></Link>
          </nav>
        </div>
      </header>

      <section className="border-b bg-muted/35">
        <div className="mx-auto max-w-5xl px-6 py-14 md:py-20">
          <Badge variant="secondary">{t("demo.badge")}</Badge>
          <h1 className="mt-5 max-w-4xl text-4xl font-extrabold tracking-tight sm:text-5xl" style={{ fontFamily: "var(--font-syne), var(--font-geist-sans), system-ui" }}>
            {t("demo.heading")}
          </h1>
          <p className="mt-6 max-w-3xl text-lg leading-8 text-muted-foreground">{t("demo.intro")}</p>
          <a href={DEMO_SOURCE.url} target="_blank" rel="noopener noreferrer" className="mt-8 inline-flex items-center gap-3 rounded-lg border bg-background p-3 pr-4 text-sm transition-colors hover:border-foreground/30">
            <span className="flex h-9 w-9 items-center justify-center rounded-md bg-red-500/10"><Youtube className="h-4 w-4 text-red-500" /></span>
            <span>
              <span className="block font-medium">{DEMO_SOURCE.title}</span>
              <span className="block text-xs text-muted-foreground">{t("demo.sourceLabel")}</span>
            </span>
            <ExternalLink className="h-3.5 w-3.5 text-muted-foreground" />
          </a>
        </div>
      </section>

      <div className="mx-auto max-w-5xl px-6 py-12 md:py-16">
        <section aria-labelledby="clips-heading">
          <h2 id="clips-heading" className="text-3xl font-bold tracking-tight">{t("demo.clipsHeading")}</h2>
          <div className="mt-8 grid gap-10 md:grid-cols-2">
            {DEMO_CLIPS.map((clip) => (
              <article key={clip.id} id={clip.id} className="scroll-mt-8">
                <video
                  src={clip.src}
                  poster={clip.poster}
                  controls
                  playsInline
                  preload="none"
                  width={540}
                  height={960}
                  className="mx-auto aspect-[9/16] w-full max-w-[300px] rounded-xl bg-black shadow-lg ring-1 ring-border"
                  aria-label={t("demo.clipAria", { hook: clip.hook })}
                />
                <h3 className="mt-5 text-center text-lg font-semibold">{clip.hook}</h3>
                <p className="mt-1 text-center text-sm tabular-nums text-muted-foreground">
                  {t("demo.clipMeta", { range: clip.range, duration: clip.duration })}
                </p>
                <p className="mt-3 text-center text-sm">
                  <a href={getDemoSourceMomentUrl(clip)} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1.5 font-medium underline underline-offset-4">
                    {t("demo.watchOriginal")} <ExternalLink className="h-3.5 w-3.5" />
                  </a>
                </p>
              </article>
            ))}
          </div>
          <aside className="mt-10 flex gap-3 rounded-lg border bg-muted/40 p-5 text-sm leading-7 text-muted-foreground">
            <Info className="mt-1 h-4 w-4 shrink-0" />
            <p>
              {t.rich("demo.previewNote", {
                lengths,
                strong: (chunks) => <strong className="text-foreground">{chunks}</strong>,
              })}
            </p>
          </aside>
        </section>

        <section className="mt-16" aria-labelledby="pipeline-heading">
          <h2 id="pipeline-heading" className="text-3xl font-bold tracking-tight">{t("demo.pipelineHeading")}</h2>
          <ol className="mt-8 grid gap-4 sm:grid-cols-2">
            {pipelineIcons.map((Icon, index) => (
              <li key={index} className="rounded-lg border p-5">
                <div className="flex items-center justify-between">
                  <Icon className="h-5 w-5" />
                  <span className="font-mono text-xs text-muted-foreground">0{index + 1}</span>
                </div>
                <h3 className="mt-4 font-semibold">{t(`demo.pipeline.${index}.title`)}</h3>
                <p className="mt-1 text-sm leading-6 text-muted-foreground">{t(`demo.pipeline.${index}.detail`)}</p>
              </li>
            ))}
          </ol>
          <p className="mt-6 text-sm leading-7 text-muted-foreground">
            {t.rich("demo.pipelineNote", {
              selfHost: (chunks) => (
                <Link href={SELF_HOSTING_GUIDE_HREF} className="font-medium text-foreground underline underline-offset-4">{chunks}</Link>
              ),
              guide: (chunks) => (
                <Link href="/ai-video-clipper" className="font-medium text-foreground underline underline-offset-4">{chunks}</Link>
              ),
            })}
          </p>
        </section>

        <section className="mt-16 rounded-2xl border bg-foreground p-6 text-background md:p-8">
          <h2 className="text-2xl font-bold tracking-tight">{t("demo.ctaTitle")}</h2>
          <p className="mt-2 max-w-xl text-sm leading-6 text-background/70">{t("demo.ctaBody")}</p>
          <div className="mt-6 flex flex-wrap gap-3">
            <Link href="/sign-up"><Button variant="secondary">{t("demo.startClipping")} <ArrowRight className="h-4 w-4" /></Button></Link>
            <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer">
              <Button variant="outline" className="border-background/30 bg-transparent text-background hover:bg-background hover:text-foreground">
                <Github className="h-4 w-4" /> {t("demo.selfHostGithub")}
              </Button>
            </a>
          </div>
        </section>

        <div className="mt-16 border-t pt-12">
          <RelatedGuides href="/demo" />
        </div>
      </div>
      <SiteFooter />
    </main>
  );
}
