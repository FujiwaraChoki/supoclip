"use client";

import Image from "next/image";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { catalogKey } from "@/i18n/config";
import { getMarketingLinks } from "@/lib/marketing-links";
import { APP_STORE_URL, GITHUB_URL } from "@/lib/site";

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
        "/affiliate",
        "/status",
        "/privacy",
        "/terms",
      ],
    },
  ];

  return (
    <footer className="border-t px-6 py-12">
      <div className="mx-auto grid max-w-6xl gap-10 sm:grid-cols-2 lg:grid-cols-[1.2fr_1fr_1fr_1fr]">
        <div>
          <Link href="/" className="flex items-center gap-2">
            <Image src="/logo.png" alt="SupoClip" width={24} height={24} className="rounded-md" />
            <span className="text-sm font-semibold" style={{ fontFamily: "var(--font-syne), system-ui" }}>
              SupoClip
            </span>
          </Link>
          <p className="mt-3 max-w-xs text-xs leading-6 text-muted-foreground">
            {t("footer.tagline")}
          </p>
          <div className="mt-4 flex gap-4 text-xs text-muted-foreground">
            <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer" className={linkClassName}>{t("footer.github")}</a>
            <a href={APP_STORE_URL} target="_blank" rel="noopener noreferrer" className={linkClassName}>{t("footer.ios")}</a>
            <span>&copy; {new Date().getFullYear()}</span>
          </div>
        </div>
        {columns.map((column) => (
          <nav key={column.id} aria-label={t("footer.columnLinks", { heading: column.heading })}>
            <p className="text-xs font-semibold uppercase tracking-[0.16em]">{column.heading}</p>
            <ul className="mt-4 space-y-2.5 text-xs text-muted-foreground">
              {column.hrefs.map((href) => (
                <li key={href}>
                  <Link href={href} className={linkClassName}>{t(`links.${catalogKey(href)}.label`)}</Link>
                </li>
              ))}
            </ul>
          </nav>
        ))}
      </div>
    </footer>
  );
}
