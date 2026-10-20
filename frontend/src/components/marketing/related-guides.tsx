"use client";

import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { useTranslations } from "next-intl";

import { catalogKey } from "@/i18n/config";
import { getRelatedMarketingLinks } from "@/lib/marketing-links";

const HEADINGS: Record<string, "continueExploring" | "keepReading" | "relatedGuides"> = {
  "Continue exploring": "continueExploring",
  "Keep reading": "keepReading",
  "Related SupoClip guides": "relatedGuides",
};

export function RelatedGuides({ href, heading = "Continue exploring" }: { href: string; heading?: string }) {
  const t = useTranslations("marketing");
  const links = getRelatedMarketingLinks(href);
  if (links.length === 0) return null;

  const headingKey = HEADINGS[heading];
  const title = headingKey ? t(`related.${headingKey}`) : heading;

  return (
    <section aria-labelledby="related-guides-heading" className="not-prose">
      <h2 id="related-guides-heading" className="text-2xl font-bold tracking-tight">{title}</h2>
      <div className="mt-6 grid gap-4 md:grid-cols-3">
        {links.map((link) => (
          <Link
            key={link.href}
            href={link.href}
            className="group rounded-lg border p-5 !no-underline transition-colors hover:border-foreground/30"
          >
            <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{t(`links.${catalogKey(link.href)}.eyebrow`)}</p>
            <h3 className="mt-2 font-semibold text-foreground">{t(`links.${catalogKey(link.href)}.title`)}</h3>
            <p className="mt-2 text-sm leading-6 !text-muted-foreground">{t(`links.${catalogKey(link.href)}.description`)}</p>
            <span className="mt-4 inline-flex items-center gap-1.5 text-sm font-semibold text-foreground">
              {t("related.read")} <ArrowRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
            </span>
          </Link>
        ))}
      </div>
    </section>
  );
}
