import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowRight, CalendarDays, Check, ExternalLink, Github } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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

  const url = `${getSiteUrl()}/${page.slug}`;

  return {
    title: page.metaTitle,
    description: page.metaDescription,
    keywords: page.keywords,
    alternates: { canonical: url },
    openGraph: {
      title: `${page.metaTitle} | SupoClip`,
      description: page.metaDescription,
      url,
      siteName: "SupoClip",
      type: "article",
      publishedTime: page.publishedAt,
      modifiedTime: page.updatedAt,
      authors: ["SupoClip Team"],
    },
    twitter: {
      card: "summary_large_image",
      title: page.metaTitle,
      description: page.metaDescription,
    },
  };
}

export default async function SeoLandingPage({ params }: SeoLandingPageProps) {
  const { slug } = await params;
  const page = getSeoPage(slug);

  if (!page) notFound();

  const siteUrl = getSiteUrl();
  const url = `${siteUrl}/${page.slug}`;
  const relatedPages = seoPages.filter((candidate) => candidate.slug !== page.slug);
  const jsonLd = [
    {
      "@context": "https://schema.org",
      "@type": "WebPage",
      name: page.heading,
      description: page.metaDescription,
      url,
      datePublished: page.publishedAt,
      dateModified: page.updatedAt,
      author: { "@type": "Organization", name: "SupoClip Team", url: siteUrl },
      publisher: { "@type": "Organization", name: "SupoClip", url: siteUrl },
      about: { "@type": "SoftwareApplication", name: "SupoClip", url: siteUrl },
    },
    {
      "@context": "https://schema.org",
      "@type": "BreadcrumbList",
      itemListElement: [
        { "@type": "ListItem", position: 1, name: "SupoClip", item: siteUrl },
        { "@type": "ListItem", position: 2, name: page.heading, item: url },
      ],
    },
    {
      "@context": "https://schema.org",
      "@type": "FAQPage",
      mainEntity: page.faqs.map((faq) => ({
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
          <nav className="flex items-center gap-2" aria-label="Primary navigation">
            <Link href="/blog"><Button variant="ghost" size="sm">Blog</Button></Link>
            <Link href="/sign-up"><Button size="sm">Start clipping</Button></Link>
          </nav>
        </div>
      </header>

      <article>
        <section className="border-b bg-muted/35">
          <div className="mx-auto max-w-5xl px-6 py-14 md:py-20">
            <nav aria-label="Breadcrumb" className="mb-6 text-sm text-muted-foreground">
              <Link href="/" className="hover:text-foreground">SupoClip</Link>
              <span aria-hidden="true" className="mx-2">/</span>
              <span>{page.eyebrow}</span>
            </nav>
            <Badge variant="secondary">{page.eyebrow}</Badge>
            <h1 className="mt-5 max-w-4xl text-4xl font-extrabold tracking-tight sm:text-5xl lg:text-6xl">
              {page.heading}
            </h1>
            <p className="mt-6 max-w-3xl text-lg leading-8 text-muted-foreground">{page.summary}</p>
            <div className="mt-7 flex flex-wrap items-center gap-4 text-sm text-muted-foreground">
              <span>SupoClip Team</span>
              <span className="flex items-center gap-1.5">
                <CalendarDays className="h-4 w-4" />
                Updated <time dateTime={page.updatedAt}>July 27, 2026</time>
              </span>
            </div>
            <div className="mt-9 flex flex-wrap gap-3">
              <Link href="/sign-up"><Button size="lg">Try SupoClip <ArrowRight className="h-4 w-4" /></Button></Link>
              <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer">
                <Button variant="outline" size="lg"><Github className="h-4 w-4" /> View source</Button>
              </a>
            </div>
          </div>
        </section>

        <div className="mx-auto max-w-5xl px-6 py-12 md:py-16">
          <section aria-labelledby="comparison-heading">
            <h2 id="comparison-heading" className="text-3xl font-bold tracking-tight">Capability overview</h2>
            <div className="mt-6 overflow-x-auto rounded-lg border">
              <table className="w-full border-collapse text-left text-sm">
                <caption className="sr-only">{page.tableCaption}</caption>
                <thead className="bg-muted/60">
                  <tr>{page.tableHeaders.map((header) => <th key={header} scope="col" className="border-b px-4 py-3 font-semibold">{header}</th>)}</tr>
                </thead>
                <tbody>
                  {page.tableRows.map((row) => (
                    <tr key={row[0]} className="border-b last:border-b-0">
                      <th scope="row" className="px-4 py-3 font-medium">{row[0]}</th>
                      <td className="px-4 py-3 text-muted-foreground">{row[1]}</td>
                      <td className="px-4 py-3 text-muted-foreground">{row[2]}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-3 text-xs text-muted-foreground">
              First-party product data from the SupoClip repository and hosted application, verified July 27, 2026.
            </p>
          </section>

          <div className="mt-14 space-y-14">
            {page.sections.map((section) => (
              <section key={section.heading}>
                <h2 className="text-3xl font-bold tracking-tight">{section.heading}</h2>
                <div className="mt-5 space-y-5 text-base leading-8 text-muted-foreground">
                  {section.paragraphs.map((paragraph) => <p key={paragraph}>{paragraph}</p>)}
                </div>
                {section.bullets ? (
                  <ul className="mt-6 grid gap-3 sm:grid-cols-2">
                    {section.bullets.map((bullet) => (
                      <li key={bullet} className="flex items-start gap-2 rounded-lg border p-4 text-sm">
                        <Check className="mt-0.5 h-4 w-4 shrink-0" /> {bullet}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </section>
            ))}
          </div>

          <section className="mt-16" aria-labelledby="faq-heading">
            <h2 id="faq-heading" className="text-3xl font-bold tracking-tight">Frequently asked questions</h2>
            <div className="mt-6 divide-y rounded-lg border">
              {page.faqs.map((faq) => (
                <div key={faq.question} className="p-5">
                  <h3 className="font-semibold">{faq.question}</h3>
                  <p className="mt-2 leading-7 text-muted-foreground">{faq.answer}</p>
                </div>
              ))}
            </div>
          </section>

          <section className="mt-16 border-t pt-12" aria-labelledby="related-heading">
            <h2 id="related-heading" className="text-2xl font-bold">Continue exploring</h2>
            <div className="mt-6 grid gap-4 md:grid-cols-3">
              {relatedPages.map((related) => (
                <Link key={related.slug} href={`/${related.slug}`} className="rounded-lg border p-5 transition-colors hover:border-foreground/30">
                  <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{related.eyebrow}</p>
                  <h3 className="mt-2 font-semibold">{related.heading}</h3>
                </Link>
              ))}
              <Link href="/blog/best-free-opusclip-alternative" className="rounded-lg border p-5 transition-colors hover:border-foreground/30">
                <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Comparison</p>
                <h3 className="mt-2 font-semibold">Best free OpusClip alternative</h3>
              </Link>
            </div>
          </section>

          <aside className="mt-14 rounded-lg bg-muted/45 p-6 text-sm text-muted-foreground">
            <p className="font-semibold text-foreground">Sources and review policy</p>
            <p className="mt-2 leading-7">
              Product capabilities are maintained by the SupoClip Team and checked against the public
              repository. Review the <a href={GITHUB_URL} className="font-medium text-foreground underline underline-offset-4">source code <ExternalLink className="inline h-3.5 w-3.5" /></a> for implementation details. This page is updated when the workflow or supported configuration changes.
            </p>
          </aside>
        </div>
      </article>
    </main>
  );
}
