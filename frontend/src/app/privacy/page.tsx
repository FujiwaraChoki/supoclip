import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { getLocale, getTranslations } from "next-intl/server";
import { ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { intlLocale } from "@/i18n/config";
import { HOSTED_APP_URL, getSiteUrl } from "@/lib/blog-posts";

const LAST_UPDATED = "2026-10-21";
const CONTACT_EMAIL = "privacy@supoclip.com";

function formatLegalDate(locale: string) {
  return new Intl.DateTimeFormat(intlLocale(locale), { dateStyle: "long", timeZone: "UTC" }).format(
    new Date(`${LAST_UPDATED}T00:00:00.000Z`),
  );
}

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("legal");
  const url = `${getSiteUrl()}/privacy`;

  return {
    title: t("privacy.metaTitle"),
    description: t("privacy.metaDescription"),
    alternates: { canonical: url },
    openGraph: {
      title: t("privacy.ogTitle"),
      description: t("privacy.ogDescription"),
      type: "website",
      url,
      siteName: "SupoClip",
    },
  };
}

function Section({ title, id, children }: { title: string; id?: string; children: React.ReactNode }) {
  return (
    <section id={id} className="scroll-mt-24 space-y-3">
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

export default async function PrivacyPolicyPage() {
  const t = await getTranslations("legal");
  const locale = await getLocale();
  const updated = formatLegalDate(locale);
  const email = (chunks: React.ReactNode) => (
    <a className="text-foreground underline" href={`mailto:${CONTACT_EMAIL}`}>
      {chunks}
    </a>
  );

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
            {t("privacy.title")}
          </h1>
          <p className="text-sm text-muted-foreground">{t("lastUpdated", { date: updated })}</p>
          <p className="rounded-lg border bg-muted/40 px-4 py-3 text-sm leading-6 text-muted-foreground">
            {t("bindingNotice")}
          </p>
        </div>

        <div className="mt-12 space-y-10">
          <Section title={t("privacy.overview.title")}>
            <p>{t("privacy.overview.p1")}</p>
            <p>{t("privacy.overview.p2")}</p>
          </Section>

          <Section title={t("privacy.collect.title")}>
            <p className="font-medium text-foreground">{t("privacy.collect.accountLabel")}</p>
            <p>{t("privacy.collect.account")}</p>
            <p className="font-medium text-foreground">{t("privacy.collect.contentLabel")}</p>
            <p>{t("privacy.collect.content")}</p>
            <p className="font-medium text-foreground">{t("privacy.collect.billingLabel")}</p>
            <p>{t("privacy.collect.billing")}</p>
            <p className="font-medium text-foreground">{t("privacy.collect.usageLabel")}</p>
            <p>{t("privacy.collect.usage")}</p>
            <p className="font-medium text-foreground">{t("privacy.collect.campaignLabel")}</p>
            <p>
              {t.rich("privacy.collect.campaign1", {
                utmSource: (chunks) => <code>{chunks}</code>,
                utmCampaign: (chunks) => <code>{chunks}</code>,
                ref: (chunks) => <code>{chunks}</code>,
              })}
            </p>
            <p>{t("privacy.collect.campaign2")}</p>
          </Section>

          <Section title={t("privacy.use.title")}>
            <ul className="list-disc space-y-2 pl-6">
              {(["li1", "li2", "li3", "li4", "li5", "li6", "li7", "li8"] as const).map((key) => (
                <li key={key}>{t(`privacy.use.${key}`)}</li>
              ))}
            </ul>
          </Section>

          <Section title={t("privacy.social.title")} id="connected-accounts">
            <p>{t("privacy.social.p1")}</p>
            <p className="font-medium text-foreground">{t("privacy.social.accessLabel")}</p>
            <ul className="list-disc space-y-2 pl-6">
              {(["li1", "li2", "li3"] as const).map((key) => (
                <li key={key}>
                  {t.rich(`privacy.social.${key}`, {
                    label: (chunks) => <span className="text-foreground">{chunks}</span>,
                  })}
                </li>
              ))}
            </ul>
            <p>{t("privacy.social.tokens")}</p>
            <p className="font-medium text-foreground">{t("privacy.social.disconnectLabel")}</p>
            <p>
              {t.rich("privacy.social.disconnect", {
                google: (chunks) => (
                  <a
                    className="text-foreground underline"
                    href="https://security.google.com/settings/security/permissions"
                    target="_blank"
                    rel="noreferrer"
                  >
                    {chunks}
                  </a>
                ),
                email,
              })}
            </p>
            <p className="font-medium text-foreground">{t("privacy.social.youtubeLabel")}</p>
            <p>
              {t.rich("privacy.social.youtube", {
                youtubeTerms: (chunks) => (
                  <a className="text-foreground underline" href="https://www.youtube.com/t/terms" target="_blank" rel="noreferrer">
                    {chunks}
                  </a>
                ),
                googlePrivacy: (chunks) => (
                  <a className="text-foreground underline" href="https://policies.google.com/privacy" target="_blank" rel="noreferrer">
                    {chunks}
                  </a>
                ),
                googleApi: (chunks) => (
                  <a
                    className="text-foreground underline"
                    href="https://developers.google.com/terms/api-services-user-data-policy"
                    target="_blank"
                    rel="noreferrer"
                  >
                    {chunks}
                  </a>
                ),
              })}
            </p>
          </Section>

          <Section title={t("privacy.processors.title")}>
            <p>{t("privacy.processors.intro")}</p>
            <ul className="list-disc space-y-2 pl-6">
              {(["li1", "li2", "li3", "li4", "li5", "li6", "li7", "li8"] as const).map((key) => (
                <li key={key}>
                  {t.rich(`privacy.processors.${key}`, {
                    label: (chunks) => <span className="text-foreground">{chunks}</span>,
                  })}
                </li>
              ))}
            </ul>
            <p>{t("privacy.processors.closing")}</p>
          </Section>

          <Section title={t("privacy.retention.title")}>
            <p>{t("privacy.retention.p1")}</p>
          </Section>

          <Section title={t("privacy.rights.title")}>
            <p>
              {t.rich("privacy.rights.p1", { email })}
            </p>
            <p>{t("privacy.rights.p2")}</p>
          </Section>

          <Section title={t("privacy.security.title")}>
            <p>{t("privacy.security.p1")}</p>
          </Section>

          <Section title={t("privacy.transfers.title")}>
            <p>{t("privacy.transfers.p1")}</p>
          </Section>

          <Section title={t("privacy.children.title")}>
            <p>{t("privacy.children.p1")}</p>
          </Section>

          <Section title={t("privacy.changes.title")}>
            <p>{t("privacy.changes.p1")}</p>
          </Section>

          <Section title={t("privacy.contact.title")}>
            <p>
              {t.rich("privacy.contact.body", { email })}
            </p>
          </Section>
        </div>

        <div className="mt-14 border-t pt-8 text-sm text-muted-foreground">
          <Link href="/" className="underline hover:text-foreground">
            {t("back")}
          </Link>
        </div>
      </article>
    </main>
  );
}
