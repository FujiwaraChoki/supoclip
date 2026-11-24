"use client";

import { useRef } from "react";
import { useTranslations } from "next-intl";
import { motion, useInView } from "motion/react";
import { Wand2 } from "lucide-react";

import { FilmClip } from "@/components/agent/onboarding/film";

/**
 * Four clips SupoClip made from real podcast episodes, each in a different caption style, two with
 * dynamic edits, so a new account sees the range in one row. Files in `public/onboarding/examples/`.
 */
export const SHOWCASE_CLIPS = [
  { id: "hormozi", src: "/onboarding/examples/1.mp4", poster: "/onboarding/examples/1.jpg", score: 88, style: "hormozi", dynamic: false },
  // The kinetic clip (2.mp4) is held back until fx captions stop covering faces in a clip's opening.
  { id: "podcast", src: "/onboarding/examples/3.mp4", poster: "/onboarding/examples/3.jpg", score: 90, style: "podcast", dynamic: false },
  { id: "highlighter", src: "/onboarding/examples/4.mp4", poster: "/onboarding/examples/4.jpg", score: 92, style: "highlighter", dynamic: true },
] as const;

function ShowcaseTile({ clip, index }: { clip: (typeof SHOWCASE_CLIPS)[number]; index: number }) {
  const t = useTranslations("onboarding.showcase");
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { amount: 0.5 });
  return (
    <motion.div
      ref={ref}
      initial={{ opacity: 0, y: 16, scale: 0.94 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ delay: index * 0.08, type: "spring", stiffness: 240, damping: 22 }}
      className="w-40 shrink-0 snap-start"
    >
      <div className="relative aspect-[9/16] overflow-hidden rounded-2xl bg-stone-900 shadow-[0_14px_34px_-16px_rgb(28_25_23/0.55)]">
        <FilmClip src={clip.src} poster={clip.poster} playing={inView} className="absolute inset-0 size-full object-cover" />
        {clip.score > 0 && (
          <span className="absolute left-2 top-2 rounded-md bg-black/60 px-1.5 py-0.5 text-[11px] font-semibold tabular-nums text-white backdrop-blur">{clip.score}</span>
        )}
      </div>
      <p className="mt-2 line-clamp-2 text-xs font-medium leading-snug">{t(`clips.${clip.id}.hook`)}</p>
      <p className="mt-1 flex items-center gap-1 text-[11px] text-muted-foreground">
        {clip.dynamic && <Wand2 className="size-3 text-brand" />}
        {t(`styles.${clip.style}`)}{clip.dynamic ? ` · ${t("dynamic")}` : ""}
      </p>
    </motion.div>
  );
}

export function ShowcaseExamples({ inset = true }: { inset?: boolean }) {
  return (
    <div className={`-mx-1 flex snap-x gap-3 overflow-x-auto px-1 pb-2 [mask-image:linear-gradient(to_right,black_90%,transparent)] ${inset ? "sm:pl-12" : ""}`}>
      {SHOWCASE_CLIPS.map((clip, index) => <ShowcaseTile key={clip.id} clip={clip} index={index} />)}
    </div>
  );
}
