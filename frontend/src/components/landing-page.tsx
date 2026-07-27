"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { Button } from "@/components/ui/button";
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
  Zap,
  Menu,
  X,
  Volume2,
  VolumeX,
} from "lucide-react";
import { isLandingOnlyModeEnabled } from "@/lib/app-flags";
import { getPublicBillingPlans } from "@/lib/billing-plans";
import { GITHUB_URL, HOSTED_APP_URL } from "@/lib/site";

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
  {
    icon: ScanFace,
    title: "Face-Centered Cropping",
    description:
      "MediaPipe + OpenCV detects and tracks faces for perfect 9:16 vertical framing.",
  },
  {
    icon: Type,
    title: "Word-Synced Subtitles",
    description:
      "Word-level timestamps power perfectly timed, animated captions on every clip.",
  },
  {
    icon: Target,
    title: "Virality Scoring",
    description:
      "AI rates hook, engagement, value, and shareability — scored 0 to 100.",
  },
  {
    icon: Film,
    title: "B-Roll Overlays",
    description:
      "Automatically source and overlay relevant stock footage from Pexels.",
  },
  {
    icon: Sparkles,
    title: "Caption Templates",
    description:
      "Multiple animation styles and font presets to match your brand.",
  },
  {
    icon: MonitorPlay,
    title: "Platform Export",
    description:
      "One-click presets for TikTok, Reels, and Shorts with optimized encoding.",
  },
];

function getPlans() {
  return [
    {
      name: "Self-Hosted",
      price: "$0",
      period: "forever",
      description: "Run on your own infrastructure with full control.",
      features: [
        "Face-centered cropping",
        "Word-synced subtitles",
        "Virality scoring",
        "All export presets",
        "Full source code access",
      ],
      cta: "View on GitHub",
      ctaHref: GITHUB_URL,
      highlighted: false,
    },
    ...getPublicBillingPlans().map((plan) => ({
      name: plan.name,
      price: `$${plan.priceMonthly}`,
      period: "/month",
      description: plan.description,
      features: [
        `${plan.generationLimit} generations per month`,
        "Everything in Free",
        "B-Roll overlays",
        "Caption templates",
        "Platform export presets",
        ...(plan.id === "scale" ? ["Priority processing"] : ["Early access to new features"]),
      ],
      cta: plan.cta,
      ctaHref: "",
      highlighted: plan.highlighted,
    })),
  ];
}

const STEPS = [
  {
    num: "01",
    title: "Drop a link or file",
    description:
      "Paste any YouTube URL or drag-and-drop your own video file.",
    icon: Youtube,
  },
  {
    num: "02",
    title: "AI finds the gold",
    description:
      "Transcription, virality scoring, and segment detection surface the best moments.",
    icon: Wand2,
  },
  {
    num: "03",
    title: "Export & publish",
    description:
      "Get vertical, captioned, face-tracked clips ready for every platform.",
    icon: Share2,
  },
];

const SEO_RESOURCES = [
  {
    href: "/ai-video-clipper",
    eyebrow: "AI Video Clipping",
    title: "AI video clipper for Shorts, Reels, and TikTok",
    description: "See how SupoClip finds moments, scores candidates, reframes faces, and adds captions.",
  },
  {
    href: "/open-source-video-clipper",
    eyebrow: "Self-Hosting",
    title: "Open-source video clipper you can control",
    description: "Compare hosted-only workflows with SupoClip's inspectable, self-hosted pipeline.",
  },
  {
    href: "/youtube-shorts-clipper",
    eyebrow: "YouTube to Shorts",
    title: "Repurpose long YouTube videos into Shorts",
    description: "Follow a practical workflow for selecting, captioning, reframing, and reviewing clips.",
  },
  {
    href: "/blog/best-free-opusclip-alternative",
    eyebrow: "Comparison",
    title: "Best free OpusClip alternative",
    description: "Compare SupoClip's open-source approach with a managed, credit-based clipping tool.",
  },
];

