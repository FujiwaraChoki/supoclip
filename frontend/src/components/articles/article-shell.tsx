import Image from "next/image";
import Link from "next/link";
import { getLocale, getTranslations } from "next-intl/server";
import { ArrowRight, Github } from "lucide-react";

import { RelatedGuides } from "@/components/marketing/related-guides";
import { SiteFooter } from "@/components/marketing/site-footer";
import { intlLocale } from "@/i18n/config";
import { type BlogPost, getSiteUrl } from "@/lib/blog-posts";
import { GITHUB_URL } from "@/lib/site";

export interface ArticleFaq {
  question: string;
  answer: string;
}

export const articleProseClassName =
  "space-y-6 leading-8 [&_h2]:scroll-mt-8 [&_h2]:pt-6 [&_h2]:text-2xl [&_h2]:font-bold [&_h2]:tracking-tight [&_h3]:pt-2 [&_h3]:text-lg [&_h3]:font-semibold [&_a]:font-medium [&_a]:underline [&_a]:underline-offset-4 [&_p]:text-muted-foreground [&_li]:text-muted-foreground [&_ul]:ml-5 [&_ul]:list-disc [&_ul]:space-y-2 [&_ol]:ml-5 [&_ol]:list-decimal [&_ol]:space-y-2 [&_code]:rounded [&_code]:bg-muted [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-[0.85em] [&_code]:text-foreground";

export function formatArticleDate(date: string, locale: string) {
  return new Intl.DateTimeFormat(intlLocale(locale), { dateStyle: "long", timeZone: "UTC" }).format(new Date(`${date}T00:00:00.000Z`));
}

export function CodeBlock({ children, label }: { children: string; label?: string }) {
  return (
    <figure className="not-prose overflow-hidden rounded-lg border bg-zinc-950 text-zinc-100">
      {label ? <figcaption className="border-b border-zinc-800 px-4 py-2 font-mono text-xs text-zinc-400">{label}</figcaption> : null}
      <pre className="overflow-x-auto p-4 text-[13px] leading-6"><code className="!bg-transparent !p-0 !text-[13px] !text-zinc-100">{children.trim()}</code></pre>
    </figure>
  );
}

