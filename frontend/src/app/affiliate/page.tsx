import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { ArrowRight, Gift, Link2, Percent, Search, Send } from "lucide-react";

import { SiteFooter } from "@/components/marketing/site-footer";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { getPublicBillingPlans } from "@/lib/billing-plans";
import { getSiteUrl } from "@/lib/site";

const APPLY_HREF = "/settings/creator-program";
const VIDEO = {
  src: "/videos/creator-program.mp4",
  poster: "/videos/creator-program-poster.jpg",
  durationSeconds: 27,
  uploadDate: "2026-10-09",
};

const title = "SupoClip Creator Program: Get Pro Free for Making Videos";
const description =
  "Make Shorts, Reels, TikToks or videos about SupoClip and get SupoClip Pro for free. Your audience gets 20% off their first 3 months with your code.";

export const metadata: Metadata = {
  title: { absolute: title },
  description,
  alternates: { canonical: `${getSiteUrl()}/affiliate` },
  openGraph: {
    title,
    description,
    url: `${getSiteUrl()}/affiliate`,
    siteName: "SupoClip",
    type: "website",
    images: [{ url: `${getSiteUrl()}${VIDEO.poster}`, width: 1920, height: 1080 }],
    videos: [{ url: `${getSiteUrl()}${VIDEO.src}`, width: 1920, height: 1080, type: "video/mp4" }],
  },
  twitter: { card: "summary_large_image", title, description },
};

const steps = [
  { icon: Send, title: "Apply", detail: "Tell us where you post and pick your code, like MAYA. It takes two minutes." },
  { icon: Search, title: "We review", detail: "Every application is reviewed by hand. You get an email either way." },
  { icon: Link2, title: "Share", detail: "Put your link in your bio or say your code in your videos. Your account gets Pro right away." },
];

const rules = [
  "Mark sponsored posts as such, for example with #ad.",
  "Don't run ads on the SupoClip name or post your code on coupon or deal sites.",
  "Don't use your own code.",
  "We can end the program or remove a creator at any time.",
];

function getFaqs(proPrice: string) {
  return [
    {
      question: "Do I get paid in cash?",
      answer: `Not right now. Approved creators get SupoClip Pro (normally $${proPrice}/month) for free while they're in the program.`,
    },
    {
      question: "Is there a minimum number of followers?",
      answer: "No. We look at what you post, not just how many people follow you. Small creators who already make videos about SupoClip are a great fit.",
    },
    {
      question: "What does my audience get?",
      answer: "Anyone who signs up through your link or enters your code at checkout gets 20% off their first 3 months. On the web it works on any plan. On iPhone it applies to Pro: people tap “Have a creator code?” in the app or open your iPhone link.",
    },
    {
      question: "Can I pick my own code?",
      answer: "Yes. Codes are 3 to 20 letters, numbers or hyphens, and each one can only belong to one creator. The form tells you right away if yours is available.",
    },
    {
      question: "What happens if my application is declined?",
      answer: "You'll get an email, with a note from us when there's something specific. You can apply again after 30 days.",
    },
    {
      question: "Do I need a SupoClip account?",
      answer: "Yes. The program is tied to your account, so you'll create a free one (or sign in) before applying.",
    },
  ];
}

