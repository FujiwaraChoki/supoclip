"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import Image from "next/image";
import {
  ArrowRight,
  ArrowUpRight,
  Check,
  Clock,
  ExternalLink,
  Github,
  Info,
  Menu,
  Upload,
  X,
} from "lucide-react";

import { Announcements } from "@/components/announcements";
import { BillingIntervalToggle, ComingSoonTag, useBillingPlanText } from "@/components/billing/upgrade-prompt";
import { FaqSection } from "@/components/landing/faq-section";
import { EditorSection } from "@/components/landing/editor-section";
import { FeatureBento } from "@/components/landing/feature-bento";
import { HeroStage, SourceStickers } from "@/components/landing/hero-stage";
import { ProductFilm } from "@/components/landing/product-film";
import { PublishSection } from "@/components/landing/publish-section";
import { Reveal, SectionHeading } from "@/components/landing/reveal";
import { CreatorOfferBanner } from "@/components/marketing/creator-offer-banner";
import { SiteFooter } from "@/components/marketing/site-footer";
import { SocialProviderIcon } from "@/components/social-provider-icon";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { VIDEO_SOURCE_LABELS, VideoSourceIcon } from "@/components/video-source-icon";
import { catalogKey } from "@/i18n/config";
import { isLandingOnlyModeEnabled } from "@/lib/app-flags";
import {
  annualFullPrice,
  annualMonthlyEquivalent,
  annualMonthsFree,
  billingPlanMessageKey,
  getPublicBillingPlans,
  planPrice,
  socialPublishingStage,
  type BillingInterval,
} from "@/lib/billing-plans";
import {
  SELF_HOSTING_GUIDE_HREF,
  getMarketingLink,
  type MarketingLink,
} from "@/lib/marketing-links";
import { APP_STORE_URL, GITHUB_URL, HOSTED_APP_URL, PRODUCT_HUNT_BADGE_URL, PRODUCT_HUNT_URL } from "@/lib/site";
import { cn } from "@/lib/utils";

type Translate = (key: string, values?: Record<string, string | number>) => string;

const PRICING_EXAMPLE_VIDEO_MINUTES = 30;

const NAV_LINKS = [
  { href: "#features", key: "features" },
  { href: "#editor", key: "editor" },
  { href: "#publish", key: "publishing" },
  { href: "#pricing", key: "pricing" },
  { href: "#open-source", key: "openSource" },
  { href: "/blog", key: "blog" },
  { href: "/affiliate", key: "affiliate" },
] as const;

// Featured on the landing page; the footer links every marketing page.
const SEO_RESOURCES = [
  "/blog/podcast-to-shorts",
  "/blog/find-best-moments-in-video",
  "/ai-video-clipper",
  "/youtube-shorts-clipper",
  "/open-source-video-clipper",
  "/blog/twitch-vod-to-shorts",
].map((href) => getMarketingLink(href)).filter((link): link is MarketingLink => Boolean(link));

