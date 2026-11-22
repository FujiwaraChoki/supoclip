import Image from "next/image";
import Link from "next/link";

import { ArticleShell } from "@/components/articles/article-shell";
import { ClipAnatomy, LaunchWeekTable, ReactionLayoutFigure, RulesBoard, WorkflowDiagram } from "@/components/articles/gta6/diagrams";
import { EarningsCalculator } from "@/components/articles/gta6/earnings-calculator";
import { MomentMap } from "@/components/articles/gta6/moment-map";
import type { BlogPost } from "@/lib/blog-posts";
import { getGta6Copy, type GuideLocale } from "@/lib/localized-guides";

export const GTA6_SOURCES = {
  rockstarPolicy: "https://support.rockstargames.com/articles/7bNaeoMFTV0iUDGhStTXvz",
  gtaSix: "https://www.rockstargames.com/VI",
  whopContentRewards: "https://whop.com/blog/content-rewards/",
  youtubeMonetization: "https://support.google.com/youtube/answer/1311392",
} as const;

export function Gta6ClippingArticle({ post, locale = "en" }: { post: BlogPost; locale?: GuideLocale }) {
  const copy = getGta6Copy(locale);

  return (
    <ArticleShell
      post={post}
      breadcrumb={copy.breadcrumb}
      toc={[
        ["why-now", copy.toc.whyNow],
        ["what-it-pays", copy.toc.whatItPays],
        ["rules", copy.toc.rules],
        ["workflow", copy.toc.workflow],
        ["launch-week", copy.toc.launchWeek],
        ["mistakes", copy.toc.mistakes],
        ["faq", copy.toc.faq],
      ]}
      faqs={copy.faqs}
      cta={copy.cta}
    >
      <figure className="not-prose space-y-3">
        <Image src="/blog/how-to-clip-gta-6.webp" alt={post.image?.alt ?? ""} width={1672} height={941} priority sizes="(max-width: 768px) 100vw, 768px" className="h-auto w-full rounded-xl border" />
        <figcaption className="text-sm text-muted-foreground">{copy.heroCaption}</figcaption>
      </figure>

      <p>{copy.intro[0]}</p>
      <p>{copy.intro[1]}</p>

      <section aria-labelledby="why-now" className="space-y-5">
        <h2 id="why-now">{copy.whyNow.heading}</h2>
        <p>
          <a href={GTA6_SOURCES.gtaSix}>{copy.whyNow.p1LinkText}</a>
          {copy.whyNow.p1AfterLinkBeforeDate}
          <strong>{copy.whyNow.p1Date}</strong>
          {copy.whyNow.p1AfterDate}
        </p>
        <p>
          {copy.whyNow.p2BeforeStrong}
          <strong>{copy.whyNow.p2Strong}</strong>
          {copy.whyNow.p2AfterStrong}
        </p>
      </section>

      <section aria-labelledby="what-it-pays" className="space-y-5">
        <h2 id="what-it-pays">{copy.whatItPays.heading}</h2>
        <p>{copy.whatItPays.lead}</p>
        <ol>
          <li>
            <strong>{copy.whatItPays.li1Label}</strong>
            {copy.whatItPays.li1BeforeLink}
            <a href={GTA6_SOURCES.whopContentRewards}>{copy.whatItPays.li1LinkText}</a>
            {copy.whatItPays.li1AfterLink}
          </li>
          <li>
            <strong>{copy.whatItPays.li2Label}</strong>
            {copy.whatItPays.li2BeforeLink}
            <a href={GTA6_SOURCES.youtubeMonetization}>{copy.whatItPays.li2LinkText}</a>
            {copy.whatItPays.li2AfterLink}
          </li>
        </ol>
        <p>{copy.whatItPays.mathIntro}</p>
        <EarningsCalculator locale={locale} />
      </section>

      <section aria-labelledby="rules" className="space-y-5">
        <h2 id="rules">{copy.rules.heading}</h2>
        <p>
          {copy.rules.p1BeforeLink}
          <a href={GTA6_SOURCES.rockstarPolicy}>{copy.rules.p1LinkText}</a>
          {copy.rules.p1BetweenLinkAndEm}
          <em>{copy.rules.p1Em}</em>
          {copy.rules.p1AfterEm}
        </p>
        <RulesBoard locale={locale} />
        <p>{copy.rules.p2}</p>
      </section>

      <section aria-labelledby="workflow" className="space-y-5">
        <h2 id="workflow">{copy.workflow.heading}</h2>
        <p>{copy.workflow.lead}</p>
        <WorkflowDiagram locale={locale} />

        <h3>{copy.workflow.s1Heading}</h3>
        <p>{copy.workflow.s1Body}</p>

        <h3>{copy.workflow.s2Heading}</h3>
        <p>{copy.workflow.s2Body}</p>
        <MomentMap locale={locale} />

        <h3>{copy.workflow.s3Heading}</h3>
        <p>{copy.workflow.s3BodyBefore}</p>
        <ReactionLayoutFigure locale={locale} />
        <p>{copy.workflow.s3BodyAfter}</p>

        <h3>{copy.workflow.s4Heading}</h3>
        <p>{copy.workflow.s4Body}</p>
        <ClipAnatomy locale={locale} />

        <h3>{copy.workflow.s5Heading}</h3>
        <p>{copy.workflow.s5Body}</p>
        <figure className="not-prose space-y-3">
          <Image src="/blog/supoclip-editor.webp" alt={copy.workflow.editorAlt} width={1800} height={1109} sizes="(max-width: 768px) 100vw, 768px" className="h-auto w-full rounded-xl border" />
          <figcaption className="text-sm leading-6 text-muted-foreground">{copy.workflow.editorCaption}</figcaption>
        </figure>

        <h3>{copy.workflow.s6Heading}</h3>
        <p>{copy.workflow.s6Body}</p>
        <p><Link href="/blog/schedule-week-of-youtube-shorts">{copy.workflow.scheduleLinkLabel}</Link></p>
      </section>

      <section aria-labelledby="launch-week" className="space-y-5">
        <h2 id="launch-week">{copy.launchWeek.heading}</h2>
        <p>{copy.launchWeek.lead}</p>
        <LaunchWeekTable locale={locale} />
      </section>

      <section aria-labelledby="mistakes" className="space-y-5">
        <h2 id="mistakes">{copy.mistakes.heading}</h2>
        <ul>
          {copy.mistakes.items.map((item) => (
            <li key={item.label}>
              <strong>{item.label}</strong> {item.body}
            </li>
          ))}
        </ul>
        <p>
          {copy.mistakes.moreBeforeFirst}
          <Link href="/blog/find-best-moments-in-video">{copy.mistakes.moreFirstLink}</Link>
          {copy.mistakes.moreBetween}
          <Link href="/blog/twitch-vod-to-shorts">{copy.mistakes.moreSecondLink}</Link>
          {copy.mistakes.moreAfter}
        </p>
      </section>

      <section aria-labelledby="sources" className="space-y-3 text-sm">
        <h2 id="sources">{copy.sources.heading}</h2>
        <ul>
          <li><a href={GTA6_SOURCES.gtaSix}>{copy.sources.gtaSix}</a></li>
          <li><a href={GTA6_SOURCES.rockstarPolicy}>{copy.sources.rockstarPolicy}</a></li>
          <li><a href={GTA6_SOURCES.whopContentRewards}>{copy.sources.whopContentRewards}</a></li>
          <li><a href={GTA6_SOURCES.youtubeMonetization}>{copy.sources.youtubeMonetization}</a></li>
        </ul>
        <p>{copy.sources.checkedNote}</p>
      </section>
    </ArticleShell>
  );
}
