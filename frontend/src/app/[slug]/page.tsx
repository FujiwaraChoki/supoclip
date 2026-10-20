import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getLocale, getTranslations } from "next-intl/server";
import { ArrowRight, CalendarDays, Check, ExternalLink, Github } from "lucide-react";

import { RelatedGuides } from "@/components/marketing/related-guides";
import { SiteFooter } from "@/components/marketing/site-footer";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { intlLocale } from "@/i18n/config";
import { GITHUB_URL, getSiteUrl } from "@/lib/site";
import { getSeoPage, seoPages } from "@/lib/seo-pages";

interface SeoLandingPageProps {
  params: Promise<{ slug: string }>;
}

export const dynamicParams = false;

export function generateStaticParams() {
  return seoPages.map((page) => ({ slug: page.slug }));
}

export async function generateMetadata({ params }: SeoLandingPageProps): Promise<Metadata> {
  const { slug } = await params;
  const page = getSeoPage(slug);

  if (!page) return {};

  const t = await getTranslations("seo");
  const url = `${getSiteUrl()}/${page.slug}`;
  const title = t(`${page.slug}.metaTitle`);
  const description = t(`${page.slug}.metaDescription`);

  return {
    title,
    description,
    keywords: page.keywords,
    alternates: { canonical: url },
    openGraph: {
      title: `${title} | SupoClip`,
      description,
      url,
      siteName: "SupoClip",
      type: "article",
      publishedTime: page.publishedAt,
      modifiedTime: page.updatedAt,
      authors: [t("chrome.team")],
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
    },
  };
}

