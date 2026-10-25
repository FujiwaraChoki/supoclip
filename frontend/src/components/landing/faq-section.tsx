"use client";

import { useTranslations } from "next-intl";
import { Plus } from "lucide-react";

import { getLandingFaqs } from "@/lib/landing-faq";
import { Reveal, SectionHeading } from "./reveal";

export function FaqSection() {
  const t = useTranslations("marketing");
  const faqs = getLandingFaqs(t);

  return (
    <section id="faq" className="scroll-mt-24 border-t py-24 md:py-32">
      <div className="mx-auto grid max-w-6xl gap-12 px-6 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)] lg:gap-16">
        <div className="lg:sticky lg:top-28 lg:self-start">
          <SectionHeading eyebrow={t("faq.eyebrow")} title={t("faq.title")} body={t("faq.body")} align="left" />
        </div>

        <Reveal delay={0.05} className="min-w-0">
          <div className="divide-y border-y">
            {faqs.map((faq, i) => (
              <details key={faq.key} className="group" open={i === 0}>
                <summary className="flex cursor-pointer list-none items-center justify-between gap-6 py-5 text-left font-medium tracking-tight transition-colors hover:text-foreground/70 [&::-webkit-details-marker]:hidden">
                  {faq.question}
                  <span className="flex size-8 shrink-0 items-center justify-center rounded-full border transition-all duration-300 group-open:rotate-45 group-open:bg-foreground group-open:text-background">
                    <Plus className="size-4" />
                  </span>
                </summary>
                <p className="max-w-2xl pb-6 pr-12 text-sm leading-relaxed text-muted-foreground">{faq.answer}</p>
              </details>
            ))}
          </div>
        </Reveal>
      </div>
    </section>
  );
}
