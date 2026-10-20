import { formatArticleDate } from "@/components/articles/article-shell";
import { OpusClipComparisonArticle } from "@/components/articles/opusclip-comparison-article";
import { SelfHostingGuideArticle } from "@/components/articles/self-hosting-guide-article";
import { RelatedGuides } from "@/components/marketing/related-guides";
import { SiteFooter } from "@/components/marketing/site-footer";
import { SupoLiveArticle } from "@/components/supo-live-article";
import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getLocale, getTranslations } from "next-intl/server";
import {
  ArrowRight,
  Captions,
  Clock,
  ExternalLink,
  Github,
  ListChecks,
  Play,
  ScanFace,
  ShieldCheck,
  Sparkles,
  Wand2,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { intlLocale } from "@/i18n/config";
import { applyBlogCopy, HOSTED_APP_URL, blogPosts, getBlogPost, getBlogPostMetadata, getSiteUrl } from "@/lib/blog-posts";
import { OPUSCLIP_COMPARISON_HREF, SELF_HOSTING_GUIDE_HREF } from "@/lib/marketing-links";

interface BlogPostPageProps {
  params: Promise<{
    slug: string;
  }>;
}

const comparisonIcons = [ShieldCheck, Sparkles, ScanFace, Github];
const faqIcons = [Sparkles, ShieldCheck, Captions];
const audienceIcons = [Play, ShieldCheck, Github];
const statIcons = [ShieldCheck, Wand2, Github];

const articleBodyClassName =
  "space-y-6 text-base leading-8 text-foreground [&_a]:font-medium [&_a]:text-foreground [&_a]:underline [&_a]:underline-offset-4 [&_h2]:mt-12 [&_h2]:text-2xl [&_h2]:font-bold [&_h2]:tracking-tight [&_h2]:text-foreground [&_h2]:first:mt-0 [&_li]:mt-2 [&_ol]:ml-5 [&_ol]:list-decimal [&_p]:text-muted-foreground";

export function generateStaticParams() {
  return blogPosts.map((post) => ({ slug: post.slug }));
}

export async function generateMetadata({ params }: BlogPostPageProps): Promise<Metadata> {
  const { slug } = await params;
  const post = getBlogPost(slug);

  if (!post) {
    return {};
  }

  const t = await getTranslations("blog");
  return getBlogPostMetadata(applyBlogCopy(post, (key) => t(key)));
}

export default async function BlogPostPage({ params }: BlogPostPageProps) {
  const { slug } = await params;
  const basePost = getBlogPost(slug);

  if (!basePost) {
    notFound();
  }

  const t = await getTranslations("blog");
  const locale = await getLocale();
  const post = applyBlogCopy(basePost, (key) => t(key));

  if (slug === "supoclip-vs-supo-live") {
    return <SupoLiveArticle post={post} />;
  }
  if (slug === "supoclip-vs-opusclip") {
    return <OpusClipComparisonArticle post={post} />;
  }
  if (slug === "self-host-supoclip-docker") {
    return <SelfHostingGuideArticle post={post} />;
  }

  const key = "best-free-opusclip-alternative";
  const siteUrl = getSiteUrl();
  const articleUrl = `${siteUrl}/blog/${post.slug}`;
  const faqs = [0, 1, 2].map((index) => ({
    question: t(`${key}.faqs.${index}.question`),
    answer: t(`${key}.faqs.${index}.answer`),
  }));
  const articleJsonLd = {
    "@context": "https://schema.org",
    "@type": "BlogPosting",
    headline: post.title,
    description: post.description,
    datePublished: post.publishedAt,
    dateModified: post.updatedAt,
    author: {
      "@type": "Organization",
      name: post.author,
    },
    publisher: {
      "@type": "Organization",
      name: "SupoClip",
      logo: {
        "@type": "ImageObject",
        url: `${siteUrl}/logo.png`,
      },
    },
    inLanguage: intlLocale(locale),
    mainEntityOfPage: articleUrl,
  };
  const faqJsonLd = {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: faqs.map((faq) => ({
      "@type": "Question",
      name: faq.question,
      acceptedAnswer: {
        "@type": "Answer",
        text: faq.answer,
      },
    })),
  };

  return (
    <main className="min-h-screen bg-background text-foreground">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(articleJsonLd) }}
      />
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(faqJsonLd) }}
      />

      <header className="border-b bg-background/95">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-6">
          <Link href="/" className="flex items-center gap-2.5">
            <Image src="/logo.png" alt="SupoClip" width={24} height={24} className="rounded-lg" />
            <span
              className="text-lg font-bold tracking-tight"
              style={{ fontFamily: "var(--font-syne), var(--font-geist-sans), system-ui" }}
            >
              SupoClip
            </span>
          </Link>
          <div className="flex items-center gap-2">
            <Link href="/blog">
              <Button variant="ghost" size="sm">
                {t("shell.blog")}
              </Button>
            </Link>
            <a href={HOSTED_APP_URL} target="_blank" rel="noopener noreferrer">
              <Button variant="ghost" size="sm" className="hidden sm:inline-flex">
                {t("index.hostedApp")}
                <ExternalLink className="h-3.5 w-3.5" />
              </Button>
            </a>
            <Link href="/sign-up">
              <Button size="sm">{t("index.startClipping")}</Button>
            </Link>
          </div>
        </div>
      </header>

      <article>
        <section className="border-b bg-muted/35">
          <div className="mx-auto grid max-w-6xl gap-10 px-6 py-14 md:grid-cols-[1fr_0.42fr] md:py-20">
            <div>
              <div className="mb-5 flex flex-wrap gap-2">
                <Badge variant="secondary" className="gap-2">
                  <Sparkles className="h-3.5 w-3.5" />
                  {post.eyebrow}
                </Badge>
                <a href={HOSTED_APP_URL} target="_blank" rel="noopener noreferrer">
                  <Badge variant="outline" className="gap-2">
                    <Play className="h-3.5 w-3.5" />
                    {t("index.tryHosted")}
                  </Badge>
                </a>
              </div>
              <h1
                className="max-w-4xl text-4xl font-extrabold tracking-tight sm:text-5xl lg:text-6xl"
                style={{ fontFamily: "var(--font-syne), var(--font-geist-sans), system-ui" }}
              >
                {post.title}
              </h1>
              <p className="mt-6 max-w-2xl text-lg leading-8 text-muted-foreground">
                {t(`${key}.lead`)}
              </p>
              <div className="mt-8 flex flex-wrap items-center gap-4 text-sm text-muted-foreground">
                <span>{post.author}</span>
                <span>
                  {t.rich("shell.updated", {
                    when: formatArticleDate(post.updatedAt, locale),
                    date: (chunks) => <time dateTime={post.updatedAt}>{chunks}</time>,
                  })}
                </span>
                <span className="flex items-center gap-1.5">
                  <Clock className="h-3.5 w-3.5" />
                  {post.readingTime}
                </span>
              </div>
            </div>

            <aside className="rounded-lg border bg-background p-5 md:self-end">
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                {t(`${key}.bestFor`)}
              </p>
              <ul className="mt-5 space-y-3 text-sm">
                {audienceIcons.map((Icon, index) => (
                  <li key={index} className="flex gap-3">
                    <Icon className="mt-0.5 h-4 w-4 shrink-0 text-green-600" />
                    <span>{t(`${key}.audience.${index}`)}</span>
                  </li>
                ))}
              </ul>
              <a href={HOSTED_APP_URL} target="_blank" rel="noopener noreferrer" className="mt-5 inline-flex text-sm font-semibold">
                {t(`${key}.openHosted`)}
                <ExternalLink className="ml-2 h-4 w-4" />
              </a>
            </aside>
          </div>
        </section>

        <section className="mx-auto grid max-w-6xl gap-12 px-6 py-12 lg:grid-cols-[minmax(0,1fr)_280px] lg:py-16">
          <div className="min-w-0">
            <div className={articleBodyClassName}>
              <p>{t(`${key}.p1`)}</p>
              <p>
                {t.rich(`${key}.p2`, {
                  hosted: (chunks) => (
                    <a href={HOSTED_APP_URL} target="_blank" rel="noopener noreferrer">{chunks}</a>
                  ),
                })}
              </p>

              <h2 id="quick-verdict">{t(`${key}.verdict.heading`)}</h2>
              <p>{t(`${key}.verdict.body`)}</p>

              <div className="not-prose my-8 grid gap-4 sm:grid-cols-3">
                {statIcons.map((Icon, index) => (
                  <div key={index} className="rounded-lg border bg-muted/35 p-4">
                    <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                      <Icon className="h-4 w-4" />
                      {t(`${key}.stats.${index}.label`)}
                    </p>
                    <p className="mt-2 text-lg font-semibold">{t(`${key}.stats.${index}.value`)}</p>
                  </div>
                ))}
              </div>

              <h2>{t(`${key}.why.heading`)}</h2>
              <p>{t(`${key}.why.p1`)}</p>
              <p>{t(`${key}.why.p2`)}</p>

              <h2>{t(`${key}.different.heading`)}</h2>
              <p>{t(`${key}.different.p1`)}</p>
              <p>
                {t.rich(`${key}.different.p2`, {
                  guide: (chunks) => <Link href={SELF_HOSTING_GUIDE_HREF}>{chunks}</Link>,
                })}
              </p>

              <h2 id="supoclip-vs-opusclip">{t(`${key}.compare.heading`)}</h2>
              <p>
                {t.rich(`${key}.compare.body`, {
                  comparison: (chunks) => <Link href={OPUSCLIP_COMPARISON_HREF}>{chunks}</Link>,
                })}
              </p>
            </div>

            <div className="my-8 overflow-hidden rounded-lg border">
              <div className="grid grid-cols-[0.85fr_1fr_1fr] bg-muted/50 text-sm font-semibold">
                <div className="p-4">{t(`${key}.table.feature`)}</div>
                <div className="border-l p-4">SupoClip</div>
                <div className="border-l p-4">OpusClip</div>
              </div>
              {comparisonIcons.map((Icon, index) => (
                <div key={index} className="grid grid-cols-[0.85fr_1fr_1fr] border-t text-sm">
                  <div className="flex items-start gap-2 p-4 font-medium">
                    <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                    {t(`${key}.table.rows.${index}.feature`)}
                  </div>
                  <div className="border-l p-4 leading-6 text-muted-foreground">{t(`${key}.table.rows.${index}.supoclip`)}</div>
                  <div className="border-l p-4 leading-6 text-muted-foreground">{t(`${key}.table.rows.${index}.opusclip`)}</div>
                </div>
              ))}
            </div>

            <div className={articleBodyClassName}>
              <h2>{t(`${key}.how.heading`)}</h2>
              <ol>
                {([0, 1, 2, 3, 4] as const).map((index) => (
                  <li key={index}>{t(`${key}.how.steps.${index}`)}</li>
                ))}
              </ol>

              <h2>{t(`${key}.better.heading`)}</h2>
              <p>{t(`${key}.better.body`)}</p>

              <h2>{t(`${key}.opus.heading`)}</h2>
              <p>{t(`${key}.opus.body`)}</p>
            </div>

            <section className="my-10 rounded-lg border bg-foreground p-6 text-background md:p-8">
              <div className="flex flex-col gap-6 md:flex-row md:items-center md:justify-between">
                <div>
                  <p className="flex items-center gap-2 text-sm font-medium text-background/70">
                    <ListChecks className="h-4 w-4" />
                    {t(`${key}.cta.kicker`)}
                  </p>
                  <h2
                    className="mt-2 text-2xl font-bold tracking-tight"
                    style={{ fontFamily: "var(--font-syne), var(--font-geist-sans), system-ui" }}
                  >
                    {t(`${key}.cta.title`)}
                  </h2>
                </div>
                <div className="flex flex-wrap gap-3">
                  <Link href={SELF_HOSTING_GUIDE_HREF}>
                    <Button variant="secondary">{t(`${key}.cta.selfHost`)}</Button>
                  </Link>
                  <Link href="/sign-up">
                    <Button variant="secondary">
                      {t(`${key}.cta.openApp`)}
                      <ArrowRight className="h-4 w-4" />
                    </Button>
                  </Link>
                  <a href="https://github.com/FujiwaraChoki/supoclip" target="_blank" rel="noopener noreferrer">
                    <Button variant="outline" className="border-background/30 bg-transparent text-background hover:bg-background hover:text-foreground">
                      <Github className="h-4 w-4" />
                      GitHub
                    </Button>
                  </a>
                </div>
              </div>
            </section>

            <div className={articleBodyClassName}>
              <h2 id="faq">{t("shell.faq")}</h2>
            </div>

            <div className="mt-6 space-y-4">
              {faqs.map((faq, index) => {
                const Icon = faqIcons[index];
                return (
                  <section key={faq.question} className="rounded-lg border p-5">
                    <h3 className="flex items-center gap-2 font-semibold">
                      <Icon className="h-4 w-4 text-muted-foreground" />
                      {faq.question}
                    </h3>
                    <p className="mt-2 text-sm leading-7 text-muted-foreground">{faq.answer}</p>
                  </section>
                );
              })}
            </div>

            <Separator className="my-10" />

            <RelatedGuides href={`/blog/${post.slug}`} heading={t(`${key}.relatedHeading`)} />

            <Separator className="my-10" />

            <section className="text-sm leading-7 text-muted-foreground">
              <h2 className="text-base font-semibold text-foreground">{t(`${key}.sources.heading`)}</h2>
              <p className="mt-2">
                {t.rich(`${key}.sources.body`, {
                  plans: (chunks) => (
                    <a href="https://help.opus.pro/docs/article/plans-and-credits" className="font-medium text-foreground underline underline-offset-4">
                      {chunks}
                    </a>
                  ),
                  watermark: (chunks) => (
                    <a href="https://help.opus.pro/docs/article/watermark" className="font-medium text-foreground underline underline-offset-4">
                      {chunks}
                    </a>
                  ),
                })}
              </p>
            </section>
          </div>

          <aside className="hidden lg:block">
            <div className="sticky top-8 space-y-4">
              <div className="rounded-lg border p-5">
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                  {t(`${key}.toc.label`)}
                </p>
                <div className="mt-4 space-y-3 text-sm">
                  <a href="#quick-verdict" className="block text-muted-foreground hover:text-foreground">
                    {t(`${key}.toc.verdict`)}
                  </a>
                  <a href="#supoclip-vs-opusclip" className="block text-muted-foreground hover:text-foreground">
                    {t(`${key}.toc.comparison`)}
                  </a>
                  <a href="#faq" className="block text-muted-foreground hover:text-foreground">
                    {t(`${key}.toc.faq`)}
                  </a>
                </div>
              </div>
              <div className="rounded-lg border bg-muted/35 p-5">
                <ShieldCheck className="h-5 w-5 text-green-600" />
                <p className="mt-3 text-sm font-semibold">{t(`${key}.aside.title`)}</p>
                <p className="mt-2 text-sm leading-6 text-muted-foreground">{t(`${key}.aside.body`)}</p>
              </div>
            </div>
          </aside>
        </section>
      </article>
      <SiteFooter />
    </main>
  );
}
