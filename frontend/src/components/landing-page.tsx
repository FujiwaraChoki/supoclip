"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import Image from "next/image";
import { Button } from "@/components/ui/button";
import { CreatorOfferBanner } from "@/components/marketing/creator-offer-banner";
import { Announcements } from "@/components/announcements";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
  Scissors,
  Sparkles,
  Youtube,
  Github,
  ArrowRight,
  Play,
  Target,
  ScanFace,
  Type,
  Film,
  MonitorPlay,
  Share2,
  Wand2,
  ChevronDown,
  ExternalLink,
  Check,
  Clock,
  Zap,
  Menu,
  X,
  Volume2,
  VolumeX,
} from "lucide-react";
import { isLandingOnlyModeEnabled } from "@/lib/app-flags";
import {
  annualMonthlyEquivalent,
  billingPlanMessageKey,
  getPublicBillingPlans,
  planPrice,
  type BillingInterval,
} from "@/lib/billing-plans";
import { BillingIntervalToggle, ComingSoonTag, useBillingPlanText } from "@/components/billing/upgrade-prompt";
import { catalogKey } from "@/i18n/config";
import { SiteFooter } from "@/components/marketing/site-footer";
import {
  OPUSCLIP_COMPARISON_HREF,
  SELF_HOSTING_GUIDE_HREF,
  getMarketingLink,
  type MarketingLink,
} from "@/lib/marketing-links";
import { DEMO_CLIPS, DEMO_SOURCE } from "@/lib/demo";
import { APP_STORE_URL, GITHUB_URL, HOSTED_APP_URL, PRODUCT_HUNT_BADGE_URL, PRODUCT_HUNT_URL } from "@/lib/site";

function ScrollReveal({
  children,
  className = "",
  delay = 0,
}: {
  children: React.ReactNode;
  className?: string;
  delay?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [isVisible, setIsVisible] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setIsVisible(true);
          observer.unobserve(entry.target);
        }
      },
      { threshold: 0.1, rootMargin: "0px 0px -60px 0px" }
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return (
    <div
      ref={ref}
      className={className}
      style={{
        opacity: isVisible ? 1 : 0,
        transform: isVisible ? "translateY(0)" : "translateY(30px)",
        transition: `opacity 0.7s cubic-bezier(0.16, 1, 0.3, 1) ${delay}s, transform 0.7s cubic-bezier(0.16, 1, 0.3, 1) ${delay}s`,
      }}
    >
      {children}
    </div>
  );
}

const FEATURES = [
  { icon: ScanFace, key: "faceCrop" },
  { icon: Type, key: "subtitles" },
  { icon: Target, key: "virality" },
  { icon: Film, key: "broll" },
  { icon: Sparkles, key: "templates" },
  { icon: MonitorPlay, key: "export" },
] as const;

type Translate = (key: string, values?: Record<string, string | number>) => string;

const PRICING_EXAMPLE_VIDEO_MINUTES = 30;
const PRICING_EXAMPLE_CLIPS_PER_VIDEO = 6;

