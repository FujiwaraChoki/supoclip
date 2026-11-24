"use client";

import { useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { motion } from "motion/react";
import { ArrowRight, Check, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { BillingIntervalToggle, useBillingPlanText } from "@/components/billing/upgrade-prompt";
import { getPublicBillingPlans, planPrice, type BillingInterval, type BillingPlanId } from "@/lib/billing-plans";
import { trackEvent } from "@/lib/analytics";
import { requestUnlockWhenPaid } from "@/lib/onboarding";
import { startUpgrade } from "@/lib/start-upgrade";
import { cn } from "@/lib/utils";

/**
 * The plans, inside Agent's conversation. One is suggested for this person's video; checkout comes
 * back to `returnTo` (the preview project), where the rest of the clips are unlocked.
 */
export function PlanCard({ suggested, returnTo, more, previewTaskId, compact = false }: {
  suggested: BillingPlanId;
  returnTo: string;
  /** Locked clips waiting. */
  more?: number;
  /** The free preview this unlocks: asked for on the server before checkout. */
  previewTaskId?: string;
  compact?: boolean;
}) {
  const t = useTranslations("onboarding.plans");
  const text = useBillingPlanText();
  const plans = useMemo(() => getPublicBillingPlans(), []);
  const [selected, setSelected] = useState<BillingPlanId>(suggested);
  const [interval, setInterval] = useState<BillingInterval>("month");
  const [opening, setOpening] = useState(false);
  const chosen = plans.find((plan) => plan.id === selected) ?? plans[0];

  const go = async () => {
    if (!chosen) return;
    setOpening(true);
    trackEvent("onboarding_checkout_clicked", { plan: chosen.id, interval, suggested: chosen.id === suggested });
    try {
      if (previewTaskId) await requestUnlockWhenPaid(previewTaskId);
      await startUpgrade(chosen.id, "pebble_onboarding", interval, returnTo);
    } catch (error) {
      setOpening(false);
      toast.error(error instanceof Error ? error.message : t("checkoutFailed"));
    }
  };

  return (
    <div className={cn("rounded-2xl border bg-card shadow-[0_18px_50px_-30px_rgb(28_25_23/0.45)]", compact ? "p-3" : "p-4")}>
      <div className="flex items-center justify-between gap-2">
        <BillingIntervalToggle value={interval} onChange={setInterval} plan={chosen} className="text-xs" />
      </div>
      <div role="radiogroup" aria-label={t("label")} className="mt-3 space-y-2">
        {plans.map((plan) => {
          const on = plan.id === selected;
          const lines = [
            text.t("minutesEach", { minutes: text.formatMinutes(plan.minuteLimit), resolution: plan.resolution, length: text.formatMinutes(plan.youtubeMaxMinutes) }),
            plan.id === "scale" ? t("creatorExtra") : t("proExtra"),
          ];
          return (
            <motion.button
              key={plan.id}
              type="button"
              role="radio"
              aria-checked={on}
              onClick={() => setSelected(plan.id)}
              whileTap={{ scale: 0.99 }}
              className={cn(
                "relative flex w-full items-start gap-3 rounded-xl border px-3 py-2.5 text-left transition-colors",
                on ? "border-foreground ring-1 ring-foreground" : "hover:border-foreground/30",
              )}
            >
              <span className={cn("mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border", on ? "border-foreground bg-foreground text-background" : "border-muted-foreground/40")}>
                {on && <Check className="size-2.5" strokeWidth={3} />}
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex flex-wrap items-center gap-x-2 text-sm font-semibold">
                  {text.planName(plan.id)}
                  {plan.id === suggested && (
                    <span className="rounded-full border border-brand/25 bg-brand/5 px-1.5 py-px text-[10px] font-medium leading-4 text-brand">{t("suggested")}</span>
                  )}
                </span>
                {lines.map((line) => <span key={line} className="block text-[11.5px] leading-snug text-muted-foreground">{line}</span>)}
              </span>
              <span className="shrink-0 text-right text-sm font-semibold tabular-nums">
                ${planPrice(plan, interval)}
                <span className="text-[11px] font-normal text-muted-foreground">{interval === "year" ? text.t("perYear") : text.t("perMonth")}</span>
              </span>
            </motion.button>
          );
        })}
      </div>
      <button
        type="button"
        onClick={() => void go()}
        disabled={opening || !chosen}
        className="mt-3 flex h-10 w-full items-center justify-center gap-2 rounded-full bg-foreground text-sm font-medium text-background transition-opacity hover:opacity-90 disabled:opacity-60"
      >
        {opening ? <Loader2 className="size-4 animate-spin" /> : null}
        {more ? t("ctaUnlock", { count: more, plan: chosen ? text.planName(chosen.id) : "" }) : t("cta", { plan: chosen ? text.planName(chosen.id) : "" })}
        {!opening && <ArrowRight className="size-4" />}
      </button>
      <p className="mt-2 text-center text-[11px] text-muted-foreground">{text.t("cancelAnytime")}</p>
    </div>
  );
}
