import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { getLocale, getTranslations } from "next-intl/server";
import { ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { intlLocale } from "@/i18n/config";
import { HOSTED_APP_URL, getSiteUrl } from "@/lib/blog-posts";

const LAST_UPDATED = "2026-10-21";
const CONTACT_EMAIL = "support@supoclip.com";

function formatLegalDate(locale: string) {
  return new Intl.DateTimeFormat(intlLocale(locale), { dateStyle: "long", timeZone: "UTC" }).format(
    new Date(`${LAST_UPDATED}T00:00:00.000Z`),
  );
}

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("legal");
  const url = `${getSiteUrl()}/terms`;

  return {
    title: t("terms.metaTitle"),
    description: t("terms.metaDescription"),
    alternates: { canonical: url },
    openGraph: {
      title: t("terms.ogTitle"),
      description: t("terms.ogDescription"),
      type: "website",
      url,
      siteName: "SupoClip",
    },
  };
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <h2
        className="text-2xl font-bold tracking-tight"
        style={{ fontFamily: "var(--font-syne), var(--font-geist-sans), system-ui" }}
      >
        {title}
      </h2>
      <div className="space-y-3 text-base leading-7 text-muted-foreground">{children}</div>
    </section>
  );
}

export default async function TermsOfServicePage() {
  const t = await getTranslations("legal");
  const locale = await getLocale();
  const updated = formatLegalDate(locale);

  return (
    <main className="min-h-screen bg-background text-foreground">
      <header className="border-b bg-background/95">
        <div className="mx-auto flex h-16 max-w-3xl items-center justify-between px-6">
          <Link href="/" className="flex items-center gap-2.5">
            <Image src="/logo.png" alt="SupoClip" width={24} height={24} className="rounded-lg" />
            <span
              className="text-lg font-bold tracking-tight"
              style={{ fontFamily: "var(--font-syne), var(--font-geist-sans), system-ui" }}
            >
              SupoClip
            </span>
          </Link>
          <a href={HOSTED_APP_URL} target="_blank" rel="noopener noreferrer">
            <Button variant="ghost" size="sm" className="hidden sm:inline-flex">
              {t("hostedApp")}
              <ExternalLink className="h-3.5 w-3.5" />
            </Button>
          </a>
        </div>
      </header>

      <article className="mx-auto max-w-3xl px-6 py-14 md:py-20">
        <div className="space-y-3">
          <h1
            className="text-4xl font-extrabold tracking-tight sm:text-5xl"
            style={{ fontFamily: "var(--font-syne), var(--font-geist-sans), system-ui" }}
          >
            {t("terms.title")}
          </h1>
          <p className="text-sm text-muted-foreground">{t("lastUpdated", { date: updated })}</p>
          <p className="rounded-lg border bg-muted/40 px-4 py-3 text-sm leading-6 text-muted-foreground">
            {t("bindingNotice")}
          </p>
        </div>

        <div className="mt-12 space-y-10">
          <Section title={t("terms.agreement.title")}>
            <p>{t("terms.agreement.p1")}</p>
            <p>{t("terms.agreement.p2")}</p>
          </Section>

          <Section title={t("terms.account.title")}>
            <p>{t("terms.account.p1")}</p>
          </Section>

          <Section title={t("terms.content.title")}>
            <p>{t("terms.content.p1")}</p>
            <p>{t("terms.content.p2")}</p>
          </Section>

          <Section title={t("terms.billing.title")}>
            <p>{t("terms.billing.p1")}</p>
            <p className="font-medium text-foreground">{t("terms.billing.webLabel")}</p>
            <p>{t("terms.billing.web")}</p>
            <p className="font-medium text-foreground">{t("terms.billing.iosLabel")}</p>
            <p>{t("terms.billing.ios")}</p>
            <p>{t("terms.billing.p2")}</p>
          </Section>

          <Section title={t("terms.acceptable.title")}>
            <ul className="list-disc space-y-2 pl-6">
              <li>{t("terms.acceptable.li1")}</li>
              <li>{t("terms.acceptable.li2")}</li>
              <li>{t("terms.acceptable.li3")}</li>
              <li>{t("terms.acceptable.li4")}</li>
            </ul>
            <p>{t("terms.acceptable.closing")}</p>
          </Section>

          <Section title={t("terms.ip.title")}>
            <p>{t("terms.ip.p1")}</p>
          </Section>

          <Section title={t("terms.disclaimers.title")}>
            <p>{t("terms.disclaimers.p1")}</p>
          </Section>

          <Section title={t("terms.liability.title")}>
            <p>{t("terms.liability.p1")}</p>
          </Section>

          <Section title={t("terms.termination.title")}>
            <p>{t("terms.termination.p1")}</p>
          </Section>

          <Section title={t("terms.changes.title")}>
            <p>{t("terms.changes.p1")}</p>
          </Section>

          <Section title={t("terms.contact.title")}>
            <p>
              {t.rich("terms.contact.body", {
                email: (chunks) => (
                  <a className="text-foreground underline" href={`mailto:${CONTACT_EMAIL}`}>
                    {chunks}
                  </a>
                ),
              })}
            </p>
          </Section>
        </div>

        <div className="mt-14 border-t pt-8 text-sm text-muted-foreground">
          <Link href="/privacy" className="underline hover:text-foreground">
            {t("terms.privacyLink")}
          </Link>
          <span className="mx-2">&middot;</span>
          <Link href="/" className="underline hover:text-foreground">
            {t("back")}
          </Link>
        </div>
      </article>
    </main>
  );
}
