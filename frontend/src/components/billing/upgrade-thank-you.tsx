"use client";

import { useEffect, useState, type ComponentType, type ReactNode } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { DistortionWaveProps } from "@/vendor/distortion-waves";

/** How long the plan card stays in view before the wave carries it away. */
const HOLD_MS = 700;

interface UpgradeThankYouProps {
  /** Run the wave. While false the card renders as it is, without the library. */
  active: boolean;
  planName: string;
  /** "10 hours", or null when the plan has no monthly cap. */
  minutes: string | null;
  /** "720p", or null when clips are not capped. */
  resolution: string | null;
  /** Called once the wave has carried the thank-you back into the card. */
  onClose: () => void;
  /** The plan card. */
  children: ReactNode;
}

/**
 * After checkout the plan card bends into a thank-you note on a distortion
 * wave, and back into the (now upgraded) card on "Done". The wave library
 * loads only when there is something to celebrate.
 */
export function UpgradeThankYou({ active, planName, minutes, resolution, onClose, children }: UpgradeThankYouProps) {
  const t = useTranslations("settings.thanks");
  const [Wave, setWave] = useState<ComponentType<DistortionWaveProps> | null>(null);
  const [revealed, setRevealed] = useState(false);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    void import("@/vendor/distortion-waves").then((mod) => {
      if (!cancelled) setWave(() => mod.DistortionWave);
    });
    return () => { cancelled = true; };
  }, [active]);

  useEffect(() => {
    if (!active || !Wave) return;
    const timer = window.setTimeout(() => setRevealed(true), HOLD_MS);
    return () => window.clearTimeout(timer);
  }, [active, Wave]);

  if (!active || !Wave) return <>{children}</>;

  return (
    <Wave
      revealed={revealed}
      palette="sunset"
      cornerRadius={12}
      prewarm
      onFinish={(shown) => { if (!shown) onClose(); }}
      className="rounded-xl bg-background"
      revealedContent={
        <div role="status" className="flex h-full flex-wrap items-center gap-x-4 gap-y-3 rounded-xl bg-muted/50 p-4">
          <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-brand text-brand-foreground">
            <Check className="size-5" strokeWidth={3} />
          </span>
          <div className="min-w-0 flex-1">
            <p className="font-semibold">{t("title")}</p>
            <p className="text-xs text-muted-foreground">
              {minutes && resolution ? t("body", { plan: planName, minutes, resolution }) : t("bodyShort", { plan: planName })}
            </p>
          </div>
          <div className="flex shrink-0 gap-2">
            <Button asChild size="sm"><Link href="/">{t("makeClip")}</Link></Button>
            <Button size="sm" variant="ghost" onClick={() => setRevealed(false)}>{t("done")}</Button>
          </div>
        </div>
      }
    >
      <div className="h-full">{children}</div>
    </Wave>
  );
}
