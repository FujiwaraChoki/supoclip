import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { ArrowRight, Info } from "lucide-react";

import { ExamplesCatalog } from "@/components/marketing/examples-catalog";
import { SiteFooter } from "@/components/marketing/site-footer";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { exampleClips, examplesMediaOrigin, getExamplesCatalog } from "@/lib/examples";
import { getSiteUrl } from "@/lib/site";

export const revalidate = 3600;

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("examples");
  const catalog = await getExamplesCatalog();
  const { clip } = exampleClips(catalog).sort((a, b) => b.clip.viralityScore - a.clip.viralityScore)[0];
  const title = t("meta.title");
  const description = t("meta.description");
  const url = `${getSiteUrl()}/examples`;

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
      images: [{ url: clip.posterUrl, width: clip.width, height: clip.height, alt: clip.title }],
    },
    twitter: { card: "summary_large_image", title, description, images: [clip.posterUrl] },
  };
}

export default async function ExamplesPage() {
  const t = await getTranslations("examples");
  const catalog = await getExamplesCatalog();
  const siteUrl = getSiteUrl();
  const clips = exampleClips(catalog);
  const structuredData = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "BreadcrumbList",
        itemListElement: [
          { "@type": "ListItem", position: 1, name: "SupoClip", item: siteUrl },
          { "@type": "ListItem", position: 2, name: t("badge"), item: `${siteUrl}/examples` },
        ],
      },
      {
        "@type": "ItemList",
        name: t("meta.title"),
        itemListElement: clips.map(({ source, clip }, index) => ({
          "@type": "ListItem",
          position: index + 1,
          item: {
            "@type": "VideoObject",
            name: clip.title,
            description: t("videoDescription", { title: source.title, channel: source.channel }),
            thumbnailUrl: clip.posterUrl,
            contentUrl: clip.videoUrl,
            uploadDate: catalog.generatedAt,
            duration: `PT${Math.round(clip.durationSeconds)}S`,
            isBasedOn: source.url,
          },
        })),
      },
    ],
  };

  return (
    <main className="min-h-screen bg-background text-foreground">
      <link rel="preconnect" href={examplesMediaOrigin(catalog)} />
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(structuredData).replace(/</g, "\\u003c") }} />
      <header className="border-b bg-background/95">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-6">
          <Link href="/" className="flex items-center gap-2.5">
            <Image src="/logo.png" alt="SupoClip" width={24} height={24} className="rounded-lg dark:invert" />
            <span className="text-lg font-bold tracking-tight">SupoClip</span>
          </Link>
          <nav className="flex items-center gap-2" aria-label={t("primaryNav")}>
            <Link href="/#pricing"><Button variant="ghost" size="sm">{t("cta.pricing")}</Button></Link>
            <Link href="/sign-up"><Button size="sm">{t("cta.signUp")}</Button></Link>
          </nav>
        </div>
      </header>

      <section className="border-b bg-muted/35">
        <div className="mx-auto max-w-6xl px-6 py-14 md:py-20">
          <Badge variant="secondary">{t("badge")}</Badge>
          <h1 className="mt-5 max-w-4xl font-display text-4xl font-extrabold tracking-tight sm:text-5xl">{t("heading")}</h1>
          <p className="mt-6 max-w-3xl text-lg leading-8 text-muted-foreground">
            {t("intro", { clips: clips.length, sources: catalog.sources.length })}
          </p>
        </div>
      </section>

      <div className="mx-auto max-w-6xl px-6 py-12 md:py-16">
        <ExamplesCatalog sources={catalog.sources} />

        <aside className="mt-14 flex gap-3 rounded-lg border bg-muted/40 p-5 text-sm leading-7 text-muted-foreground">
          <Info aria-hidden className="mt-1 size-4 shrink-0" />
          <p>{t("note")}</p>
        </aside>

        <section className="mt-16 rounded-2xl border bg-foreground p-6 text-background md:p-8">
          <h2 className="text-2xl font-bold tracking-tight">{t("cta.title")}</h2>
          <p className="mt-2 max-w-xl text-sm leading-6 text-background/70">{t("cta.body")}</p>
          <div className="mt-6 flex flex-wrap gap-3">
            <Link href="/sign-up"><Button variant="secondary">{t("cta.signUp")} <ArrowRight className="size-4" /></Button></Link>
            <Link href="/#pricing">
              <Button variant="outline" className="border-background/30 bg-transparent text-background hover:bg-background hover:text-foreground">
                {t("cta.pricing")}
              </Button>
            </Link>
          </div>
        </section>
      </div>
      <SiteFooter />
    </main>
  );
}
