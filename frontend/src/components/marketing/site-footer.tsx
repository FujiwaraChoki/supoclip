"use client";

import Image from "next/image";
import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
import { useTranslations } from "next-intl";

import { catalogKey } from "@/i18n/config";
import { getMarketingLink, getMarketingLinks } from "@/lib/marketing-links";
import { APP_STORE_URL, COMPANY_NAME, DISCORD_COMMUNITY_URL, GITHUB_URL } from "@/lib/site";

const linkClassName = "hover:text-foreground transition-colors";

/** Crawlable footer shared by the landing page, SEO pages, and blog. */
export function SiteFooter() {
  const t = useTranslations("marketing");
  const columns = [
    {
      id: "guides",
      heading: t("footer.columns.guides"),
      hrefs: getMarketingLinks("guide").map((link) => link.href),
    },
    {
      id: "compare",
      heading: t("footer.columns.compare"),
      hrefs: [...getMarketingLinks("comparison").map((link) => link.href), "/blog"],
    },
    {
      id: "product",
      heading: t("footer.columns.product"),
      hrefs: [
        ...getMarketingLinks("product").map((link) => link.href),
        "/sign-up",
        "/#pricing",
        "/faq",
        "/affiliate",
        "/changelog",
        "/status",
        "/privacy",
        "/terms",
      ],
    },
  ];

  return (
    <footer className="border-t px-6 py-12">
      <a
        href={DISCORD_COMMUNITY_URL}
        target="_blank"
        rel="noopener noreferrer"
        className="group mx-auto mb-12 flex max-w-6xl flex-wrap items-center gap-x-5 gap-y-3 border-b pb-10"
      >
        <span className="flex size-12 shrink-0 items-center justify-center rounded-2xl border bg-background shadow-[0_10px_24px_-14px_rgba(88,101,242,0.7)]">
          <Image src="/discord-mark.svg" alt="" width={26} height={19} className="h-auto" />
        </span>
        <span className="min-w-[13rem] flex-1">
          <span className="block text-xs font-medium text-muted-foreground">{t("footer.discord")}</span>
          <span className="mt-0.5 block font-display text-lg font-bold tracking-tight sm:text-xl">{t("community.title")}</span>
        </span>
        <span className="inline-flex items-center justify-center gap-1.5 rounded-full bg-[#5865f2] px-4 py-2 max-sm:w-full text-sm font-medium text-white transition-colors group-hover:bg-[#4752c4]">
          {t("community.join")}
          <ArrowUpRight aria-hidden="true" className="size-4" />
        </span>
      </a>
      <div className="mx-auto grid max-w-6xl gap-10 sm:grid-cols-2 lg:grid-cols-[1.2fr_1fr_1fr_1fr]">
        <div>
          <Link href="/" className="flex items-center gap-2">
            <Image src="/logo.png" alt="SupoClip" width={24} height={24} className="rounded-md dark:invert" />
            <span className="text-sm font-semibold" style={{ fontFamily: "var(--font-syne), system-ui" }}>
              SupoClip
            </span>
          </Link>
          <p className="mt-3 max-w-xs text-xs leading-6 text-muted-foreground">
            {t("footer.tagline")}
          </p>
          <p className="mt-2 text-xs leading-6 text-muted-foreground">
            {t("footer.operator", { company: COMPANY_NAME })}
          </p>
          <div className="mt-4 flex flex-wrap gap-x-4 gap-y-2 text-xs text-muted-foreground">
            <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer" className={linkClassName}>{t("footer.github")}</a>
            <a href={APP_STORE_URL} target="_blank" rel="noopener noreferrer" className={linkClassName}>{t("footer.ios")}</a>
            <span>&copy; {new Date().getFullYear()} {COMPANY_NAME}</span>
          </div>
        </div>
        {columns.map((column) => (
          <nav key={column.id} aria-label={t("footer.columnLinks", { heading: column.heading })}>
            <p className="text-sm font-medium">{column.heading}</p>
            <ul className="mt-4 space-y-2.5 text-xs text-muted-foreground">
              {column.hrefs.map((href) => (
                <li key={href}>
                  <Link href={href} lang={getMarketingLink(href)?.language} className={linkClassName}>{t(`links.${catalogKey(href)}.label`)}</Link>
                </li>
              ))}
            </ul>
          </nav>
        ))}
      </div>
    </footer>
  );
}
