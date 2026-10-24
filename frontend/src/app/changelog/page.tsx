import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { getLocale, getTranslations } from "next-intl/server";
import { ExternalLink } from "lucide-react";

import { SiteFooter } from "@/components/marketing/site-footer";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { intlLocale } from "@/i18n/config";
import { readChangelog, type ChangelogCategory } from "@/lib/changelog";
import { GITHUB_URL, HOSTED_APP_URL, getSiteUrl } from "@/lib/site";

const GITHUB_CHANGELOG_URL = `${GITHUB_URL}/blob/main/CHANGELOG.md`;

const badgeVariant: Record<ChangelogCategory, "default" | "secondary" | "outline"> = {
  new: "default",
  improved: "secondary",
  fixed: "outline",
  underTheHood: "outline",
};

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("changelog");
  const url = `${getSiteUrl()}/changelog`;

  return {
    title: { absolute: t("metaTitle") },
    description: t("metaDescription"),
    alternates: { canonical: url },
    openGraph: {
      title: t("ogTitle"),
      description: t("ogDescription"),
      type: "website",
      url,
      siteName: "SupoClip",
    },
    twitter: {
      card: "summary",
      title: t("ogTitle"),
      description: t("ogDescription"),
    },
  };
}

export default async function ChangelogPage() {
  const t = await getTranslations("changelog");
  const locale = await getLocale();
  const releases = readChangelog();
  const formatDate = new Intl.DateTimeFormat(intlLocale(locale), {
    dateStyle: "long",
    timeZone: "UTC",
  });
  const categoryLabel: Record<ChangelogCategory, string> = {
    new: t("categories.new"),
    improved: t("categories.improved"),
    fixed: t("categories.fixed"),
    underTheHood: t("categories.underTheHood"),
  };

  return (
    <main className="min-h-screen bg-background text-foreground">
      <header className="border-b bg-background/95">
        <div className="mx-auto flex h-16 max-w-3xl items-center justify-between px-6">
          <Link href="/" className="flex items-center gap-2.5">
            <Image src="/logo.png" alt="SupoClip" width={24} height={24} className="rounded-lg" />
            <span className="font-display text-lg font-bold tracking-tight">SupoClip</span>
          </Link>
          <div className="flex items-center gap-2">
            <a href={HOSTED_APP_URL} target="_blank" rel="noopener noreferrer">
              <Button variant="ghost" size="sm" className="hidden sm:inline-flex">
                {t("hostedApp")}
                <ExternalLink className="h-3.5 w-3.5" />
              </Button>
            </a>
            <Link href="/sign-up">
              <Button size="sm">{t("startClipping")}</Button>
            </Link>
          </div>
        </div>
      </header>

      <section className="border-b bg-muted/35">
        <div className="mx-auto max-w-3xl px-6 py-16 md:py-20">
          <Badge variant="secondary">{t("eyebrow")}</Badge>
          <h1 className="font-display mt-5 text-4xl font-extrabold tracking-tight sm:text-5xl">{t("title")}</h1>
          <p className="mt-5 max-w-2xl text-base leading-8 text-muted-foreground sm:text-lg">{t("intro")}</p>
        </div>
      </section>

      <div className="mx-auto max-w-3xl px-6 py-12 md:py-16">
        {releases.map((release) => (
          <section key={release.date} aria-labelledby={`changelog-${release.date}`} className="border-b py-10 first:pt-0 last:border-b-0">
            <h2 id={`changelog-${release.date}`} className="font-display text-2xl font-bold tracking-tight">
              <time dateTime={release.date}>{formatDate.format(new Date(`${release.date}T00:00:00.000Z`))}</time>
            </h2>
            <ul className="mt-6 space-y-6">
              {release.entries.map((entry) => (
                <li key={`${release.date}-${entry.category}-${entry.text}`} className="grid gap-3 sm:grid-cols-[9.5rem_1fr] sm:gap-6">
                  <div>
                    <Badge variant={badgeVariant[entry.category]}>{categoryLabel[entry.category]}</Badge>
                  </div>
                  <p className="text-sm leading-7 text-foreground">
                    {entry.text}{" "}
                    {entry.links.map((link, index) => (
                      <span key={link.href}>
                        {index > 0 ? ", " : null}
                        <a
                          href={link.href}
                          target="_blank"
                          rel="noopener noreferrer"
                          aria-label={t("relatedChange", { label: link.label })}
                          className="font-medium underline decoration-foreground/30 underline-offset-4 hover:decoration-foreground"
                        >
                          {link.label}
                        </a>
                      </span>
                    ))}
                  </p>
                </li>
              ))}
            </ul>
          </section>
        ))}

        <p className="pt-4 text-sm text-muted-foreground">
          <a href={GITHUB_CHANGELOG_URL} target="_blank" rel="noopener noreferrer" className="font-medium text-foreground underline-offset-4 hover:underline">
            {t("viewOnGitHub")}
          </a>
        </p>
      </div>
      <SiteFooter />
    </main>
  );
}