function getPlans(
  t: Translate,
  tCommon: Translate,
  interval: BillingInterval,
  planText: ReturnType<typeof useBillingPlanText>,
) {
  // The landing page states which platforms scheduling reaches, so it swaps the
  // generic billing line for its own YouTube (live) / TikTok + Instagram (soon) pair.
  const showScheduling = socialPublishingStage() !== "off";
  const genericScheduling = planText.t("highlights.scheduling");
  return [
    {
      id: "selfHost",
      name: tCommon("plans.selfHost"),
      price: "$0",
      originalPrice: null as string | null,
      period: t("pricing.period.forever"),
      note: null as string | null,
      soon: null as string | null,
      usageExample: null as string | null,
      description: t("pricing.selfHost.description"),
      features: [
        t("pricing.selfHost.features.crop"),
        t("pricing.selfHost.features.subtitles"),
        t("pricing.selfHost.features.virality"),
        t("pricing.selfHost.features.exports"),
        t("pricing.selfHost.features.source"),
      ],
      cta: t("pricing.selfHost.cta"),
      ctaHref: GITHUB_URL,
      highlighted: false,
    },
    ...getPublicBillingPlans().map((plan) => {
      const exampleVideos = Math.floor(plan.minuteLimit / PRICING_EXAMPLE_VIDEO_MINUTES);
      const scheduling = plan.scheduling && showScheduling;
      return {
        id: plan.id,
        name: tCommon(`plans.${billingPlanMessageKey(plan.id)}`),
        price: `$${planPrice(plan, interval)}`,
        originalPrice: interval === "year" && annualMonthsFree(plan) > 0 ? `$${annualFullPrice(plan)}` : null,
        period: interval === "year" ? t("pricing.period.year") : t("pricing.period.month"),
        note: interval === "year" ? t("pricing.billedYearly", { price: annualMonthlyEquivalent(plan) }) : null,
        soon: scheduling ? t("pricing.schedulingSoon") : null,
        usageExample:
          exampleVideos > 0
            ? t("pricing.usageExample.videos", { count: exampleVideos })
            : null,
        description: t(`pricing.${plan.id}.description`),
        features: [
          ...planText.highlights(plan).filter((line) => line !== genericScheduling),
          // Creator schedules; plans without scheduling can still post right away.
          ...(scheduling ? [t("pricing.schedulingYoutube")] : showScheduling ? [t("pricing.publishYoutube")] : []),
          t("pricing.hosted.tracking"),
          t("pricing.hosted.exports"),
        ],
        cta: t(`pricing.${plan.id}.cta`),
        ctaHref: "",
        highlighted: plan.highlighted,
      };
    }),
  ];
}

function PrimaryCta({ authEnabled, label, hostedLabel, className }: {
  authEnabled: boolean;
  label: string;
  hostedLabel: string;
  className?: string;
}) {
  return authEnabled ? (
    <Button asChild size="lg" className={cn("h-12 rounded-full px-7 text-sm", className)}>
      <Link href="/sign-up">
        {label}
        <ArrowRight className="size-4" />
      </Link>
    </Button>
  ) : (
    <Button asChild size="lg" className={cn("h-12 rounded-full px-7 text-sm", className)}>
      <a href={HOSTED_APP_URL} target="_blank" rel="noopener noreferrer">
        {hostedLabel}
        <ExternalLink className="size-4" />
      </a>
    </Button>
  );
}