export default function AffiliatePage() {
  const siteUrl = getSiteUrl();
  const pro = getPublicBillingPlans().find((plan) => plan.id === "pro");
  const proPrice = pro?.priceMonthly ?? "10";
  const faqs = getFaqs(proPrice);
  const structuredData = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "BreadcrumbList",
        itemListElement: [
          { "@type": "ListItem", position: 1, name: "SupoClip", item: siteUrl },
          { "@type": "ListItem", position: 2, name: "Creator program", item: `${siteUrl}/affiliate` },
        ],
      },
      {
        "@type": "VideoObject",
        name: "How the SupoClip creator program works",
        description: "Apply, pick your code, and your audience gets 20% off their first 3 months. Approved creators get SupoClip Pro for free.",
        thumbnailUrl: `${siteUrl}${VIDEO.poster}`,
        contentUrl: `${siteUrl}${VIDEO.src}`,
        uploadDate: VIDEO.uploadDate,
        duration: `PT${VIDEO.durationSeconds}S`,
      },
      {
        "@type": "FAQPage",
        mainEntity: faqs.map((faq) => ({
          "@type": "Question",
          name: faq.question,
          acceptedAnswer: { "@type": "Answer", text: faq.answer },
        })),
      },
    ],
  };

  return (
    <main className="min-h-screen bg-background text-foreground">
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(structuredData).replace(/</g, "\\u003c") }} />
      <header className="border-b bg-background/95">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-6">
          <Link href="/" className="flex items-center gap-2.5">
            <Image src="/logo.png" alt="SupoClip" width={24} height={24} className="rounded-lg" />
            <span className="text-lg font-bold tracking-tight">SupoClip</span>
          </Link>
          <nav className="flex items-center gap-2" aria-label="Primary navigation">
            <Link href="/demo"><Button variant="ghost" size="sm">Demo</Button></Link>
            <Link href={APPLY_HREF}><Button size="sm">Apply</Button></Link>
          </nav>
        </div>
      </header>

      <section className="border-b bg-muted/35">
        <div className="mx-auto max-w-5xl px-6 py-14 md:py-20">
          <Badge variant="secondary">Creator program</Badge>
          <h1 className="mt-5 max-w-4xl text-4xl font-extrabold tracking-tight sm:text-5xl" style={{ fontFamily: "var(--font-syne), var(--font-geist-sans), system-ui" }}>
            Make videos about SupoClip. Get Pro for free.
          </h1>
          <p className="mt-6 max-w-3xl text-lg leading-8 text-muted-foreground">
            Posting Shorts, Reels or TikToks about clipping, podcasting or creator tools? Get your own code. Your audience saves on
            SupoClip, and you get Pro while you&apos;re in the program.
          </p>
          <div className="mt-8 flex flex-wrap items-center gap-3">
            <Link href={APPLY_HREF}><Button size="lg">Apply now <ArrowRight className="h-4 w-4" /></Button></Link>
            <span className="text-sm text-muted-foreground">No follower minimum.</span>
          </div>
          <video
            src={VIDEO.src}
            poster={VIDEO.poster}
            autoPlay
            muted
            loop
            playsInline
            controls
            preload="metadata"
            width={1920}
            height={1080}
            className="mt-12 aspect-video w-full rounded-2xl border bg-muted shadow-lg"
            aria-label="Creator program explainer: apply, pick your code, and your audience gets 20% off their first 3 months"
          />
        </div>
      </section>

      <div className="mx-auto max-w-5xl px-6 py-12 md:py-16">
        <section aria-labelledby="offer-heading">
          <h2 id="offer-heading" className="text-3xl font-bold tracking-tight">What everyone gets</h2>
          <div className="mt-8 grid gap-4 md:grid-cols-2">
            <div className="rounded-2xl border p-6">
              <Gift className="h-5 w-5" />
              <h3 className="mt-4 text-lg font-semibold">You get SupoClip Pro, free</h3>
              <p className="mt-2 text-sm leading-6 text-muted-foreground">
                Pro is normally ${proPrice}/month{pro ? ` and includes ${pro.generationLimit} videos a month` : ""}. Approved creators get it for
                free for as long as they&apos;re in the program.
              </p>
            </div>
            <div className="rounded-2xl border p-6">
              <Percent className="h-5 w-5" />
              <h3 className="mt-4 text-lg font-semibold">Your audience gets 20% off</h3>
              <p className="mt-2 text-sm leading-6 text-muted-foreground">
                For their first 3 months on any plan. It works through your link or by typing your code at checkout.
              </p>
            </div>
          </div>
        </section>

        <section className="mt-16" aria-labelledby="how-heading">
          <h2 id="how-heading" className="text-3xl font-bold tracking-tight">How it works</h2>
          <ol className="mt-8 grid gap-4 sm:grid-cols-3">
            {steps.map(({ icon: Icon, title: stepTitle, detail }, index) => (
              <li key={stepTitle} className="rounded-lg border p-5">
                <div className="flex items-center justify-between">
                  <Icon className="h-5 w-5" />
                  <span className="font-mono text-xs text-muted-foreground">0{index + 1}</span>
                </div>
                <h3 className="mt-4 font-semibold">{stepTitle}</h3>
                <p className="mt-1 text-sm leading-6 text-muted-foreground">{detail}</p>
              </li>
            ))}
          </ol>
        </section>

        <section className="mt-16" aria-labelledby="rules-heading">
          <h2 id="rules-heading" className="text-3xl font-bold tracking-tight">The rules</h2>
          <ul className="mt-6 list-disc space-y-2 pl-5 text-base leading-7 text-muted-foreground">
            {rules.map((rule) => <li key={rule}>{rule}</li>)}
          </ul>
        </section>

        <section className="mt-16" aria-labelledby="faq-heading">
          <h2 id="faq-heading" className="text-3xl font-bold tracking-tight">Questions</h2>
          <dl className="mt-8 divide-y rounded-2xl border">
            {faqs.map((faq) => (
              <div key={faq.question} className="p-5">
                <dt className="font-semibold">{faq.question}</dt>
                <dd className="mt-2 text-sm leading-6 text-muted-foreground">{faq.answer}</dd>
              </div>
            ))}
          </dl>
        </section>

        <section className="mt-16 rounded-2xl border bg-foreground p-6 text-background md:p-8">
          <h2 className="text-2xl font-bold tracking-tight">Already posting about SupoClip?</h2>
          <p className="mt-2 max-w-xl text-sm leading-6 text-background/70">
            Apply with a link to your profile and pick your code. If you&apos;ve made a video about SupoClip, include it.
          </p>
          <div className="mt-6">
            <Link href={APPLY_HREF}><Button variant="secondary">Apply now <ArrowRight className="h-4 w-4" /></Button></Link>
          </div>
        </section>
      </div>
      <SiteFooter />
    </main>
  );
}