export default function LandingPage() {
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
              How It Works
            </a>
            <a
              href="#features"
              className="text-sm text-muted-foreground hover:text-foreground transition-colors"
            >
              Features
            </a>
            <a
              href="#pricing"
              className="text-sm text-muted-foreground hover:text-foreground transition-colors"
            >
              Pricing
            </a>
            <a
              href="#open-source"
              className="text-sm text-muted-foreground hover:text-foreground transition-colors"
            >
              Open Source
            </a>
            <Link
              href="/blog"
              className="text-sm text-muted-foreground hover:text-foreground transition-colors"
            >
              Blog
            </Link>
          </div>

          {/* Desktop auth buttons */}
          <div className="hidden md:flex items-center gap-3">
            {authEnabled ? (
              <>
                <Link href="/sign-in">
                  <Button variant="ghost" size="sm">
                    Sign In
                  </Button>
                </Link>
                <Link href="/sign-up">
                  <Button size="sm">Get Started</Button>
                </Link>
              </>
            ) : (
              <a href={HOSTED_APP_URL} target="_blank" rel="noopener noreferrer">
                <Button size="sm">
                  Open Hosted App
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
            aria-label="Toggle menu"
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
                How It Works
              </a>
              <a
                href="#features"
                onClick={() => setMobileNavOpen(false)}
                className="block rounded-lg px-3 py-2.5 text-sm text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
              >
                Features
              </a>
              <a
                href="#pricing"
                onClick={() => setMobileNavOpen(false)}
                className="block rounded-lg px-3 py-2.5 text-sm text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
              >
                Pricing
              </a>
              <a
                href="#open-source"
                onClick={() => setMobileNavOpen(false)}
                className="block rounded-lg px-3 py-2.5 text-sm text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
              >
                Open Source
              </a>
              <Link
                href="/blog"
                onClick={() => setMobileNavOpen(false)}
                className="block rounded-lg px-3 py-2.5 text-sm text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
              >
                Blog
              </Link>
              <Separator className="my-2" />
              <div className="flex flex-col gap-2 px-3 pt-1">
                {authEnabled ? (
                  <>
                    <Link href="/sign-in" onClick={() => setMobileNavOpen(false)}>
                      <Button variant="outline" size="sm" className="w-full">
                        Sign In
                      </Button>
                    </Link>
                    <Link href="/sign-up" onClick={() => setMobileNavOpen(false)}>
                      <Button size="sm" className="w-full">Get Started</Button>
                    </Link>
                  </>
                ) : (
                  <a href={HOSTED_APP_URL} target="_blank" rel="noopener noreferrer">
                    <Button size="sm" className="w-full">
                      Open Hosted App
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
              <Badge
                variant="secondary"
                className="mb-6 gap-2"
                style={{ animation: "landing-fade-in-up 0.6s ease-out both" }}
              >
                <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
                Open Source & Self-Hostable
              </Badge>

              <h1
                className="text-4xl sm:text-5xl lg:text-[3.5rem] font-extrabold leading-[1.08] tracking-tight text-foreground mb-6"
                style={{
                  fontFamily:
                    "var(--font-syne), var(--font-geist-sans), system-ui",
                  animation: "landing-fade-in-up 0.6s ease-out 0.1s both",
                }}
              >
                Open-source AI video clipper
                <br />
                for better shorts
              </h1>

              <p
                className="text-base sm:text-lg text-muted-foreground leading-relaxed max-w-lg mb-10"
                style={{
                  animation: "landing-fade-in-up 0.6s ease-out 0.2s both",
                }}
              >
                Turn long videos into captioned YouTube Shorts, TikToks, and Reels
                with AI-assisted highlight detection, virality scoring, and
                face-aware vertical crops.
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
                      Start Clipping
                      <ArrowRight className="w-4 h-4" />
                    </Button>
                  </Link>
                ) : (
                  <a href={HOSTED_APP_URL} target="_blank" rel="noopener noreferrer">
                    <Button size="lg" className="px-8 h-12 text-sm">
                      Use Hosted App
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
                    View Source
                  </Button>
                </a>
              </div>

              <div
                className="flex flex-wrap gap-x-6 gap-y-2 text-xs text-muted-foreground"
                style={{
                  animation: "landing-fade-in-up 0.6s ease-out 0.4s both",
                }}
              >
                {[
                  { icon: ScanFace, label: "9:16 Auto-Crop" },
                  { icon: Type, label: "Word-Synced Captions" },
                  { icon: Target, label: "Virality Scoring" },
                ].map(({ icon: Icon, label }) => (
                  <div key={label} className="flex items-center gap-1.5">
                    <Icon className="w-3.5 h-3.5" />
                    {label}
                  </div>
                ))}
              </div>
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
              How It Works
            </p>
            <h2
              className="text-3xl sm:text-4xl font-bold tracking-tight"
              style={{
                fontFamily:
                  "var(--font-syne), var(--font-geist-sans), system-ui",
              }}
            >
              Three steps. Zero effort.
            </h2>
          </ScrollReveal>

          <div className="grid md:grid-cols-3 gap-6">
            {STEPS.map((step, i) => (
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
              Features
            </p>
            <h2
              className="text-3xl sm:text-4xl font-bold tracking-tight mb-4"
              style={{
                fontFamily:
                  "var(--font-syne), var(--font-geist-sans), system-ui",
              }}
            >
              Everything you need to go viral
            </h2>
            <p className="text-sm text-muted-foreground max-w-md mx-auto">
              Professional-grade video clipping with AI intelligence at every
              step of the pipeline.
            </p>
          </ScrollReveal>

          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
            {FEATURES.map((feature, i) => (
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
              Pricing
            </p>
            <h2
              className="text-3xl sm:text-4xl font-bold tracking-tight mb-4"
              style={{
                fontFamily:
                  "var(--font-syne), var(--font-geist-sans), system-ui",
              }}
            >
              Simple pricing, no surprises
            </h2>
            <p className="text-sm text-muted-foreground max-w-md mx-auto">
              Start free. Upgrade when you need more generations.
              Self-hosters get everything free, always.
            </p>
          </ScrollReveal>

          <div className="grid md:grid-cols-3 gap-6 max-w-5xl mx-auto items-start">
            {getPlans().map((plan, i) => (
              <ScrollReveal key={plan.name} delay={i * 0.12}>
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
                        Most Popular
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

                    <div className="flex items-baseline gap-1 mb-8">
                      <span
                        className="text-5xl font-extrabold tracking-tight"
                        style={{ fontFamily: "var(--font-syne), system-ui" }}
                      >
                        {plan.price}
                      </span>
                      <span
                        className={`text-sm ${
                          plan.highlighted
                            ? "text-primary-foreground/60"
                            : "text-muted-foreground"
                        }`}
                      >
                        {plan.period}
                      </span>
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
                    </ul>

                    {plan.ctaHref ? (
                      <a
                        href={plan.ctaHref}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        <Button
                          className="w-full h-11 text-sm"
                          variant="outline"
                          size="lg"
                        >
                          <Github className="w-4 h-4" />
                          {plan.cta}
                          <ExternalLink className="w-3.5 h-3.5 opacity-50" />
                        </Button>
                      </a>
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
                          Use Hosted App
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
              Self-hosting? All features are free and unlimited.{" "}
              <a
                href="#open-source"
                className="underline underline-offset-2 hover:text-foreground transition-colors"
              >
                See setup instructions
              </a>
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
              AGPL-3.0 Licensed
            </Badge>
            <h2
              className="text-3xl sm:text-4xl font-bold tracking-tight mb-4"
              style={{ fontFamily: "var(--font-syne), system-ui" }}
            >
              Built in the open
            </h2>
            <p className="text-sm text-muted-foreground max-w-lg mx-auto">
              Fully open source. Self-host on your infrastructure, contribute
              features, or fork it and make it yours.
            </p>
          </ScrollReveal>

          <ScrollReveal delay={0.1}>
            <Card className="py-0 gap-0">
              <CardContent className="p-6 md:p-8">
                <p className="text-xs font-medium text-muted-foreground mb-3">
                  Get running in 30 seconds:
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
                    cd{" "}
                    <span className="opacity-40">supoclip</span>
                  </div>
                  <div>
                    <span className="opacity-50">$</span>{" "}
                    docker-compose up -d
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
                      View on GitHub
                      <ExternalLink className="w-3.5 h-3.5 opacity-50" />
                    </Button>
                  </a>
                  {authEnabled ? (
                    <Link href="/sign-up">
                      <Button variant="outline">
                        Try the hosted version
                        <ArrowRight className="w-4 h-4" />
                      </Button>
                    </Link>
                  ) : (
                    <a href={HOSTED_APP_URL} target="_blank" rel="noopener noreferrer">
                      <Button variant="outline">
                        Open hosted version
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
              Guides & Comparisons
            </p>
            <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">
              Learn the complete clipping workflow
            </h2>
            <p className="mx-auto mt-4 max-w-2xl text-sm leading-7 text-muted-foreground">
              Practical, source-backed pages for choosing a video clipper, self-hosting SupoClip,
              and turning long recordings into short-form content.
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
                    {resource.eyebrow}
                  </p>
                  <h3 className="mt-3 text-xl font-bold tracking-tight">{resource.title}</h3>
                  <p className="mt-3 text-sm leading-7 text-muted-foreground">{resource.description}</p>
                  <span className="mt-5 inline-flex items-center gap-2 text-sm font-semibold">
                    Read guide <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
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
            Ready to clip?
          </h2>
          <p className="text-base text-muted-foreground mb-8">
            Turn your next video into scroll-stopping shorts. Free, open source,
            no credit card required.
          </p>
          {authEnabled ? (
            <Link href="/sign-up">
              <Button size="lg" className="px-10 h-12 text-sm">
                Get Started Free
                <ArrowRight className="w-4 h-4" />
              </Button>
            </Link>
          ) : (
            <a href={HOSTED_APP_URL} target="_blank" rel="noopener noreferrer">
              <Button size="lg" className="px-10 h-12 text-sm">
                Open Hosted App
                <ExternalLink className="w-4 h-4" />
              </Button>
            </a>
          )}
        </ScrollReveal>
      </section>


      {/* ─── FOOTER ─── */}
      <footer className="border-t py-8 px-6">
        <div className="max-w-6xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <Image
              src="/logo.png"
              alt="SupoClip"
              width={24}
              height={24}
              className="rounded-md"
            />
            <span
              className="text-sm font-semibold"
              style={{ fontFamily: "var(--font-syne), system-ui" }}
            >
              SupoClip
            </span>
          </div>
          <nav aria-label="Footer navigation" className="flex flex-wrap items-center justify-center gap-x-5 gap-y-2 text-xs text-muted-foreground">
            <Link href="/ai-video-clipper" className="hover:text-foreground transition-colors">AI clipper</Link>
            <Link href="/open-source-video-clipper" className="hover:text-foreground transition-colors">Open source</Link>
            <Link href="/youtube-shorts-clipper" className="hover:text-foreground transition-colors">YouTube Shorts</Link>
            <Link href="/blog" className="hover:text-foreground transition-colors">Blog</Link>
            <Link href="/privacy" className="hover:text-foreground transition-colors">Privacy</Link>
            <Link href="/terms" className="hover:text-foreground transition-colors">Terms</Link>
            <a
              href={GITHUB_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-foreground transition-colors"
            >
              GitHub
            </a>
            <span>&copy; {new Date().getFullYear()}</span>
          </nav>
        </div>
      </footer>
    </div>
  );
}

/* ─── Hero Visual ─── */

/**
 * Real SupoClip output. Clips were generated from the source video below and
 * trimmed to 15s previews for the landing page (see public/clips/).
 */
const DEMO_SOURCE = {
  title: "Sam Altman — How to Start a Startup",
  url: "https://www.youtube.com/watch?v=Vv3CEAS_w34",
};

const DEMO_CLIPS = [
  {
    src: "/clips/demo-2.mp4",
    poster: "/clips/demo-2.jpg",
    hook: "Why chaos management is not teachable",
    range: "05:04 – 05:54",
    duration: "0:50",
  },
  {
    src: "/clips/demo-1.mp4",
    poster: "/clips/demo-1.jpg",
    hook: "Why your 10 week old startup is failing",
    range: "00:14 – 00:39",
    duration: "0:25",
  },
];

function HeroVisual() {
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
                youtube.com · long-form source
              </p>
            </div>
            <ExternalLink className="w-3.5 h-3.5 text-muted-foreground/60 shrink-0" />
          </a>

          {/* Scissors divider */}
          <div className="flex items-center gap-2 my-3.5">
            <div className="flex-1 h-px bg-border" />
            <span className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
              <Scissors className="w-3 h-3 rotate-90" />
              {DEMO_CLIPS.length} clips found
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
              aria-label={playing ? "Pause clip" : "Play clip"}
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
              aria-label={muted ? "Unmute clip" : "Mute clip"}
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
        </CardContent>
      </Card>

      {/* Decorative blur spots */}
      <div className="absolute -top-8 -right-8 w-32 h-32 rounded-full bg-muted/80 blur-3xl -z-10" />
      <div className="absolute -bottom-8 -left-8 w-24 h-24 rounded-full bg-muted/60 blur-2xl -z-10" />
    </div>
  );
}
