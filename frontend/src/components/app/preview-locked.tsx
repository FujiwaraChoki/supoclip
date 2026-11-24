"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { motion } from "motion/react";
import { Loader2, Lock, Sparkles } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { trackEvent } from "@/lib/analytics";
import { formatRuntime, unlockPreview, type LockedMoment, type PreviewState } from "@/lib/onboarding";
import { emitPebble } from "@/lib/pebble-events";
import { getYouTubeThumbnailUrl } from "@/lib/video-upload";

/** A moment the free preview found but didn't make: its hook and score, the picture blurred. */
export function LockedMomentTile({ moment, sourceUrl, backdropVideo, index, onUnlock }: {
  moment: LockedMoment;
  sourceUrl?: string | null;
  /** The free clip's video, shown blurred behind uploads (they have no thumbnail). */
  backdropVideo?: string | null;
  index: number;
  onUnlock: () => void;
}) {
  const t = useTranslations("onboarding.preview");
  const thumbnail = sourceUrl ? getYouTubeThumbnailUrl(sourceUrl) : null;
  return (
    <motion.button
      type="button"
      onClick={onUnlock}
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.05 * index, type: "spring", stiffness: 240, damping: 24 }}
      className="group block w-full text-left focus-visible:outline-none"
      aria-label={t("lockedLabel", { hook: moment.hook_title ?? "" })}
    >
      <div className="relative aspect-[9/16] overflow-hidden rounded-xl bg-stone-900 ring-1 ring-black/5 transition-transform group-hover:-translate-y-0.5">
        {thumbnail && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={thumbnail} alt="" className="absolute inset-0 size-full scale-125 object-cover opacity-70 blur-xl" style={{ objectPosition: `${30 + ((index * 17) % 40)}% 50%` }} />
        )}
        {!thumbnail && backdropVideo && (
          <video
            src={`${backdropVideo}#t=${(2 + index * 5.5).toFixed(1)}`}
            muted
            playsInline
            preload="metadata"
            aria-hidden
            tabIndex={-1}
            className="absolute inset-0 size-full scale-125 object-cover opacity-75 blur-xl"
          />
        )}
        <div className="absolute inset-0 bg-gradient-to-b from-black/30 via-black/10 to-black/60" />
        {typeof moment.virality_score === "number" && (
          <span className="absolute left-2 top-2 rounded-md bg-black/55 px-1.5 py-0.5 text-[11px] font-semibold tabular-nums text-white backdrop-blur">{moment.virality_score}</span>
        )}
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 px-3 text-center">
          <span className="flex size-9 items-center justify-center rounded-full bg-white/15 text-white backdrop-blur-md transition-transform group-hover:scale-110"><Lock className="size-4" /></span>
          <span className="text-[11px] font-medium text-white/80">{t("lockedTag")}</span>
        </div>
        {moment.duration ? (
          <span className="absolute bottom-2 right-2 rounded bg-black/60 px-1 text-[10px] font-medium tabular-nums text-white">{formatRuntime(moment.duration)}</span>
        ) : null}
      </div>
      <p className="mt-2 line-clamp-2 text-sm font-medium leading-snug text-foreground/80">{moment.hook_title ?? t("untitled")}</p>
    </motion.button>
  );
}

/** Above a free preview's clips: what was found and how to get the rest. */
export function PreviewBanner({ preview, onUnlock, unlocking }: { preview: PreviewState; onUnlock: () => void; unlocking: boolean }) {
  const t = useTranslations("onboarding.preview");
  const found = preview.found ?? 1;
  const more = Math.max(0, found - 1);
  return (
    <div className="mb-6 flex flex-col gap-3 rounded-2xl border bg-gradient-to-r from-brand/[0.06] to-transparent p-4 sm:flex-row sm:items-center">
      <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-brand/10 text-brand"><Sparkles className="size-5" /></span>
      <div className="min-w-0 flex-1">
        <p className="font-semibold">{t("bannerTitle", { found })}</p>
        <p className="mt-0.5 text-sm text-muted-foreground">{more > 0 ? t("bannerBody", { count: more }) : t("bannerBodyOne")}</p>
      </div>
      {more > 0 && (
        <Button onClick={onUnlock} disabled={unlocking} className="rounded-full">
          {unlocking ? <Loader2 className="size-4 animate-spin" /> : <Lock className="size-4" />}
          {t("unlock", { count: found })}
        </Button>
      )}
    </div>
  );
}

/**
 * Back from checkout on a free preview: wait for the plan to land (Stripe's webhook can trail the
 * redirect), then unlock the rest in place. `paidReturn` is `?billing=success` on this page.
 */
export function usePreviewUnlock(taskId: string, locked: boolean, paidReturn: boolean, onUnlocked: () => void) {
  const t = useTranslations("onboarding.preview");
  const [unlocking, setUnlocking] = useState(false);
  const started = useRef(false);

  const unlock = async ({ retryUntilPaid }: { retryUntilPaid: boolean }) => {
    setUnlocking(true);
    for (let attempt = 0; attempt < (retryUntilPaid ? 16 : 1); attempt++) {
      try {
        await unlockPreview(taskId);
        trackEvent("preview_unlocked", { plan: "paid" });
        onUnlocked();
        setUnlocking(false);
        return true;
      } catch (error) {
        const status = (error as { status?: number }).status;
        if (status === 402 && retryUntilPaid) {
          await new Promise((resolve) => setTimeout(resolve, 1500));
          continue;
        }
        setUnlocking(false);
        if (status !== 402) toast.error(error instanceof Error ? error.message : t("unlockFailed"));
        return false;
      }
    }
    setUnlocking(false);
    toast.error(t("unlockWaiting"));
    return false;
  };

  useEffect(() => {
    if (!locked || !paidReturn || started.current) return;
    started.current = true;
    emitPebble("pebble:paid", { taskId });
    void unlock({ retryUntilPaid: true });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [locked, paidReturn, taskId]);

  return { unlocking, unlock };
}