export default async function SeoLandingPage({ params }: SeoLandingPageProps) {
  const { slug } = await params;
  const page = getSeoPage(slug);

  if (!page) notFound();

  const t = await getTranslations("seo");
  const locale = await getLocale();
  const siteUrl = getSiteUrl();
  const url = `${siteUrl}/${page.slug}`;
  const heading = t(`${slug}.heading`);
  const description = t(`${slug}.metaDescription`);
  const updatedLabel = new Intl.DateTimeFormat(intlLocale(locale), { dateStyle: "long", timeZone: "UTC" }).format(
    new Date(`${page.updatedAt}T00:00:00.000Z`),
  );
  const faqs = page.faqs.map((_, index) => ({
    question: t(`${slug}.faqs.${index}.question`),
    answer: t(`${slug}.faqs.${index}.answer`),
  }));
  const jsonLd = [
    {
      "@context": "https://schema.org",
      "@type": "WebPage",
      name: heading,
      description,
      url,
      datePublished: page.publishedAt,
      dateModified: page.updatedAt,
      author: { "@type": "Organization", name: t("chrome.team"), url: siteUrl },
      publisher: { "@type": "Organization", name: "SupoClip", url: siteUrl },
      about: { "@type": "SoftwareApplication", name: "SupoClip", url: siteUrl },
    },
    {
      "@context": "https://schema.org",
      "@type": "BreadcrumbList",
      itemListElement: [
        { "@type": "ListItem", position: 1, name: "SupoClip", item: siteUrl },
        { "@type": "ListItem", position: 2, name: heading, item: url },
      ],
    },
    {
      "@context": "https://schema.org",
      "@type": "FAQPage",
      mainEntity: faqs.map((faq) => ({
        "@type": "Question",
        name: faq.question,
        acceptedAnswer: { "@type": "Answer", text: faq.answer },
      })),
    },
  ];

  return (
    <main className="min-h-screen bg-background text-foreground">
      {jsonLd.map((entry) => (
        <script
          key={entry["@type"]}
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(entry) }}
        />
      ))}

      <header className="border-b bg-background/95">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-6">
          <Link href="/" className="flex items-center gap-2.5">
            <Image src="/logo.png" alt="SupoClip" width={24} height={24} className="rounded-lg" />
            <span className="text-lg font-bold tracking-tight">SupoClip</span>
          </Link>
          <nav className="flex items-center gap-2" aria-label={t("chrome.primaryNav")}>
            <Link href="/blog"><Button variant="ghost" size="sm">{t("chrome.blog")}</Button></Link>
            <Link href="/blog/self-host-supoclip-docker" className="hidden sm:block"><Button variant="ghost" size="sm">{t("chrome.selfHost")}</Button></Link>
            <Link href="/sign-up"><Button size="sm">{t("chrome.startClipping")}</Button></Link>
          </nav>
        </div>
      </header>

      <article>
        <section className="border-b bg-muted/35">
          <div className="mx-auto max-w-5xl px-6 py-14 md:py-20">
            <nav aria-label={t("chrome.breadcrumbNav")} className="mb-6 text-sm text-muted-foreground">
              <Link href="/" className="hover:text-foreground">SupoClip</Link>
              <span aria-hidden="true" className="mx-2">/</span>
              <span>{t(`${slug}.eyebrow`)}</span>
            </nav>
            <Badge variant="secondary">{t(`${slug}.eyebrow`)}</Badge>
            <h1 className="mt-5 max-w-4xl text-4xl font-extrabold tracking-tight sm:text-5xl lg:text-6xl">
              {heading}
            </h1>
            <p className="mt-6 max-w-3xl text-lg leading-8 text-muted-foreground">{t(`${slug}.summary`)}</p>
            <div className="mt-7 flex flex-wrap items-center gap-4 text-sm text-muted-foreground">
              <span>{t("chrome.team")}</span>
              <span className="flex items-center gap-1.5">
                <CalendarDays className="h-4 w-4" />
                {t.rich("chrome.updated", {
                  when: updatedLabel,
                  date: (chunks) => <time dateTime={page.updatedAt}>{chunks}</time>,
                })}
              </span>
            </div>
            <div className="mt-9 flex flex-wrap gap-3">
              <Link href="/sign-up"><Button size="lg">{t("chrome.trySupoclip")} <ArrowRight className="h-4 w-4" /></Button></Link>
              <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer">
                <Button variant="outline" size="lg"><Github className="h-4 w-4" /> {t("chrome.viewSource")}</Button>
              </a>
            </div>
          </div>
        </section>

        <div className="mx-auto max-w-5xl px-6 py-12 md:py-16">
          <section aria-labelledby="comparison-heading">
            <h2 id="comparison-heading" className="text-3xl font-bold tracking-tight">{t("chrome.capabilityOverview")}</h2>
            <div className="mt-6 overflow-x-auto rounded-lg border">
              <table className="w-full border-collapse text-left text-sm">
                <caption className="sr-only">{t(`${slug}.tableCaption`)}</caption>
                <thead className="bg-muted/60">
                  <tr>
                    {page.tableHeaders.map((_, index) => (
                      <th key={index} scope="col" className="border-b px-4 py-3 font-semibold">{t(`${slug}.tableHeaders.${index}`)}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {page.tableRows.map((_row, rowIndex) => (
                    <tr key={rowIndex} className="border-b last:border-b-0">
                      <th scope="row" className="px-4 py-3 font-medium">{t(`${slug}.tableRows.${rowIndex}.0`)}</th>
                      <td className="px-4 py-3 text-muted-foreground">{t(`${slug}.tableRows.${rowIndex}.1`)}</td>
                      <td className="px-4 py-3 text-muted-foreground">{t(`${slug}.tableRows.${rowIndex}.2`)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-3 text-xs text-muted-foreground">
              {t("chrome.verified", { date: updatedLabel })}
            </p>
          </section>

          <div className="mt-14 space-y-14">
            {page.sections.map((section, index) => (
              <section key={index}>
                <h2 className="text-3xl font-bold tracking-tight">{t(`${slug}.sections.${index}.heading`)}</h2>
                <div className="mt-5 space-y-5 text-base leading-8 text-muted-foreground">
                  {section.paragraphs.map((_, paragraphIndex) => (
                    <p key={paragraphIndex}>{t(`${slug}.sections.${index}.p${paragraphIndex + 1}`)}</p>
                  ))}
                </div>
                {section.bullets ? (
                  <ul className="mt-6 grid gap-3 sm:grid-cols-2">
                    {section.bullets.map((_, bulletIndex) => (
                      <li key={bulletIndex} className="flex items-start gap-2 rounded-lg border p-4 text-sm">
                        <Check className="mt-0.5 h-4 w-4 shrink-0" /> {t(`${slug}.sections.${index}.bullet${bulletIndex + 1}`)}
                      </li>
                    ))}
                  </ul>
                ) : null}
                {section.links ? (
                  <p className="mt-6 flex flex-wrap gap-x-5 gap-y-2 text-sm">
                    {section.links.map((link, linkIndex) => (
                      <Link key={link.href} href={link.href} className="inline-flex items-center gap-1.5 font-medium underline underline-offset-4">
                        {t(`${slug}.sections.${index}.link${linkIndex + 1}`)} <ArrowRight className="h-3.5 w-3.5" />
                      </Link>
                    ))}
                  </p>
                ) : null}
              </section>
            ))}
          </div>

          <section className="mt-16" aria-labelledby="faq-heading">
            <h2 id="faq-heading" className="text-3xl font-bold tracking-tight">{t("chrome.faqHeading")}</h2>
            <div className="mt-6 divide-y rounded-lg border">
              {faqs.map((faq) => (
                <div key={faq.question} className="p-5">
                  <h3 className="font-semibold">{faq.question}</h3>
                  <p className="mt-2 leading-7 text-muted-foreground">{faq.answer}</p>
                </div>
              ))}
            </div>
          </section>

          <div className="mt-16 border-t pt-12">
            <RelatedGuides href={`/${page.slug}`} />
          </div>

          <aside className="mt-14 rounded-lg bg-muted/45 p-6 text-sm text-muted-foreground">
            <p className="font-semibold text-foreground">{t("chrome.sourcesTitle")}</p>
            <p className="mt-2 leading-7">
              {t.rich("chrome.sourcesBody", {
                source: (chunks) => (
                  <a href={GITHUB_URL} className="font-medium text-foreground underline underline-offset-4">
                    {chunks} <ExternalLink className="inline h-3.5 w-3.5" />
                  </a>
                ),
              })}
            </p>
          </aside>
        </div>
      </article>
      <SiteFooter />
    </main>
  );
}
