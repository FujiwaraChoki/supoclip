import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { getLocale, getTranslations } from "next-intl/server";
import { Plus } from "lucide-react";

import { SiteFooter } from "@/components/marketing/site-footer";
import { SupportedLanguages } from "@/components/marketing/supported-languages";
import { buttonVariants } from "@/components/ui/button";
import { getLandingFaqs } from "@/lib/landing-faq";
import { getSupportedLanguageLabels, LANGUAGE_SUPPORT_SOURCE } from "@/lib/supported-languages";
import { getSiteUrl } from "@/lib/site";

const LANGUAGE_QUESTIONS = ["detection", "translation", "mixed", "accuracy", "interface"] as const;
const PROCESSING_QUESTIONS = ["fonts", "cleanup", "providers"] as const;

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("marketing.faqPage");
  return {
    title: t("metaTitle"),
    description: t("intro"),
    alternates: { canonical: `${getSiteUrl()}/faq` },
    openGraph: { title: t("metaTitle"), description: t("intro"), url: `${getSiteUrl()}/faq` },
  };
}

function Questions({ items }: { items: { key: string; question: string; answer: string }[] }) {
  return (
    <div className="mt-6 divide-y border-y">
      {items.map((item) => (
        <details id={item.key} key={item.key} className="group scroll-mt-24">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-6 py-5 font-medium [&::-webkit-details-marker]:hidden">
            {item.question}
            <Plus aria-hidden className="size-4 shrink-0 transition-transform group-open:rotate-45" />
          </summary>
          <p className="max-w-3xl pb-6 text-sm leading-7 text-muted-foreground">{item.answer}</p>
        </details>
      ))}
    </div>
  );
}

export default async function FaqPage() {
  const t = await getTranslations("marketing");
  const locale = await getLocale();
  const languages = getSupportedLanguageLabels(locale, t("faqPage.mandarin"));
  const questions = (keys: readonly string[]) => keys.map((key) => ({
    key,
    question: t(`faqPage.items.${key}.question`),
    answer: t(`faqPage.items.${key}.answer`),
  }));
  const languageQuestions = questions(LANGUAGE_QUESTIONS);
  const processingQuestions = questions(PROCESSING_QUESTIONS);
  const generalQuestions = getLandingFaqs(t).filter((item) => item.key !== "languages");
  const supportedQuestion = {
    question: t("faq.items.languages.question"),
    answer: `${t("faqPage.languageIntro")} ${languages.map((language) => language.label).join(", ")}.`,
  };
  const structuredData = {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: [supportedQuestion, ...languageQuestions, ...processingQuestions, ...generalQuestions].map((item) => ({
      "@type": "Question",
      name: item.question,
      acceptedAnswer: { "@type": "Answer", text: item.answer },
    })),
  };

  return (
    <main className="min-h-screen bg-background text-foreground">
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(structuredData).replace(/</g, "\\u003c") }} />
      <header className="border-b">
        <div className="mx-auto flex h-16 max-w-5xl items-center justify-between px-6">
          <Link href="/" className="flex items-center gap-2.5">
            <Image src="/logo.png" alt="SupoClip" width={24} height={24} className="rounded-lg" />
            <span className="font-display text-lg font-bold tracking-tight">SupoClip</span>
          </Link>
          <Link href="/sign-up" className={buttonVariants({ size: "sm" })}>{t("creator.start")}</Link>
        </div>
      </header>
      <section className="border-b bg-muted/35">
        <div className="mx-auto max-w-5xl px-6 py-16 md:py-20">
          <p className="text-sm font-medium text-muted-foreground">{t("faq.eyebrow")}</p>
          <h1 className="font-display mt-4 text-4xl font-bold tracking-tight sm:text-5xl">{t("faqPage.title")}</h1>
          <p className="mt-5 max-w-2xl text-base leading-8 text-muted-foreground">{t("faqPage.intro")}</p>
          <nav aria-label={t("faqPage.topics")} className="mt-8 flex flex-wrap gap-3 text-sm">
            {["languages", "processing", "general"].map((key) => (
              <a key={key} href={`#${key}`} className="rounded-full border bg-background px-4 py-2 hover:bg-muted">{t(`faqPage.sections.${key}`)}</a>
            ))}
          </nav>
        </div>
      </section>
      <div className="mx-auto max-w-5xl space-y-16 px-6 py-14 md:py-20">
        <section id="languages" aria-labelledby="languages-title" className="scroll-mt-8">
          <p className="text-sm font-medium text-muted-foreground">{t("faqPage.languageBadge")}</p>
          <h2 id="languages-title" className="font-display mt-3 text-3xl font-bold tracking-tight">{supportedQuestion.question}</h2>
          <p className="mt-4 max-w-3xl text-sm leading-7 text-muted-foreground">{t("faqPage.languageIntro")}</p>
          <SupportedLanguages languages={languages} />
          <p className="mt-4 text-xs leading-6 text-muted-foreground">{t("faqPage.sourceNote")}{" "}<a href={LANGUAGE_SUPPORT_SOURCE} target="_blank" rel="noopener noreferrer" className="underline underline-offset-4">{t("faqPage.sourceLink")}</a></p>
          <Questions items={languageQuestions} />
        </section>
        <section id="processing" aria-labelledby="processing-title" className="scroll-mt-8">
          <h2 id="processing-title" className="font-display text-3xl font-bold tracking-tight">{t("faqPage.sections.processing")}</h2>
          <Questions items={processingQuestions} />
          <Link href="/blog/self-host-supoclip-docker" className="mt-5 inline-block text-sm font-medium underline underline-offset-4">{t("pricing.selfHostGuide")}</Link>
        </section>
        <section id="general" aria-labelledby="general-title" className="scroll-mt-8">
          <h2 id="general-title" className="font-display text-3xl font-bold tracking-tight">{t("faqPage.sections.general")}</h2>
          <Questions items={generalQuestions} />
        </section>
      </div>
      <SiteFooter />
    </main>
  );
}
