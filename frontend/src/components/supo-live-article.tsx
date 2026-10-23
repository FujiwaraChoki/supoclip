import Link from "next/link";
import Image from "next/image";
import { getLocale, getTranslations } from "next-intl/server";
import { ArrowRight, Captions, Check, Code2, Film, Github, ScanFace, Server, Sparkles, Upload } from "lucide-react";
import { formatArticleDate } from "@/components/articles/article-shell";
import { RelatedGuides } from "@/components/marketing/related-guides";
import { SiteFooter } from "@/components/marketing/site-footer";
import { intlLocale } from "@/i18n/config";
import { type BlogPost, getSiteUrl, HOSTED_APP_URL } from "@/lib/blog-posts";
import { OPUSCLIP_COMPARISON_HREF, SELF_HOSTING_GUIDE_HREF } from "@/lib/marketing-links";

const repository = "https://github.com/FujiwaraChoki/supoclip";
const workflowIcons = [Upload, Sparkles, ScanFace, Film];

export async function SupoLiveArticle({ post }: { post: BlogPost }) {
  const t = await getTranslations("blog");
  const locale = await getLocale();
  const slug = post.slug;
  const siteUrl = getSiteUrl();
  const updatedLabel = formatArticleDate(post.updatedAt, locale);
  const faqs = [0, 1, 2].map((index) => ({
    question: t(`${slug}.faqs.${index}.question`),
    answer: t(`${slug}.faqs.${index}.answer`),
  }));
  const structuredData = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "BreadcrumbList",
        itemListElement: [
          { "@type": "ListItem", position: 1, name: t("shell.home"), item: siteUrl },
          { "@type": "ListItem", position: 2, name: t("shell.blog"), item: `${siteUrl}/blog` },
          { "@type": "ListItem", position: 3, name: t(`${slug}.breadcrumb`), item: `${siteUrl}/blog/${post.slug}` },
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
        image: post.image ? [`${siteUrl}${post.image.src}`, `${siteUrl}/blog/supoclip-editor.webp`] : undefined,
        articleSection: post.category,
        inLanguage: intlLocale(locale),
        mainEntityOfPage: `${siteUrl}/blog/${post.slug}`,
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
        <nav aria-label={t("shell.blogNav")} className="mx-auto flex max-w-4xl items-center justify-between gap-4 px-6 py-5">
          <Link href="/" className="flex items-center gap-2.5 text-xl font-bold"><Image src="/logo.png" alt="" width={30} height={30} className="rounded-lg" />SupoClip</Link>
          <Link href="/blog" className="text-sm underline underline-offset-4">{t("shell.allArticles")}</Link>
        </nav>
      </header>
      <article className="mx-auto max-w-5xl px-5 py-10 sm:px-8 sm:py-16">
        <nav aria-label={t("shell.breadcrumbNav")} className="mb-8 flex flex-wrap gap-2 text-sm text-muted-foreground">
          <Link href="/">{t("shell.home")}</Link><span aria-hidden="true">/</span><Link href="/blog">{t("shell.blog")}</Link><span aria-hidden="true">/</span><span>{t(`${slug}.breadcrumb`)}</span>
        </nav>
        <header className="mb-10 max-w-3xl space-y-5">
          <p className="inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-semibold uppercase tracking-widest"><Code2 className="h-4 w-4" />{post.eyebrow}</p>
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
        {post.image && <figure className="mb-10 overflow-hidden rounded-2xl border bg-muted/30">
          <Image src={post.image.src} alt={post.image.alt} width={post.image.width} height={post.image.height} priority sizes="(max-width: 1024px) 100vw, 960px" className="h-auto w-full" />
          <figcaption className="px-5 py-3 text-xs leading-5 text-muted-foreground">{t(`${slug}.heroCaption`)}</figcaption>
        </figure>}
        <section aria-labelledby="verdict" className="mb-10 rounded-2xl border border-emerald-500/25 bg-emerald-500/5 p-6 sm:p-8">
          <p className="mb-3 text-xs font-semibold uppercase tracking-widest text-emerald-700 dark:text-emerald-400">{t(`${slug}.verdict.kicker`)}</p>
          <h2 id="verdict" className="text-2xl font-bold tracking-tight">{t(`${slug}.verdict.heading`)}</h2>
          <p className="mt-3 max-w-2xl leading-7 text-muted-foreground">{t(`${slug}.verdict.body`)}</p>
          <div className="mt-6 grid gap-3 sm:grid-cols-3">
            {[Github, Server, Captions].map((Icon, index) => (
              <div key={index} className="flex items-center gap-3 rounded-xl border bg-background p-4 text-sm font-medium">
                <Icon className="h-5 w-5 shrink-0 text-emerald-600" />
                {t(`${slug}.verdict.points.${index}`)}
              </div>
            ))}
          </div>
        </section>
        <nav aria-label={t("shell.inThisArticle")} className="mb-10 flex flex-wrap gap-x-5 gap-y-2 border-y py-4 text-sm font-medium">
          {(["comparison", "workflow", "control", "cost", "faq"] as const).map((id) => (
            <a key={id} href={`#${id}`} className="underline-offset-4 hover:underline">{t(`${slug}.toc.${id}`)}</a>
          ))}
        </nav>
        <div className="mx-auto max-w-3xl space-y-6 leading-8 [&_h2]:pt-6 [&_h2]:text-2xl [&_h2]:font-bold [&_h3]:text-lg [&_h3]:font-semibold [&_a]:underline [&_a]:underline-offset-4 [&_p]:text-muted-foreground">
          <p>{t(`${slug}.body.p1`)}</p>
          <p>{t(`${slug}.body.p2`)}</p>
          <h2>{t(`${slug}.body.whatSupoclip`)}</h2>
          <p>
            {t.rich(`${slug}.body.whatSupoclipBody`, { repo: (chunks) => <a href={repository}>{chunks}</a> })}
          </p>
          <h2>{t(`${slug}.body.whatSupo`)}</h2>
          <p>
            {t.rich(`${slug}.body.whatSupoBody`, { site: (chunks) => <a href="https://supo.live/">{chunks}</a> })}
          </p>
          <h2 id="comparison" className="scroll-mt-8">{t(`${slug}.comparison.heading`)}</h2>
          <div className="overflow-x-auto rounded-lg border">
            <table className="w-full min-w-[540px] text-left text-sm leading-6">
              <caption className="sr-only">{t(`${slug}.comparison.caption`)}</caption>
              <thead className="bg-muted">
                <tr>
                  <th scope="col" className="p-4">{t(`${slug}.comparison.priority`)}</th>
                  <th scope="col" className="p-4">SupoClip</th>
                  <th scope="col" className="p-4">supo.live</th>
                </tr>
              </thead>
              <tbody>
                {(["deployment", "source", "usecase", "customization", "usage"] as const).map((row) => (
                  <tr key={row} className="border-t">
                    <th scope="row" className="p-4 align-top">{t(`${slug}.comparison.rows.${row}.label`)}</th>
                    <td className="p-4 align-top">{t(`${slug}.comparison.rows.${row}.supoclip`)}</td>
                    <td className="p-4 align-top">{t(`${slug}.comparison.rows.${row}.supolive`)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-sm">
            {t.rich(`${slug}.comparison.note`, { site: (chunks) => <a href="https://supo.live/">{chunks}</a> })}
          </p>
          <h2 id="workflow" className="scroll-mt-8">{t(`${slug}.workflow.heading`)}</h2>
          <p>{t(`${slug}.workflow.intro`)}</p>
          <figure className="rounded-2xl border bg-muted/25 p-5 sm:p-7">
            <ol className="grid gap-4 sm:grid-cols-2">
              {workflowIcons.map((Icon, index) => (
                <li key={index} className="rounded-xl border bg-background p-4">
                  <div className="flex items-center justify-between"><Icon className="h-5 w-5 text-emerald-600" /><span className="font-mono text-xs text-muted-foreground">0{index + 1}</span></div>
                  <h3 className="mt-4">{t(`${slug}.workflow.steps.${index}.title`)}</h3>
                  <p className="mt-1 text-sm leading-6">{t(`${slug}.workflow.steps.${index}.detail`)}</p>
                </li>
              ))}
            </ol>
            <figcaption className="mt-4 text-xs leading-5 text-muted-foreground">{t(`${slug}.workflow.caption`)}</figcaption>
          </figure>
          <figure className="overflow-hidden rounded-2xl border">
            <a href="/blog/supoclip-editor.webp" aria-label={t(`${slug}.editor.aria`)}>
              <Image src="/blog/supoclip-editor.webp" alt={t(`${slug}.editor.alt`)} width={1800} height={1109} sizes="(max-width: 768px) 100vw, 768px" className="h-auto w-full" />
            </a>
            <figcaption className="bg-muted/25 px-5 py-4 text-sm leading-6 text-muted-foreground">{t(`${slug}.editor.caption`)}</figcaption>
          </figure>
          <h2 id="control" className="scroll-mt-8">{t(`${slug}.control.heading`)}</h2>
          <h3>{t(`${slug}.control.h1`)}</h3>
          <p>{t(`${slug}.control.p1`)}</p>
          <h3>{t(`${slug}.control.h2`)}</h3>
          <p>{t(`${slug}.control.p2`)}</p>
          <h3>{t(`${slug}.control.h3`)}</h3>
          <p>
            {t.rich(`${slug}.control.p3`, { guide: (chunks) => <Link href={SELF_HOSTING_GUIDE_HREF}>{chunks}</Link> })}
          </p>
          <h3>{t(`${slug}.control.h4`)}</h3>
          <p>
            {t.rich(`${slug}.control.p4`, { api: (chunks) => <a href={`${repository}/blob/main/docs/api-reference.md`}>{chunks}</a> })}
          </p>
          <figure className="rounded-2xl bg-zinc-950 p-6 text-zinc-50 sm:p-8">
            <figcaption className="mb-6 font-semibold">{t(`${slug}.control.figureCaption`)}</figcaption>
            <div className="grid gap-4 sm:grid-cols-[1fr_auto_1fr] sm:items-center">
              <div className="rounded-xl border border-emerald-400/40 bg-emerald-400/10 p-5">
                <Server className="mb-3 h-6 w-6 text-emerald-300" />
                <h3>{t(`${slug}.control.yours.heading`)}</h3>
                <ul className="mt-3 space-y-2 text-sm text-zinc-300">
                  <li>{t(`${slug}.control.yours.li1`)}</li>
                  <li>{t(`${slug}.control.yours.li2`)}</li>
                  <li>{t(`${slug}.control.yours.li3`)}</li>
                </ul>
              </div>
              <ArrowRight className="mx-auto h-5 w-5 rotate-90 text-zinc-400 sm:rotate-0" aria-hidden="true" />
              <div className="rounded-xl border border-zinc-700 p-5">
                <Sparkles className="mb-3 h-6 w-6 text-zinc-300" />
                <h3>{t(`${slug}.control.connected.heading`)}</h3>
                <ul className="mt-3 space-y-2 text-sm text-zinc-300">
                  <li>{t(`${slug}.control.connected.li1`)}</li>
                  <li>{t(`${slug}.control.connected.li2`)}</li>
                  <li>{t(`${slug}.control.connected.li3`)}</li>
                </ul>
              </div>
            </div>
            <div className="mt-5 text-xs leading-6 text-zinc-400">{t(`${slug}.control.figureNote`)}</div>
          </figure>
          <h2 id="cost" className="scroll-mt-8">{t(`${slug}.cost.heading`)}</h2>
          <p>{t(`${slug}.cost.p1`)}</p>
          <p>{t(`${slug}.cost.p2`)}</p>
          <h2>{t(`${slug}.choose.heading`)}</h2>
          <p>{t(`${slug}.choose.p1`)}</p>
          <p>{t(`${slug}.choose.p2`)}</p>
          <h2>{t(`${slug}.start.heading`)}</h2>
          <p>{t(`${slug}.start.p1`)}</p>
          <section className="rounded-2xl border bg-muted/30 p-6 sm:p-8" aria-label={t("shell.startAria")}>
            <Image src="/logo.png" alt={t(`${slug}.start.logoAlt`)} width={40} height={40} className="mb-4 rounded-lg" />
            <h3>{t(`${slug}.start.cardTitle`)}</h3>
            <ul className="my-5 space-y-2 text-sm">
              {([0, 1, 2] as const).map((index) => (
                <li key={index} className="flex items-center gap-2"><Check className="h-4 w-4 text-emerald-600" />{t(`${slug}.start.points.${index}`)}</li>
              ))}
            </ul>
            <div className="flex flex-wrap gap-3">
              <a href={HOSTED_APP_URL} className="inline-flex items-center gap-2 rounded-lg bg-foreground px-5 py-3 text-sm font-semibold !text-background !no-underline">{t(`${slug}.start.try`)}<ArrowRight className="h-4 w-4" /></a>
              <a href={repository} className="inline-flex items-center gap-2 rounded-lg border px-5 py-3 text-sm font-semibold !no-underline"><Github className="h-4 w-4" />{t("shell.getSource")}</a>
            </div>
          </section>
          <h2 id="faq" className="scroll-mt-8">{t("shell.faqHeading")}</h2>
          {faqs.map(({ question, answer }) => <section key={question} className="space-y-2 rounded-lg border p-5"><h3>{question}</h3><p>{answer}</p></section>)}
          <p>
            {t.rich(`${slug}.closing`, {
              openSource: (chunks) => <Link href="/open-source-video-clipper">{chunks}</Link>,
              comparison: (chunks) => <Link href={OPUSCLIP_COMPARISON_HREF}>{chunks}</Link>,
            })}
          </p>
        </div>
        <div className="mx-auto mt-16 max-w-5xl border-t pt-12">
          <RelatedGuides href={`/blog/${post.slug}`} heading={t("shell.keepReading")} />
        </div>
      </article>
      <SiteFooter />
    </main>
  );
}