/** Header, breadcrumb, JSON-LD, related links, and footer shared by long-form blog articles. */
export async function ArticleShell({
  post,
  breadcrumb,
  toc,
  faqs,
  children,
}: {
  post: BlogPost;
  breadcrumb: string;
  toc: Array<[id: string, label: string]>;
  faqs: ArticleFaq[];
  children: React.ReactNode;
}) {
  const t = await getTranslations("blog");
  const locale = await getLocale();
  const siteUrl = getSiteUrl();
  const url = `${siteUrl}/blog/${post.slug}`;
  const updatedLabel = formatArticleDate(post.updatedAt, locale);
  const structuredData = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "BreadcrumbList",
        itemListElement: [
          { "@type": "ListItem", position: 1, name: t("shell.home"), item: siteUrl },
          { "@type": "ListItem", position: 2, name: t("shell.blog"), item: `${siteUrl}/blog` },
          { "@type": "ListItem", position: 3, name: breadcrumb, item: url },
        ],
      },
      {
        "@type": "BlogPosting",
        headline: post.title,
        description: post.description,
        datePublished: post.publishedAt,
        dateModified: post.updatedAt,
        author: { "@type": "Organization", name: post.author },
        publisher: { "@type": "Organization", name: "SupoClip", url: siteUrl, logo: { "@type": "ImageObject", url: `${siteUrl}/logo.png` } },
        articleSection: post.category,
        keywords: post.keywords.join(", "),
        inLanguage: intlLocale(locale),
        mainEntityOfPage: url,
      },
      {
        "@type": "FAQPage",
        mainEntity: faqs.map(({ question, answer }) => ({
          "@type": "Question",
          name: question,
          acceptedAnswer: { "@type": "Answer", text: answer },
        })),
      },
    ],
  };

  return (
    <main className="min-h-screen bg-background text-foreground">
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(structuredData).replace(/</g, "\\u003c") }} />
      <header className="border-b">
        <nav aria-label={t("shell.blogNav")} className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-6 py-5">
          <Link href="/" className="flex items-center gap-2.5 text-xl font-bold">
            <Image src="/logo.png" alt="" width={30} height={30} className="rounded-lg" />SupoClip
          </Link>
          <div className="flex items-center gap-5 text-sm">
            <Link href="/blog" className="underline underline-offset-4">{t("shell.allArticles")}</Link>
            <Link href="/sign-up" className="hidden rounded-lg bg-foreground px-4 py-2 font-semibold text-background sm:inline-flex">{t("shell.startClipping")}</Link>
          </div>
        </nav>
      </header>
      <article className="mx-auto max-w-5xl px-5 py-10 sm:px-8 sm:py-16">
        <nav aria-label={t("shell.breadcrumbNav")} className="mb-8 flex flex-wrap gap-2 text-sm text-muted-foreground">
          <Link href="/">{t("shell.home")}</Link><span aria-hidden="true">/</span><Link href="/blog">{t("shell.blog")}</Link><span aria-hidden="true">/</span><span>{breadcrumb}</span>
        </nav>
        <header className="mb-10 max-w-3xl space-y-5">
          <p className="inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-semibold uppercase tracking-widest">{post.eyebrow}</p>
          <h1 className="text-4xl font-extrabold leading-[1.08] tracking-tight sm:text-6xl" style={{ fontFamily: "var(--font-syne), var(--font-geist-sans), sans-serif" }}>{post.title}</h1>
          <p className="text-xl leading-8 text-muted-foreground">{post.summary}</p>
          <p className="text-sm text-muted-foreground">
            {t.rich("shell.byline", {
              when: updatedLabel,
              readingTime: post.readingTime,
              date: (chunks) => <time dateTime={post.updatedAt}>{chunks}</time>,
            })}
          </p>
        </header>
        <nav aria-label={t("shell.inThisArticle")} className="mb-10 flex flex-wrap gap-x-5 gap-y-2 border-y py-4 text-sm font-medium">
          {toc.map(([id, label]) => <a key={id} href={`#${id}`} className="underline-offset-4 hover:underline">{label}</a>)}
        </nav>
        <div className={`mx-auto max-w-3xl ${articleProseClassName}`}>
          {children}
          <h2 id="faq">{t("shell.faqHeading")}</h2>
          {faqs.map(({ question, answer }) => (
            <section key={question} className="space-y-2 rounded-lg border p-5">
              <h3 className="!pt-0">{question}</h3>
              <p>{answer}</p>
            </section>
          ))}
          <section className="not-prose rounded-2xl border bg-muted/30 p-6 sm:p-8" aria-label={t("shell.startAria")}>
            <h2 className="!pt-0 text-2xl font-bold">{t("shell.ctaTitle")}</h2>
            <p className="mt-2 text-muted-foreground">{t("shell.ctaBody")}</p>
            <div className="mt-5 flex flex-wrap gap-3">
              <Link href="/sign-up" className="inline-flex items-center gap-2 rounded-lg bg-foreground px-5 py-3 text-sm font-semibold !text-background !no-underline">{t("shell.startClipping")}<ArrowRight className="h-4 w-4" /></Link>
              <a href={GITHUB_URL} className="inline-flex items-center gap-2 rounded-lg border px-5 py-3 text-sm font-semibold !no-underline"><Github className="h-4 w-4" />{t("shell.getSource")}</a>
            </div>
          </section>
        </div>
        <div className="mx-auto mt-16 max-w-5xl border-t pt-12">
          <RelatedGuides href={`/blog/${post.slug}`} heading={t("shell.keepReading")} />
        </div>
      </article>
      <SiteFooter />
    </main>
  );
}
