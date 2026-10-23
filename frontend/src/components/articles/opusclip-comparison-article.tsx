import Link from "next/link";
import { getTranslations } from "next-intl/server";

import { ArticleShell, type ArticleFaq } from "@/components/articles/article-shell";
import type { BlogPost } from "@/lib/blog-posts";
import { getPublicBillingPlans } from "@/lib/billing-plans";
import { SELF_HOSTING_GUIDE_HREF } from "@/lib/marketing-links";
import { GITHUB_URL } from "@/lib/site";

const source = (path: string) => `${GITHUB_URL}/blob/main/${path}`;

const OPUS_SOURCES = {
  plans: "https://help.opus.pro/docs/article/plans-and-credits",
  watermark: "https://help.opus.pro/docs/article/watermark",
};

const tocIds = ["verdict", "comparison", "pricing", "choose-supoclip", "choose-opusclip", "test", "sources", "faq"] as const;

export async function OpusClipComparisonArticle({ post }: { post: BlogPost }) {
  const t = await getTranslations("blog");
  const [pro, scale] = getPublicBillingPlans();
  const slug = "supoclip-vs-opusclip";
  const link = (href: string) => function ArticleLink(chunks: React.ReactNode) {
    return <a href={href}>{chunks}</a>;
  };
  const faqs: ArticleFaq[] = [0, 1, 2, 3].map((index) => ({
    question: t(`${slug}.faqs.${index}.question`),
    answer: t(`${slug}.faqs.${index}.answer`),
  }));
  const toc: Array<[string, string]> = tocIds.map((id) => [id, t(`${slug}.toc.${id}`)]);
  const rows: Array<[string, React.ReactNode, React.ReactNode]> = [
    [
      t(`${slug}.rows.deployment.label`),
      t.rich(`${slug}.rows.deployment.supoclip`, { compose: link(source("docker-compose.yml")) }),
      t(`${slug}.rows.deployment.opusclip`),
    ],
    [
      t(`${slug}.rows.source.label`),
      t.rich(`${slug}.rows.source.supoclip`, { license: link(source("LICENSE")) }),
      t(`${slug}.notCovered`),
    ],
    [
      t(`${slug}.rows.pricing.label`),
      t(`${slug}.rows.pricing.supoclip`),
      t.rich(`${slug}.rows.pricing.opusclip`, { plans: link(OPUS_SOURCES.plans) }),
    ],
    [
      t(`${slug}.rows.watermark.label`),
      t.rich(`${slug}.rows.watermark.supoclip`, { pipeline: link(`${GITHUB_URL}/tree/main/backend/src/media`) }),
      t.rich(`${slug}.rows.watermark.opusclip`, { watermark: link(OPUS_SOURCES.watermark) }),
    ],
    [
      t(`${slug}.rows.models.label`),
      t.rich(`${slug}.rows.models.supoclip`, { config: link(source("docs/configuration.md")) }),
      t(`${slug}.notCovered`),
    ],
    [
      t(`${slug}.rows.transcription.label`),
      t(`${slug}.rows.transcription.supoclip`),
      t(`${slug}.notCovered`),
    ],
    [
      t(`${slug}.rows.automation.label`),
      t.rich(`${slug}.rows.automation.supoclip`, { mcp: link(source("mcp/README.md")) }),
      t(`${slug}.notCovered`),
    ],
  ];

  return (
    <ArticleShell post={post} breadcrumb={t(`${slug}.breadcrumb`)} toc={toc} faqs={faqs}>
      <p>{t(`${slug}.intro`)}</p>
      <p>
        {t.rich(`${slug}.introNote`, { test: (chunks) => <a href="#test">{chunks}</a> })}
      </p>

      <h2 id="verdict">{t(`${slug}.verdict.heading`)}</h2>
      <ul>
        <li>{t.rich(`${slug}.verdict.supoclip`, { strong: (chunks) => <strong>{chunks}</strong> })}</li>
        <li>{t.rich(`${slug}.verdict.opusclip`, { strong: (chunks) => <strong>{chunks}</strong> })}</li>
      </ul>

      <h2 id="comparison">{t(`${slug}.comparison.heading`)}</h2>
      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full min-w-[600px] text-left text-sm leading-6">
          <caption className="sr-only">{t(`${slug}.comparison.caption`)}</caption>
          <thead className="bg-muted">
            <tr>
              <th scope="col" className="p-3">{t(`${slug}.comparison.area`)}</th>
              <th scope="col" className="p-3">SupoClip</th>
              <th scope="col" className="p-3">OpusClip</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([area, supoclip, opusclip], index) => (
              <tr key={index} className="border-t">
                <th scope="row" className="p-3 align-top">{area}</th>
                <td className="p-3 align-top text-muted-foreground">{supoclip}</td>
                <td className="p-3 align-top text-muted-foreground">{opusclip}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-sm">
        {t.rich(`${slug}.comparison.footnote`, { site: link("https://www.opus.pro/") })}
      </p>

      <h2 id="pricing">{t(`${slug}.pricing.heading`)}</h2>
      <p>
        {t.rich(`${slug}.pricing.p1`, {
          plans: link(OPUS_SOURCES.plans),
          watermark: link(OPUS_SOURCES.watermark),
        })}
      </p>
      <p>
        {t.rich(`${slug}.pricing.p2`, {
          proName: pro.name,
          proPrice: pro.priceMonthly,
          proLimit: pro.minuteLimit / 60,
          scaleName: scale.name,
          scalePrice: scale.priceMonthly,
          scaleLimit: scale.minuteLimit / 60,
          pro: (chunks) => <strong>{chunks}</strong>,
          scale: (chunks) => <strong>{chunks}</strong>,
          guide: (chunks) => <Link href={SELF_HOSTING_GUIDE_HREF}>{chunks}</Link>,
        })}
      </p>

      <h2 id="choose-supoclip">{t(`${slug}.chooseSupoclip.heading`)}</h2>
      <ul>
        {(["li1", "li2", "li3", "li4"] as const).map((key) => (
          <li key={key}>{t.rich(`${slug}.chooseSupoclip.${key}`, { strong: (chunks) => <strong>{chunks}</strong> })}</li>
        ))}
      </ul>

      <h2 id="choose-opusclip">{t(`${slug}.chooseOpusclip.heading`)}</h2>
      <ul>
        {(["li1", "li2", "li3"] as const).map((key) => (
          <li key={key}>{t.rich(`${slug}.chooseOpusclip.${key}`, { strong: (chunks) => <strong>{chunks}</strong> })}</li>
        ))}
      </ul>

      <h2 id="test">{t(`${slug}.test.heading`)}</h2>
      <p>{t(`${slug}.test.intro`)}</p>
      <ol>
        {(["li1", "li2", "li3", "li4"] as const).map((key) => (
          <li key={key}>{t(`${slug}.test.${key}`)}</li>
        ))}
      </ol>
      <p>
        {t.rich(`${slug}.test.demo`, { demo: (chunks) => <Link href="/demo">{chunks}</Link> })}
      </p>

      <h2 id="sources">{t(`${slug}.sources.heading`)}</h2>
      <ul>
        <li>
          {t.rich(`${slug}.sources.opus`, {
            plans: link(OPUS_SOURCES.plans),
            watermark: link(OPUS_SOURCES.watermark),
            site: link("https://www.opus.pro/"),
          })}
        </li>
        <li>
          {t.rich(`${slug}.sources.supoclip`, {
            repo: link(GITHUB_URL),
            compose: link(source("docker-compose.yml")),
            config: link(source("docs/configuration.md")),
            billing: link(source("backend/src/services/billing_service.py")),
          })}
        </li>
      </ul>
    </ArticleShell>
  );
}
