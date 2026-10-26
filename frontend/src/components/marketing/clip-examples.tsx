import Link from "next/link";
import { getTranslations } from "next-intl/server";

import { DEMO_CLIPS, DEMO_SOURCE, getDemoSourceMomentUrl } from "@/lib/demo";

/** Use real demo assets without loading video data until a visitor plays them. */
export async function ClipExamples({ locale }: { locale: string }) {
  const t = await getTranslations({ locale, namespace: "seo.proof" });
  return (
    <section aria-label={t("heading")} className="not-prose space-y-6 rounded-2xl border bg-muted/20 p-5 sm:p-7">
      <div className="space-y-2">
        <h2 className="!pt-0 text-2xl font-bold tracking-tight">{t("heading")}</h2>
        <p className="text-sm leading-7 text-muted-foreground">{t("intro")}</p>
        <a href={DEMO_SOURCE.url} className="text-sm font-medium underline underline-offset-4">{DEMO_SOURCE.title}</a>
      </div>
      <div className="grid gap-8 sm:grid-cols-2">
        {DEMO_CLIPS.map((clip) => (
          <figure key={clip.id} className="space-y-3">
            <video src={clip.src} poster={clip.poster} controls playsInline preload="none" width={540} height={960} aria-label={t("preview", { hook: clip.hook })} className="mx-auto aspect-[9/16] w-full max-w-[230px] rounded-xl bg-black" />
            <figcaption className="space-y-1 text-sm leading-6">
              <p className="font-semibold" lang="en">{clip.hook}</p>
              <p className="text-muted-foreground">{t("duration", { seconds: clip.previewSeconds, full: clip.duration })}</p>
              <a href={getDemoSourceMomentUrl(clip)} className="font-medium underline underline-offset-4">{t("source", { range: clip.range })}</a>
            </figcaption>
          </figure>
        ))}
      </div>
      <p className="text-sm leading-7 text-muted-foreground">{t("note")}</p>
      <Link href="/demo" className="inline-flex text-sm font-semibold underline underline-offset-4">{t("details")}</Link>
    </section>
  );
}