export default function LandingPage() {
  const t = useTranslations("marketing");
  const tCommon = useTranslations("common");
  const planText = useBillingPlanText();
  const [billingInterval, setBillingInterval] = useState<BillingInterval>("month");
  const plans = getPlans(t, tCommon, billingInterval, planText);
  const [scrolled, setScrolled] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const authEnabled = !isLandingOnlyModeEnabled;

  useEffect(() => {
    const handleScroll = () => setScrolled(window.scrollY > 24);
    handleScroll();
    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  return (
    <div className="min-h-screen bg-background text-foreground">
      {authEnabled && <CreatorOfferBanner />}

      {/* ─── NAV ─── */}
      <header className="fixed inset-x-0 top-0 z-50 px-3 pt-3">
        <nav
          className={cn(
            "mx-auto flex h-14 max-w-6xl items-center justify-between rounded-full border pl-4 pr-2 transition-all duration-300",
            scrolled || mobileNavOpen
              ? "border-border bg-background/80 shadow-[0_8px_30px_-12px_oklch(0.2_0.01_50/0.25)] backdrop-blur-xl"
              : "border-transparent bg-transparent",
          )}
        >
          <Link href="/" className="flex items-center gap-2">
            <Image src="/logo.png" alt="SupoClip" width={24} height={24} className="rounded-md" />
            <span className="font-display text-[17px] font-bold tracking-tight">SupoClip</span>
          </Link>

          <div className="hidden items-center gap-1 md:flex">
            {NAV_LINKS.map(({ href, key }) => {
              const className =
                "whitespace-nowrap rounded-full px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground";
              return href.startsWith("#") ? (
                <a key={key} href={href} className={className}>{t(`nav.${key}`)}</a>
              ) : (
                <Link key={key} href={href} className={className}>{t(`nav.${key}`)}</Link>
              );
            })}
          </div>

          <div className="hidden items-center gap-1 md:flex">
            {authEnabled ? (
              <>
                <Button asChild variant="ghost" size="sm" className="rounded-full">
                  <Link href="/sign-in">{t("nav.signIn")}</Link>
                </Button>
                <Button asChild size="sm" className="rounded-full px-4">
                  <Link href="/sign-up">{t("nav.getStarted")}</Link>
                </Button>
              </>
            ) : (
              <Button asChild size="sm" className="rounded-full px-4">
                <a href={HOSTED_APP_URL} target="_blank" rel="noopener noreferrer">
                  {t("nav.openHosted")}
                  <ExternalLink className="size-3.5" />
                </a>
              </Button>
            )}
          </div>

          <Button
            variant="ghost"
            size="icon"
            onClick={() => setMobileNavOpen((open) => !open)}
            className="rounded-full md:hidden"
            aria-label={t("nav.toggleMenu")}
            aria-expanded={mobileNavOpen}
          >
            {mobileNavOpen ? <X className="size-5" /> : <Menu className="size-5" />}
          </Button>
        </nav>

        {mobileNavOpen && (
          <div className="animate-rise mx-auto mt-2 max-w-6xl rounded-3xl border bg-background/95 p-3 shadow-lg backdrop-blur-xl md:hidden">
            {NAV_LINKS.map(({ href, key }) => {
              const className =
                "block rounded-2xl px-4 py-3 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground";
              return href.startsWith("#") ? (
                <a key={key} href={href} onClick={() => setMobileNavOpen(false)} className={className}>{t(`nav.${key}`)}</a>
              ) : (
                <Link key={key} href={href} onClick={() => setMobileNavOpen(false)} className={className}>{t(`nav.${key}`)}</Link>
              );
            })}
            <div className="mt-2 grid grid-cols-2 gap-2 border-t pt-3">
              {authEnabled ? (
                <>
                  <Button asChild variant="outline" className="rounded-full">
                    <Link href="/sign-in" onClick={() => setMobileNavOpen(false)}>{t("nav.signIn")}</Link>
                  </Button>
                  <Button asChild className="rounded-full">
                    <Link href="/sign-up" onClick={() => setMobileNavOpen(false)}>{t("nav.getStarted")}</Link>
                  </Button>
                </>
              ) : (
                <Button asChild className="col-span-2 rounded-full">
                  <a href={HOSTED_APP_URL} target="_blank" rel="noopener noreferrer">
                    {t("nav.openHosted")}
                    <ExternalLink className="size-3.5" />
                  </a>
                </Button>
              )}
            </div>
          </div>
        )}
      </header>

      {/* ─── HERO ─── */}
      <section className="relative overflow-hidden pb-20 pt-32 md:pb-28 md:pt-40">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 opacity-[0.035] [background-image:radial-gradient(circle_at_1px_1px,currentColor_1px,transparent_0)] [background-size:28px_28px] [mask-image:linear-gradient(to_bottom,black,transparent_70%)]"
        />

        <div className="relative mx-auto max-w-6xl px-6">
          <div className="mx-auto max-w-4xl text-center">
            <div
              className="mb-8 flex flex-wrap items-center justify-center gap-2"
              style={{ animation: "landing-fade-in-up 0.7s ease-out both" }}
            >
              <a
                href="#publish"
                className="group inline-flex items-center gap-2 rounded-full border bg-background py-1 pl-1 pr-3 text-xs font-medium shadow-sm transition-colors hover:border-foreground/20"
              >
                <span className="flex items-center gap-1 rounded-full bg-foreground px-2 py-0.5 text-background">
                  <SocialProviderIcon provider="youtube" className="size-3" />
                  {t("hero.newLabel")}
                </span>
                {t("hero.announcement")}
                <ArrowRight className="size-3 transition-transform group-hover:translate-x-0.5" />
              </a>
              <Announcements landing />
            </div>

            <div style={{ animation: "landing-fade-in-up 0.7s ease-out 0.04s both" }}>
              <SourceStickers className="mb-8" />
            </div>

            <h1
              className="font-display text-[2.75rem] font-bold leading-[0.98] tracking-[-0.035em] text-balance sm:text-6xl lg:text-[5.5rem]"
              style={{ animation: "landing-fade-in-up 0.7s ease-out 0.08s both" }}
            >
              {t("hero.titleLine1")}
              <br />
              <span className="text-muted-foreground/70">{t("hero.titleLine2")}</span>
            </h1>

            <p
              className="mx-auto mt-7 max-w-2xl text-base leading-relaxed text-muted-foreground text-pretty sm:text-lg"
              style={{ animation: "landing-fade-in-up 0.7s ease-out 0.16s both" }}
            >
              {t("hero.body")}
            </p>

            <div
              className="mt-9 flex flex-wrap items-center justify-center gap-3"
              style={{ animation: "landing-fade-in-up 0.7s ease-out 0.24s both" }}
            >
              <PrimaryCta authEnabled={authEnabled} label={t("hero.startClipping")} hostedLabel={t("hero.useHosted")} />
              <Button asChild variant="outline" size="lg" className="h-12 rounded-full bg-background px-7 text-sm">
                <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer">
                  <Github className="size-4" />
                  {t("hero.viewSource")}
                </a>
              </Button>
            </div>
            <p
              className="mt-4 text-xs text-muted-foreground"
              style={{ animation: "landing-fade-in-up 0.7s ease-out 0.3s both" }}
            >
              {t("hero.badge")}
            </p>
          </div>

          <div className="mt-16 md:mt-20" style={{ animation: "landing-fade-in-up 0.9s ease-out 0.35s both" }}>
            <HeroStage />
          </div>

          {/* Where clips come from and where they go */}
          <Reveal className="mx-auto mt-12 flex max-w-5xl flex-col items-center justify-center gap-x-8 gap-y-4 text-sm text-muted-foreground lg:flex-row">
            <div className="flex flex-wrap items-center justify-center gap-x-5 gap-y-2">
              <span>{t("hero.sources.label")}</span>
              {(["youtube", "twitch", "kick"] as const).map((source) => (
                <span key={source} className="flex items-center gap-1.5 text-foreground/80">
                  <VideoSourceIcon source={source} className="size-4" aria-hidden />
                  {VIDEO_SOURCE_LABELS[source]}
                </span>
              ))}
              <span className="flex items-center gap-1.5 text-foreground/80">
                <Upload className="size-4" />
                {t("hero.sources.upload")}
              </span>
            </div>
            <span className="hidden h-5 w-px bg-border lg:block" />
            <div className="flex flex-wrap items-center justify-center gap-x-5 gap-y-2">
              <span>{t("hero.publishTo")}</span>
              <span className="flex items-center gap-1.5 text-foreground/80">
                <SocialProviderIcon provider="youtube" className="size-4" />
                YouTube
              </span>
              {(["tiktok", "instagram"] as const).map((provider) => (
                <span key={provider} className="flex items-center gap-1.5 opacity-60">
                  <SocialProviderIcon provider={provider} className="size-4" />
                  {t(`publish.platforms.${provider}Short`)}
                </span>
              ))}
              <ComingSoonTag />
            </div>
          </Reveal>
        </div>
      </section>

      {/* ─── HOW IT WORKS ─── */}
      <section id="how-it-works" className="scroll-mt-24 border-y bg-canvas py-24 md:py-32">
        <div className="mx-auto max-w-6xl px-6">
          <SectionHeading eyebrow={t("steps.eyebrow")} title={t("steps.title")} align="left" />
          <Reveal className="mt-14">
            <ProductFilm />
          </Reveal>
        </div>
      </section>

      <FeatureBento />

      <EditorSection />

      <PublishSection />

      {/* ─── PRICING ─── */}
      <section id="pricing" className="scroll-mt-24 py-24 md:py-32">
        <div className="mx-auto max-w-6xl px-6">
          <SectionHeading
            eyebrow={t("nav.pricing")}
            title={t("pricing.title")}
            body={t("pricing.body", { price: getPublicBillingPlans()[0]?.priceMonthly ?? "9.99" })}
          />
          <Reveal className="mt-8 flex justify-center">
            <BillingIntervalToggle
              className="rounded-full bg-background [&_button]:rounded-full"
              value={billingInterval}
              onChange={setBillingInterval}
              plan={getPublicBillingPlans()[0]}
            />
          </Reveal>

          <div className="mx-auto mt-14 grid max-w-5xl items-stretch gap-4 md:grid-cols-3">
            {plans.map((plan, i) => (
              <Reveal key={plan.id} delay={i * 0.08} className="h-full">
                <div
                  className={cn(
                    "relative flex h-full flex-col rounded-[26px] border p-7",
                    plan.highlighted
                      ? "dark bg-background text-foreground shadow-2xl"
                      : "bg-card",
                  )}
                >
                  <div className="flex items-center justify-between gap-3">
                    <h3 className="font-display text-xl font-bold tracking-tight">{plan.name}</h3>
                    {plan.highlighted && (
                      <span className="rounded-full border px-2.5 py-0.5 text-[11px] font-medium text-muted-foreground">
                        {t("pricing.popular")}
                      </span>
                    )}
                  </div>
                  <p className="mt-1.5 min-h-10 text-sm text-muted-foreground">{plan.description}</p>

                  <div className="mt-7 @container">
                    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                      {plan.originalPrice && (
                        <span className="text-lg text-muted-foreground line-through">{plan.originalPrice}</span>
                      )}
                      <span className="font-display max-w-full whitespace-nowrap text-[clamp(1.75rem,16cqi,3rem)] font-bold tracking-tight">
                        {plan.price}
                      </span>
                      <span className="shrink-0 whitespace-nowrap text-sm text-muted-foreground">{plan.period}</span>
                    </div>
                    {plan.note && <p className="mt-1 text-xs text-muted-foreground">{plan.note}</p>}
                  </div>

                  <ul className="mt-7 flex-1 space-y-3 border-t pt-7">
                    {plan.features.map((feature, index) => (
                      <li key={feature} className="flex items-start gap-3 text-sm">
                        <Check className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                        {/* The first line is the minute allowance; translate it into videos right there. */}
                        {index === 0 && plan.usageExample ? (
                          <span>
                            {feature}
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <button
                                  type="button"
                                  aria-label={plan.usageExample}
                                  className="ml-1 inline-flex translate-y-[2px] rounded-full p-0.5 text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                                >
                                  <Info className="size-3.5" />
                                </button>
                              </TooltipTrigger>
                              <TooltipContent
                                side="top"
                                sideOffset={6}
                                // Light so it stands out on the dark Pro card too.
                                className="max-w-60 bg-background text-foreground shadow-lg ring-1 ring-border [&_svg]:bg-background [&_svg]:fill-background"
                              >
                                {plan.usageExample}
                              </TooltipContent>
                            </Tooltip>
                          </span>
                        ) : (
                          feature
                        )}
                      </li>
                    ))}
                    {plan.soon && (
                      <li className="flex items-start gap-3 text-sm text-muted-foreground">
                        <Clock className="mt-0.5 size-4 shrink-0" />
                        <span>
                          {plan.soon} <ComingSoonTag />
                        </span>
                      </li>
                    )}
                  </ul>

                  <div className="mt-6">
                    {plan.ctaHref ? (
                      <div className="flex flex-col gap-3">
                        <p className="text-xs leading-relaxed text-muted-foreground">{t("pricing.selfHost.setup")}</p>
                        <Button asChild variant="outline" size="lg" className="h-11 w-full rounded-full text-sm">
                          <Link href={SELF_HOSTING_GUIDE_HREF}>
                            {t(`links.${catalogKey(SELF_HOSTING_GUIDE_HREF)}.label`)}
                            <ArrowRight className="size-4" />
                          </Link>
                        </Button>
                        <a
                          href={plan.ctaHref}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex items-center justify-center gap-2 text-xs text-muted-foreground underline underline-offset-4 hover:text-foreground"
                        >
                          <Github className="size-3.5" />
                          {plan.cta}
                        </a>
                      </div>
                    ) : authEnabled ? (
                      <Button asChild size="lg" variant={plan.highlighted ? "default" : "outline"} className="h-11 w-full rounded-full text-sm">
                        <Link href="/sign-up">
                          {plan.cta}
                          <ArrowRight className="size-4" />
                        </Link>
                      </Button>
                    ) : (
                      <Button asChild size="lg" variant={plan.highlighted ? "default" : "outline"} className="h-11 w-full rounded-full text-sm">
                        <a href={HOSTED_APP_URL} target="_blank" rel="noopener noreferrer">
                          {t("hero.useHosted")}
                          <ExternalLink className="size-4" />
                        </a>
                      </Button>
                    )}
                  </div>
                </div>
              </Reveal>
            ))}
          </div>

          <Reveal>
            <p className="mx-auto mt-10 max-w-md text-center text-sm text-muted-foreground">{t("pricing.clipsNote")}</p>
            <p className="mx-auto mt-2 max-w-md text-center text-xs text-muted-foreground">
              {t("pricing.selfHostNote")}{" "}
              <Link href={SELF_HOSTING_GUIDE_HREF} className="underline underline-offset-2 transition-colors hover:text-foreground">
                {t("pricing.selfHostGuide")}
              </Link>
              .
            </p>
          </Reveal>
        </div>
      </section>

      <FaqSection />

      {/* ─── OPEN SOURCE ─── */}
      <section id="open-source" className="scroll-mt-24 border-y bg-canvas py-24 md:py-32">
        <div className="mx-auto grid max-w-6xl items-center gap-12 px-6 lg:grid-cols-2 lg:gap-16">
          <div>
            <SectionHeading eyebrow={t("openSource.badge")} title={t("openSource.title")} body={t("openSource.body")} align="left" />
            <Reveal delay={0.05}>
              <ul className="mt-8 space-y-3 text-sm">
                {(["license", "docker", "models"] as const).map((fact) => (
                  <li key={fact} className="flex items-start gap-3">
                    <Check className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                    {t(`openSource.facts.${fact}`)}
                  </li>
                ))}
              </ul>
              <div className="mt-9 flex flex-wrap gap-3">
                <Button asChild className="rounded-full">
                  <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer">
                    <Github className="size-4" />
                    {t("pricing.selfHost.cta")}
                  </a>
                </Button>
                <Button asChild variant="outline" className="rounded-full bg-background">
                  <Link href={SELF_HOSTING_GUIDE_HREF}>{t(`links.${catalogKey(SELF_HOSTING_GUIDE_HREF)}.label`)}</Link>
                </Button>
              </div>
            </Reveal>
          </div>

          <Reveal delay={0.1} className="min-w-0">
            <div className="dark overflow-hidden rounded-[22px] border bg-background text-foreground shadow-2xl">
              <div className="flex items-center gap-1.5 border-b px-4 py-3">
                {[0, 1, 2].map((dot) => (
                  <span key={dot} className="size-2.5 rounded-full bg-foreground/20" />
                ))}
                <span className="ml-3 font-mono text-[11px] text-muted-foreground">{t("openSource.commandLabel")}</span>
              </div>
              <pre className="overflow-x-auto p-6 font-mono text-[13px] leading-8">
                <code>
                  <span className="text-muted-foreground">$ </span>git clone https://github.com/FujiwaraChoki/supoclip{"\n"}
                  <span className="text-muted-foreground">$ </span>cd supoclip && cp .env.example .env{"\n"}
                  <span className="text-muted-foreground">{t("openSource.comment")}</span>{"\n"}
                  <span className="text-muted-foreground">$ </span>docker compose up -d --build{"\n"}
                  <span className="text-emerald-400">✓</span> <span className="text-muted-foreground">{t("openSource.ready")}</span>
                </code>
              </pre>
            </div>
          </Reveal>
        </div>
      </section>

      {/* ─── GUIDES ─── */}
      <section id="guides" className="scroll-mt-24 py-24 md:py-32">
        <div className="mx-auto max-w-6xl px-6">
          <div className="flex flex-col justify-between gap-6 md:flex-row md:items-end">
            <SectionHeading eyebrow={t("guides.eyebrow")} title={t("guides.title")} body={t("guides.body")} align="left" />
          </div>
          <div className="mt-12 grid border-t md:grid-cols-2 md:gap-x-12">
            {SEO_RESOURCES.map((resource, index) => (
              <Reveal key={resource.href} delay={(index % 2) * 0.06}>
                <Link
                  href={resource.href}
                  lang={resource.language}
                  className="group flex items-start justify-between gap-6 border-b py-6 transition-colors"
                >
                  <span>
                    <span className="text-sm text-muted-foreground">
                      {t(`links.${catalogKey(resource.href)}.eyebrow`)}
                    </span>
                    <span className="mt-2 block text-lg font-semibold tracking-tight underline-offset-4 group-hover:underline">
                      {t(`links.${catalogKey(resource.href)}.title`)}
                    </span>
                    <span className="mt-1.5 block text-sm leading-relaxed text-muted-foreground">
                      {t(`links.${catalogKey(resource.href)}.description`)}
                    </span>
                  </span>
                  <span className="mt-1 flex size-9 shrink-0 items-center justify-center rounded-full border transition-colors group-hover:bg-foreground group-hover:text-background">
                    <ArrowUpRight className="size-4" />
                  </span>
                </Link>
              </Reveal>
            ))}
          </div>
        </div>
      </section>

      {/* ─── FINAL CTA ─── */}
      <section className="px-3 pb-24 md:pb-32">
        <Reveal>
          <div className="dark relative mx-auto max-w-6xl overflow-hidden rounded-[36px] bg-background px-6 py-20 text-center text-foreground md:py-28">
            <div
              aria-hidden
              className="pointer-events-none absolute inset-0 opacity-[0.07] [background-image:radial-gradient(circle_at_1px_1px,currentColor_1px,transparent_0)] [background-size:28px_28px] [mask-image:radial-gradient(ellipse_at_center,black,transparent_70%)]"
            />
            <div className="relative">
              <h2 className="mx-auto max-w-3xl font-display text-4xl font-bold leading-[1.02] tracking-[-0.03em] text-balance sm:text-6xl">
                {t("cta.title")}
              </h2>
              <p className="mx-auto mt-6 max-w-xl text-base leading-relaxed text-muted-foreground">{t("cta.body")}</p>
              <div className="mt-10 flex flex-wrap items-center justify-center gap-3">
                <PrimaryCta authEnabled={authEnabled} label={t("nav.getStarted")} hostedLabel={t("nav.openHosted")} />
                <a
                  href={APP_STORE_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-label={t("hero.appStoreAria")}
                  className="flex items-center"
                >
                  <Image src="/app-store-badge.svg" alt={t("hero.appStoreAlt")} width={135} height={40} className="h-12 w-auto" />
                </a>
              </div>
              <a href={PRODUCT_HUNT_URL} target="_blank" rel="noopener noreferrer" className="mt-10 inline-block opacity-80 transition-opacity hover:opacity-100">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={PRODUCT_HUNT_BADGE_URL} alt={t("hero.productHuntAlt")} width={200} height={43} />
              </a>
            </div>
          </div>
        </Reveal>
      </section>

      <SiteFooter />
    </div>
  );
}