function getPlans(
  t: Translate,
  tCommon: Translate,
  interval: BillingInterval,
  planText: ReturnType<typeof useBillingPlanText>,
) {
  return [
    {
      id: "selfHost",
      name: tCommon("plans.selfHost"),
      price: "$0",
      period: t("pricing.period.forever"),
      note: null as string | null,
      soon: null as string | null,
      usageExample: null as { videos: string; clips: string } | null,
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
      return {
        id: plan.id,
        name: tCommon(`plans.${billingPlanMessageKey(plan.id)}`),
        price: `$${planPrice(plan, interval)}`,
        period: interval === "year" ? t("pricing.period.year") : t("pricing.period.month"),
        note: interval === "year" ? t("pricing.billedYearly", { price: annualMonthlyEquivalent(plan) }) : null,
        soon: planText.comingSoon(plan),
        usageExample: exampleVideos > 0 ? {
          videos: t("pricing.usageExample.videos", {
            count: exampleVideos,
            minutes: PRICING_EXAMPLE_VIDEO_MINUTES,
          }),
          clips: t("pricing.usageExample.clips", {
            count: exampleVideos * PRICING_EXAMPLE_CLIPS_PER_VIDEO,
            perVideo: PRICING_EXAMPLE_CLIPS_PER_VIDEO,
          }),
        } : null,
        description: t(`pricing.${plan.id}.description`),
        features: [
          ...planText.highlights(plan),
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

const STEPS = [
  { num: "01", key: "drop", icon: Youtube },
  { num: "02", key: "gold", icon: Wand2 },
  { num: "03", key: "export", icon: Share2 },
] as const;

// Featured on the landing page; the footer links every marketing page.
const SEO_RESOURCES = [
  OPUSCLIP_COMPARISON_HREF,
  SELF_HOSTING_GUIDE_HREF,
  "/ai-video-clipper",
  "/youtube-shorts-clipper",
  "/open-source-video-clipper",
  "/blog/supoclip-vs-supo-live",
].map((href) => getMarketingLink(href)).filter((link): link is MarketingLink => Boolean(link));

export default function LandingPage() {
  const t = useTranslations("marketing");
  const tCommon = useTranslations("common");
  const planText = useBillingPlanText();
  const [billingInterval, setBillingInterval] = useState<BillingInterval>("month");
  const plans = getPlans(t, tCommon, billingInterval, planText);
  const features = FEATURES.map((feature) => ({
    ...feature,
    title: t(`features.items.${feature.key}.title`),
    description: t(`features.items.${feature.key}.description`),
  }));
  const steps = STEPS.map((step) => ({
    ...step,
    title: t(`steps.items.${step.key}.title`),
    description: t(`steps.items.${step.key}.description`),
  }));
  const [scrolled, setScrolled] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const authEnabled = !isLandingOnlyModeEnabled;

  useEffect(() => {
    const handleScroll = () => setScrolled(window.scrollY > 20);
    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  return (
    <div className="min-h-screen bg-background text-foreground">
      {authEnabled && <CreatorOfferBanner />}
      {/* ─── NAV ─── */}
      <nav
        className={`fixed top-0 left-0 right-0 z-50 transition-all duration-300 ${
          scrolled
            ? "bg-background/80 backdrop-blur-xl border-b shadow-sm"
            : "bg-transparent"
        }`}
      >
        <div className="max-w-6xl mx-auto px-6 h-16 flex items-center justify-between">
          <Link href="/" className="flex items-center gap-2.5 group">
            <Image
              src="/logo.png"
              alt="SupoClip"
              width={24}
              height={24}
              className="rounded-lg transition-transform group-hover:scale-105"
            />
            <span
              className="text-lg font-bold tracking-tight"
              style={{
                fontFamily:
                  "var(--font-syne), var(--font-geist-sans), system-ui",
              }}
            >
              SupoClip
            </span>
          </Link>

          <div className="hidden md:flex items-center gap-8">
            <a
              href="#how-it-works"
              className="text-sm text-muted-foreground hover:text-foreground transition-colors"
            >
              {t("nav.howItWorks")}
            </a>
            <a
              href="#features"
              className="text-sm text-muted-foreground hover:text-foreground transition-colors"
            >
              {t("nav.features")}
            </a>
            <a
              href="#pricing"
              className="text-sm text-muted-foreground hover:text-foreground transition-colors"
            >
              {t("nav.pricing")}
            </a>
            <a
              href="#open-source"
              className="text-sm text-muted-foreground hover:text-foreground transition-colors"
            >
              {t("nav.openSource")}
            </a>
            <Link
              href="/blog"
              className="text-sm text-muted-foreground hover:text-foreground transition-colors"
            >
              {t("nav.blog")}
            </Link>
            <Link
              href="/affiliate"
              className="text-sm text-muted-foreground hover:text-foreground transition-colors"
            >
              {t("nav.affiliate")}
            </Link>
          </div>

          {/* Desktop auth buttons */}
          <div className="hidden md:flex items-center gap-3">
            {authEnabled ? (
              <>
                <Link href="/sign-in">
                  <Button variant="ghost" size="sm">
                    {t("nav.signIn")}
                  </Button>
                </Link>
                <Link href="/sign-up">
                  <Button size="sm">{t("nav.getStarted")}</Button>
                </Link>
              </>
            ) : (
              <a href={HOSTED_APP_URL} target="_blank" rel="noopener noreferrer">
                <Button size="sm">
                  {t("nav.openHosted")}
                  <ExternalLink className="w-3.5 h-3.5" />
                </Button>
              </a>
            )}
          </div>

          {/* Mobile hamburger */}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setMobileNavOpen(!mobileNavOpen)}
            className="md:hidden p-2"
            aria-label={t("nav.toggleMenu")}
          >
            {mobileNavOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
          </Button>
        </div>

        {/* Mobile nav dropdown */}
        {mobileNavOpen && (
          <div className="md:hidden border-t bg-background/95 backdrop-blur-xl">
            <div className="max-w-6xl mx-auto px-6 py-4 space-y-1">
              <a
                href="#how-it-works"
                onClick={() => setMobileNavOpen(false)}
                className="block rounded-lg px-3 py-2.5 text-sm text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
              >
                {t("nav.howItWorks")}
              </a>
              <a
                href="#features"
                onClick={() => setMobileNavOpen(false)}
                className="block rounded-lg px-3 py-2.5 text-sm text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
              >
                {t("nav.features")}
              </a>
              <a
                href="#pricing"
                onClick={() => setMobileNavOpen(false)}
                className="block rounded-lg px-3 py-2.5 text-sm text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
              >
                {t("nav.pricing")}
              </a>
              <a
                href="#open-source"
                onClick={() => setMobileNavOpen(false)}
                className="block rounded-lg px-3 py-2.5 text-sm text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
              >
                {t("nav.openSource")}
              </a>
              <Link
                href="/blog"
                onClick={() => setMobileNavOpen(false)}
                className="block rounded-lg px-3 py-2.5 text-sm text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
              >
                {t("nav.blog")}
              </Link>
              <Link
                href="/affiliate"
                onClick={() => setMobileNavOpen(false)}
                className="block rounded-lg px-3 py-2.5 text-sm text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
              >
                {t("nav.affiliate")}
              </Link>
              <Separator className="my-2" />
              <div className="flex flex-col gap-2 px-3 pt-1">
                {authEnabled ? (
                  <>
                    <Link href="/sign-in" onClick={() => setMobileNavOpen(false)}>
                      <Button variant="outline" size="sm" className="w-full">
                        {t("nav.signIn")}
                      </Button>
                    </Link>
                    <Link href="/sign-up" onClick={() => setMobileNavOpen(false)}>
                      <Button size="sm" className="w-full">{t("nav.getStarted")}</Button>
                    </Link>
                  </>
                ) : (
                  <a href={HOSTED_APP_URL} target="_blank" rel="noopener noreferrer">
                    <Button size="sm" className="w-full">
                      {t("nav.openHosted")}
                      <ExternalLink className="w-3.5 h-3.5" />
                    </Button>
                  </a>
                )}
              </div>
            </div>
          </div>
        )}
      </nav>

      {/* ─── HERO ─── */}
      <section className="relative pt-32 pb-20 md:pt-40 md:pb-28 overflow-hidden">
        {/* Subtle background pattern */}
        <div
          className="absolute inset-0 opacity-[0.02]"
          style={{
            backgroundImage:
              "radial-gradient(circle at 1px 1px, currentColor 1px, transparent 0)",
            backgroundSize: "32px 32px",
          }}
        />

        <div className="relative max-w-6xl mx-auto px-6">
          <div className="grid lg:grid-cols-2 gap-16 lg:gap-20 items-center">
            {/* Left: Text */}
            <div>
              <div className="mb-4"><Announcements landing /></div>
              <Badge
                variant="secondary"
                className="mb-6 gap-2"
                style={{ animation: "landing-fade-in-up 0.6s ease-out both" }}
              >
                <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
                {t("hero.badge")}
              </Badge>

              <h1
                className="text-4xl sm:text-5xl lg:text-[3.5rem] font-extrabold leading-[1.08] tracking-tight text-foreground mb-6"
                style={{
                  fontFamily:
                    "var(--font-syne), var(--font-geist-sans), system-ui",
                  animation: "landing-fade-in-up 0.6s ease-out 0.1s both",
                }}
              >
                {t("hero.titleLine1")}
                <br />
                {t("hero.titleLine2")}
              </h1>

              <p
                className="text-base sm:text-lg text-muted-foreground leading-relaxed max-w-lg mb-10"
                style={{
                  animation: "landing-fade-in-up 0.6s ease-out 0.2s both",
                }}
              >
                {t("hero.body")}
              </p>

              <div
                className="flex flex-wrap gap-3 mb-10"
                style={{
                  animation: "landing-fade-in-up 0.6s ease-out 0.3s both",
                }}
              >
                {authEnabled ? (
                  <Link href="/sign-up">
                    <Button size="lg" className="px-8 h-12 text-sm">
                      {t("hero.startClipping")}
                      <ArrowRight className="w-4 h-4" />
                    </Button>
                  </Link>
                ) : (
                  <a href={HOSTED_APP_URL} target="_blank" rel="noopener noreferrer">
                    <Button size="lg" className="px-8 h-12 text-sm">
                      {t("hero.useHosted")}
                      <ExternalLink className="w-4 h-4" />
                    </Button>
                  </a>
                )}
                <a
                  href="https://github.com/FujiwaraChoki/supoclip"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <Button variant="outline" size="lg" className="px-8 h-12 text-sm">
                    <Github className="w-4 h-4" />
                    {t("hero.viewSource")}
                  </Button>
                </a>
                <a
                  href={APP_STORE_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-label={t("hero.appStoreAria")}
                  className="flex items-center"
                >
                  <Image
                    src="/app-store-badge.svg"
                    alt={t("hero.appStoreAlt")}
                    width={135}
                    height={40}
                    className="h-12 w-auto"
                  />
                </a>
              </div>

              <div
                className="flex flex-wrap gap-x-6 gap-y-2 text-xs text-muted-foreground"
                style={{
                  animation: "landing-fade-in-up 0.6s ease-out 0.4s both",
                }}
              >
                {[
                  { icon: ScanFace, label: t("hero.chips.crop") },
                  { icon: Type, label: t("hero.chips.captions") },
                  { icon: Target, label: t("hero.chips.virality") },
                ].map(({ icon: Icon, label }) => (
                  <div key={label} className="flex items-center gap-1.5">
                    <Icon className="w-3.5 h-3.5" />
                    {label}
                  </div>
                ))}
              </div>

              <a
                href={PRODUCT_HUNT_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-8 inline-block"
                style={{
                  animation: "landing-fade-in-up 0.6s ease-out 0.5s both",
                }}
              >
                {/* PH's dark badge sits with the App Store badge; its light one is brand orange. */}
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={PRODUCT_HUNT_BADGE_URL}
                  alt={t("hero.productHuntAlt")}
                  width={250}
                  height={54}
                />
              </a>
            </div>

            {/* Right: Visual */}
            <div
              className="relative flex justify-center lg:justify-end"
              style={{ animation: "landing-fade-in-up 0.8s ease-out 0.3s both" }}
            >
              <HeroVisual />
            </div>
          </div>
        </div>

        <div className="absolute bottom-6 left-1/2 -translate-x-1/2 hidden md:block">
          <ChevronDown className="w-5 h-5 text-muted-foreground/30 animate-bounce" />
        </div>
      </section>

      <Separator />

      {/* ─── HOW IT WORKS ─── */}
      <section id="how-it-works" className="py-20 md:py-28 bg-muted/40">
        <div className="max-w-6xl mx-auto px-6">
          <ScrollReveal className="text-center mb-14">
            <p className="text-xs font-semibold tracking-[0.2em] uppercase text-muted-foreground mb-3">
              {t("nav.howItWorks")}
            </p>
            <h2
              className="text-3xl sm:text-4xl font-bold tracking-tight"
              style={{
                fontFamily:
                  "var(--font-syne), var(--font-geist-sans), system-ui",
              }}
            >
              {t("steps.title")}
            </h2>
          </ScrollReveal>

          <div className="grid md:grid-cols-3 gap-6">
            {steps.map((step, i) => (
              <ScrollReveal key={step.num} delay={i * 0.1}>
                <Card className="h-full py-0 gap-0 hover:shadow-md transition-shadow duration-300">
                  <CardContent className="p-8">
                    <span
                      className="text-6xl font-black leading-none block mb-6 text-muted-foreground/25 select-none"
                      style={{ fontFamily: "var(--font-syne), system-ui" }}
                    >
                      {step.num}
                    </span>
                    <div className="w-10 h-10 rounded-xl bg-secondary flex items-center justify-center mb-5">
                      <step.icon className="w-5 h-5 text-foreground" />
                    </div>
                    <h3
                      className="text-lg font-semibold mb-2"
                      style={{ fontFamily: "var(--font-syne), system-ui" }}
                    >
                      {step.title}
                    </h3>
                    <p className="text-sm text-muted-foreground leading-relaxed">
                      {step.description}
                    </p>
                  </CardContent>
                </Card>
              </ScrollReveal>
            ))}
          </div>
        </div>
      </section>

      <Separator />

      {/* ─── FEATURES ─── */}
      <section id="features" className="py-20 md:py-28">
        <div className="max-w-6xl mx-auto px-6">
          <ScrollReveal className="text-center mb-14">
            <p className="text-xs font-semibold tracking-[0.2em] uppercase text-muted-foreground mb-3">
              {t("nav.features")}
            </p>
            <h2
              className="text-3xl sm:text-4xl font-bold tracking-tight mb-4"
              style={{
                fontFamily:
                  "var(--font-syne), var(--font-geist-sans), system-ui",
              }}
            >
              {t("features.title")}
            </h2>
            <p className="text-sm text-muted-foreground max-w-md mx-auto">
              {t("features.body")}
            </p>
          </ScrollReveal>

          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
            {features.map((feature, i) => (
              <ScrollReveal key={feature.title} delay={i * 0.07}>
                <Card className="h-full py-0 gap-0 hover:shadow-md transition-all duration-300 hover:-translate-y-1">
                  <CardContent className="p-6">
                    <div className="w-10 h-10 rounded-xl bg-secondary flex items-center justify-center mb-4">
                      <feature.icon className="w-5 h-5 text-foreground" />
                    </div>
                    <h3 className="font-semibold mb-2">{feature.title}</h3>
                    <p className="text-sm text-muted-foreground leading-relaxed">
                      {feature.description}
                    </p>
                  </CardContent>
                </Card>
              </ScrollReveal>
            ))}
          </div>
        </div>
      </section>

      <Separator />

      {/* ─── PRICING ─── */}
      <section id="pricing" className="relative py-20 md:py-28 bg-muted/40 overflow-hidden">
        {/* Decorative background grain */}
        <div
          className="absolute inset-0 opacity-[0.03] pointer-events-none"
          style={{
            backgroundImage:
              "radial-gradient(circle at 2px 2px, currentColor 0.5px, transparent 0)",
            backgroundSize: "24px 24px",
          }}
        />

        <div className="relative max-w-5xl mx-auto px-6">
          <ScrollReveal className="text-center mb-16">
            <p className="text-xs font-semibold tracking-[0.2em] uppercase text-muted-foreground mb-3">
              {t("nav.pricing")}
            </p>
            <h2
              className="text-3xl sm:text-4xl font-bold tracking-tight mb-4"
              style={{
                fontFamily:
                  "var(--font-syne), var(--font-geist-sans), system-ui",
              }}
            >
              {t("pricing.title")}
            </h2>
            <p className="text-sm text-muted-foreground max-w-md mx-auto">
              {t("pricing.body", { price: getPublicBillingPlans()[0]?.priceMonthly ?? "9.99" })}
            </p>
            <BillingIntervalToggle
              className="mt-6 bg-background"
              value={billingInterval}
              onChange={setBillingInterval}
              plan={getPublicBillingPlans()[0]}
            />
          </ScrollReveal>

          <div className="grid md:grid-cols-3 gap-6 max-w-5xl mx-auto items-start">
            {plans.map((plan, i) => (
              <ScrollReveal key={plan.id} delay={i * 0.12}>
                <Card
                  className={`relative py-0 gap-0 transition-all duration-300 hover:shadow-lg ${
                    plan.highlighted
                      ? "bg-primary text-primary-foreground border-primary shadow-xl md:-mt-4 md:mb-4"
                      : "hover:-translate-y-1"
                  }`}
                >
                  {plan.highlighted && (
                    <div className="absolute -top-3.5 left-1/2 -translate-x-1/2">
                      <Badge className="bg-foreground text-background border-0 shadow-md gap-1.5 px-3 py-1">
                        <Zap className="w-3 h-3" />
                        {t("pricing.popular")}
                      </Badge>
                    </div>
                  )}

                  <CardContent className="p-8">
                    <div className="mb-6">
                      <h3
                        className="text-lg font-semibold mb-1"
                        style={{ fontFamily: "var(--font-syne), system-ui" }}
                      >
                        {plan.name}
                      </h3>
                      <p
                        className={`text-sm ${
                          plan.highlighted
                            ? "text-primary-foreground/70"
                            : "text-muted-foreground"
                        }`}
                      >
                        {plan.description}
                      </p>
                    </div>

                    <div className="mb-8 w-full @container">
                      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                        <span
                          className="max-w-full whitespace-nowrap text-[clamp(1.5rem,15cqi,3rem)] font-extrabold tracking-tight"
                          style={{ fontFamily: "var(--font-syne), system-ui" }}
                        >
                          {plan.price}
                        </span>
                        <span
                          className={`shrink-0 whitespace-nowrap text-sm ${
                            plan.highlighted
                              ? "text-primary-foreground/60"
                              : "text-muted-foreground"
                          }`}
                        >
                          {plan.period}
                        </span>
                      </div>
                      {plan.note && (
                        <p
                          className={`mt-1 text-xs ${
                            plan.highlighted ? "text-primary-foreground/60" : "text-muted-foreground"
                          }`}
                        >
                          {plan.note}
                        </p>
                      )}
                    </div>

                    <ul className="space-y-3 mb-8">
                      {plan.features.map((feature) => (
                        <li key={feature} className="flex items-start gap-3 text-sm">
                          <Check
                            className={`w-4 h-4 mt-0.5 shrink-0 ${
                              plan.highlighted
                                ? "text-primary-foreground/80"
                                : "text-muted-foreground"
                            }`}
                          />
                          <span
                            className={
                              plan.highlighted
                                ? "text-primary-foreground/90"
                                : ""
                            }
                          >
                            {feature}
                          </span>
                        </li>
                      ))}
                      {plan.soon && (
                        <li className="flex items-start gap-3 text-sm">
                          <Clock
                            className={`w-4 h-4 mt-0.5 shrink-0 ${
                              plan.highlighted ? "text-primary-foreground/60" : "text-muted-foreground"
                            }`}
                          />
                          <span className={plan.highlighted ? "text-primary-foreground/70" : "text-muted-foreground"}>
                            {plan.soon} <ComingSoonTag />
                          </span>
                        </li>
                      )}
                    </ul>

                    {plan.usageExample && (
                      <div className={`mb-6 space-y-1 text-xs leading-relaxed ${
                        plan.highlighted ? "text-primary-foreground/75" : "text-muted-foreground"
                      }`}>
                        <p className="font-medium">{plan.usageExample.videos}</p>
                        <p>{plan.usageExample.clips}</p>
                      </div>
                    )}

                    {plan.ctaHref ? (
                      <div className="flex flex-col gap-3">
                        <p className="text-xs leading-relaxed text-muted-foreground">
                          {t("pricing.selfHost.setup")}
                        </p>
                        <Link href={SELF_HOSTING_GUIDE_HREF}>
                          <Button className="w-full h-11 text-sm" variant="outline" size="lg">
                            {t(`links.${catalogKey(SELF_HOSTING_GUIDE_HREF)}.label`)}
                            <ArrowRight className="w-4 h-4" />
                          </Button>
                        </Link>
                        <a
                          href={plan.ctaHref}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex items-center justify-center gap-2 text-xs text-muted-foreground underline underline-offset-4 hover:text-foreground"
                        >
                          <Github className="w-3.5 h-3.5" />
                          {plan.cta}
                          <ExternalLink className="w-3.5 h-3.5 opacity-50" />
                        </a>
                      </div>
                    ) : authEnabled ? (
                      <Link href="/sign-up">
                        <Button
                          className={`w-full h-11 text-sm ${
                            plan.highlighted
                              ? "bg-primary-foreground text-primary hover:bg-primary-foreground/90"
                              : ""
                          }`}
                          variant={plan.highlighted ? "secondary" : "default"}
                          size="lg"
                        >
                          {plan.cta}
                          <ArrowRight className="w-4 h-4" />
                        </Button>
                      </Link>
                    ) : (
                      <a
                        href={HOSTED_APP_URL}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        <Button
                          className={`w-full h-11 text-sm ${
                            plan.highlighted
                              ? "bg-primary-foreground text-primary hover:bg-primary-foreground/90"
                              : ""
                          }`}
                          variant={plan.highlighted ? "secondary" : "default"}
                          size="lg"
                        >
                          {t("hero.useHosted")}
                          <ExternalLink className="w-4 h-4" />
                        </Button>
                      </a>
                    )}
                  </CardContent>
                </Card>
              </ScrollReveal>
            ))}
          </div>

          <ScrollReveal delay={0.3}>
            <p className="text-center text-xs text-muted-foreground mt-10 max-w-md mx-auto">
              {t("pricing.selfHostNote")}{" "}
              <Link
                href={SELF_HOSTING_GUIDE_HREF}
                className="underline underline-offset-2 hover:text-foreground transition-colors"
              >
                {t("pricing.selfHostGuide")}
              </Link>
              .
            </p>
          </ScrollReveal>
        </div>
      </section>

      <Separator />

      {/* ─── OPEN SOURCE ─── */}
      <section id="open-source" className="py-20 md:py-28 bg-muted/40">
        <div className="max-w-3xl mx-auto px-6">
          <ScrollReveal className="text-center mb-10">
            <Badge variant="outline" className="mb-6 gap-1.5">
              <Github className="w-3.5 h-3.5" />
              {t("openSource.badge")}
            </Badge>
            <h2
              className="text-3xl sm:text-4xl font-bold tracking-tight mb-4"
              style={{ fontFamily: "var(--font-syne), system-ui" }}
            >
              {t("openSource.title")}
            </h2>
            <p className="text-sm text-muted-foreground max-w-lg mx-auto">
              {t("openSource.body")}
            </p>
          </ScrollReveal>

          <ScrollReveal delay={0.1}>
            <Card className="py-0 gap-0">
              <CardContent className="p-6 md:p-8">
                <p className="text-xs font-medium text-muted-foreground mb-3">
                  {t("openSource.commandLabel")}
                </p>
                <div className="bg-primary text-primary-foreground rounded-lg p-5 font-mono text-sm leading-loose overflow-x-auto">
                  <div>
                    <span className="opacity-50">$</span>{" "}
                    git clone{" "}
                    <span className="opacity-40">
                      https://github.com/FujiwaraChoki/supoclip
                    </span>
                  </div>
                  <div>
                    <span className="opacity-50">$</span>{" "}
                    cd supoclip && cp .env.example .env
                  </div>
                  <div className="opacity-40">{t("openSource.comment")}</div>
                  <div>
                    <span className="opacity-50">$</span>{" "}
                    docker compose up -d --build
                  </div>
                </div>

                <div className="flex flex-wrap gap-3 mt-6">
                  <a
                    href="https://github.com/FujiwaraChoki/supoclip"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    <Button>
                      <Github className="w-4 h-4" />
                      {t("pricing.selfHost.cta")}
                      <ExternalLink className="w-3.5 h-3.5 opacity-50" />
                    </Button>
                  </a>
                  <Link href={SELF_HOSTING_GUIDE_HREF}>
                    <Button variant="outline">{t(`links.${catalogKey(SELF_HOSTING_GUIDE_HREF)}.label`)}</Button>
                  </Link>
                  {authEnabled ? (
                    <Link href="/sign-up">
                      <Button variant="outline">
                        {t("openSource.tryHosted")}
                        <ArrowRight className="w-4 h-4" />
                      </Button>
                    </Link>
                  ) : (
                    <a href={HOSTED_APP_URL} target="_blank" rel="noopener noreferrer">
                      <Button variant="outline">
                        {t("openSource.openHosted")}
                        <ExternalLink className="w-4 h-4" />
                      </Button>
                    </a>
                  )}
                </div>
              </CardContent>
            </Card>
          </ScrollReveal>
        </div>
      </section>

      <Separator />

      {/* ─── GUIDES ─── */}
      <section id="guides" className="py-20 md:py-28">
        <div className="mx-auto max-w-6xl px-6">
          <ScrollReveal className="mb-12 text-center">
            <p className="mb-3 text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground">
              {t("guides.eyebrow")}
            </p>
            <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">
              {t("guides.title")}
            </h2>
            <p className="mx-auto mt-4 max-w-2xl text-sm leading-7 text-muted-foreground">
              {t("guides.body")}
            </p>
          </ScrollReveal>
          <div className="grid gap-5 md:grid-cols-2">
            {SEO_RESOURCES.map((resource, index) => (
              <ScrollReveal key={resource.href} delay={index * 0.08}>
                <Link
                  href={resource.href}
                  className="group block h-full rounded-lg border bg-card p-6 transition-all hover:-translate-y-0.5 hover:border-foreground/25 hover:shadow-md"
                >
                  <p className="text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                    {t(`links.${catalogKey(resource.href)}.eyebrow`)}
                  </p>
                  <h3 className="mt-3 text-xl font-bold tracking-tight">{t(`links.${catalogKey(resource.href)}.title`)}</h3>
                  <p className="mt-3 text-sm leading-7 text-muted-foreground">{t(`links.${catalogKey(resource.href)}.description`)}</p>
                  <span className="mt-5 inline-flex items-center gap-2 text-sm font-semibold">
                    {t("guides.read")} <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
                  </span>
                </Link>
              </ScrollReveal>
            ))}
          </div>
        </div>
      </section>

      <Separator />

      {/* ─── FINAL CTA ─── */}
      <section className="py-20 md:py-28">
        <ScrollReveal className="max-w-2xl mx-auto px-6 text-center">
          <h2
            className="text-3xl sm:text-4xl lg:text-5xl font-bold tracking-tight mb-6"
            style={{ fontFamily: "var(--font-syne), system-ui" }}
          >
            {t("cta.title")}
          </h2>
          <p className="text-base text-muted-foreground mb-8">
            {t("cta.body")}
          </p>
          {authEnabled ? (
            <Link href="/sign-up">
              <Button size="lg" className="px-10 h-12 text-sm">
                {t("nav.getStarted")}
                <ArrowRight className="w-4 h-4" />
              </Button>
            </Link>
          ) : (
            <a href={HOSTED_APP_URL} target="_blank" rel="noopener noreferrer">
              <Button size="lg" className="px-10 h-12 text-sm">
                {t("nav.openHosted")}
                <ExternalLink className="w-4 h-4" />
              </Button>
            </a>
          )}
        </ScrollReveal>
      </section>


      <SiteFooter />
    </div>
  );
}

/* ─── Hero Visual ─── */

function HeroVisual() {
  const t = useTranslations("marketing");
  const videoRefs = useRef<(HTMLVideoElement | null)[]>([]);
  const [active, setActive] = useState(0);
  const [muted, setMuted] = useState(true);
  const [playing, setPlaying] = useState(true);
  const [progress, setProgress] = useState(0);

  // Only the active clip plays; the others stay parked at frame zero.
  useEffect(() => {
    setProgress(0);
    videoRefs.current.forEach((video, i) => {
      if (!video) return;
      if (i !== active) {
        video.pause();
        video.currentTime = 0;
        return;
      }
      video.currentTime = 0;
      video
        .play()
        .then(() => setPlaying(true))
        .catch(() => setPlaying(false));
    });
  }, [active]);

  // React can drop the `muted` attribute on hydration — set the property too.
  useEffect(() => {
    videoRefs.current.forEach((video) => {
      if (video) video.muted = muted;
    });
  }, [muted]);

  const togglePlay = () => {
    const video = videoRefs.current[active];
    if (!video) return;
    if (video.paused) {
      video
        .play()
        .then(() => setPlaying(true))
        .catch(() => setPlaying(false));
    } else {
      video.pause();
      setPlaying(false);
    }
  };

  return (
    <div className="relative w-full max-w-[360px]">
      <Card className="py-0 gap-0 overflow-hidden shadow-xl border-border/60">
        <CardContent className="p-4">
          {/* Source video being clipped */}
          <a
            href={DEMO_SOURCE.url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2.5 group"
          >
            <div className="w-8 h-8 rounded-md bg-red-500/10 flex items-center justify-center shrink-0">
              <Youtube className="w-4 h-4 text-red-500" />
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-xs font-medium truncate group-hover:underline">
                {DEMO_SOURCE.title}
              </p>
              <p className="text-[10px] text-muted-foreground">
                {t("hero.player.source")}
              </p>
            </div>
            <ExternalLink className="w-3.5 h-3.5 text-muted-foreground/60 shrink-0" />
          </a>

          {/* Scissors divider */}
          <div className="flex items-center gap-2 my-3.5">
            <div className="flex-1 h-px bg-border" />
            <span className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
              <Scissors className="w-3 h-3 rotate-90" />
              {t("hero.player.clipsFound", { count: DEMO_CLIPS.length })}
            </span>
            <div className="flex-1 h-px bg-border" />
          </div>

          {/* Player */}
          <div
            className="relative mx-auto w-[214px] rounded-xl overflow-hidden bg-black ring-1 ring-border shadow-lg"
            style={{ aspectRatio: "9/16" }}
          >
            {DEMO_CLIPS.map((clip, i) => (
              <video
                key={clip.src}
                ref={(el) => {
                  videoRefs.current[i] = el;
                }}
                src={clip.src}
                poster={clip.poster}
                muted
                playsInline
                preload={i === 0 ? "metadata" : "none"}
                aria-label={clip.hook}
                className={`absolute inset-0 w-full h-full object-cover transition-opacity duration-500 ${
                  i === active ? "opacity-100" : "opacity-0"
                }`}
                onTimeUpdate={(e) => {
                  if (i !== active) return;
                  const el = e.currentTarget;
                  if (el.duration) setProgress(el.currentTime / el.duration);
                }}
                onEnded={() => setActive((prev) => (prev + 1) % DEMO_CLIPS.length)}
              />
            ))}

            {/* Click-to-pause surface */}
            <button
              type="button"
              onClick={togglePlay}
              aria-label={playing ? t("hero.player.pause") : t("hero.player.play")}
              className="absolute inset-0 flex items-center justify-center focus:outline-none"
            >
              <span
                className={`w-11 h-11 rounded-full bg-background/85 backdrop-blur-sm flex items-center justify-center shadow-lg transition-opacity duration-200 ${
                  playing ? "opacity-0" : "opacity-100"
                }`}
              >
                <Play className="w-4.5 h-4.5 text-foreground ml-0.5" />
              </span>
            </button>

            {/* Mute toggle */}
            <button
              type="button"
              onClick={() => setMuted((m) => !m)}
              aria-label={muted ? t("hero.player.unmute") : t("hero.player.mute")}
              className="absolute top-2 right-2 w-7 h-7 rounded-full bg-black/45 backdrop-blur-sm flex items-center justify-center text-white/90 hover:bg-black/65 transition-colors"
            >
              {muted ? (
                <VolumeX className="w-3.5 h-3.5" />
              ) : (
                <Volume2 className="w-3.5 h-3.5" />
              )}
            </button>

            {/* Timestamp + progress */}
            <div className="absolute inset-x-0 bottom-0 pt-8 pb-2 px-2.5 bg-gradient-to-t from-black/70 to-transparent pointer-events-none">
              <p className="text-[10px] font-medium text-white/85 mb-1.5 tabular-nums">
                {DEMO_CLIPS[active].range}
              </p>
              <div className="h-0.5 rounded-full bg-white/25 overflow-hidden">
                <div
                  className="h-full bg-white/90"
                  style={{ width: `${Math.min(progress * 100, 100)}%` }}
                />
              </div>
            </div>
          </div>

          {/* Clip list */}
          <div className="mt-3.5 space-y-1.5">
            {DEMO_CLIPS.map((clip, i) => (
              <button
                key={clip.src}
                type="button"
                onClick={() => setActive(i)}
                aria-current={i === active}
                className={`w-full flex items-center gap-2.5 p-1.5 rounded-lg text-left transition-colors ${
                  i === active
                    ? "bg-muted ring-1 ring-border"
                    : "hover:bg-muted/60"
                }`}
              >
                <Image
                  src={clip.poster}
                  alt=""
                  width={30}
                  height={53}
                  className="rounded shrink-0 object-cover"
                />
                <span className="min-w-0 flex-1">
                  <span className="block text-[11px] font-medium leading-tight truncate">
                    {clip.hook}
                  </span>
                  <span className="block text-[10px] text-muted-foreground mt-0.5 tabular-nums">
                    {clip.range} · {clip.duration}
                  </span>
                </span>
                <Badge
                  variant={i === active ? "default" : "secondary"}
                  className="text-[9px] px-1.5 py-0 h-4 shrink-0"
                >
                  9:16
                </Badge>
              </button>
            ))}
          </div>
          <Link
            href="/demo"
            className="mt-3 flex items-center justify-center gap-1.5 text-[11px] font-medium text-muted-foreground hover:text-foreground transition-colors"
          >
            {t("hero.player.howMade")}
            <ArrowRight className="w-3 h-3" />
          </Link>
        </CardContent>
      </Card>

      {/* Decorative blur spots */}
      <div className="absolute -top-8 -right-8 w-32 h-32 rounded-full bg-muted/80 blur-3xl -z-10" />
      <div className="absolute -bottom-8 -left-8 w-24 h-24 rounded-full bg-muted/60 blur-2xl -z-10" />
    </div>
  );
}
