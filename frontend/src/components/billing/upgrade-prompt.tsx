"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, Check, Loader2, Server, X } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { CapacityIllustration, FontIllustration, LengthIllustration } from "@/components/billing/upgrade-illustrations";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import type { BillingSummary } from "@/hooks/use-billing-summary";
import {
  formatBillingPlanName,
  formatMinutes,
  getNextBillingPlan,
  getPublicBillingPlans,
  isPaidBillingPlan,
  type BillingPlanId,
  type PublicBillingPlan,
} from "@/lib/billing-plans";
import { track } from "@/lib/datafast";
import { startUpgrade } from "@/lib/start-upgrade";
import { cn } from "@/lib/utils";

export type UpgradeReason =
  | { kind: "start" }
  | { kind: "limit" }
  | { kind: "video_too_long"; durationSeconds: number; maxSeconds: number }
  | { kind: "custom_fonts" }
  | { kind: "explore" };

type UpgradeBilling = Pick<
  BillingSummary,
  "plan" | "subscription_status" | "subscription_provider" | "usage_count" | "usage_limit" | "remaining" | "period_end" | "upgrade_required"
>;

/**
 * Maps a backend error to the upgrade prompt that resolves it, or null when an
 * upgrade wouldn't help (e.g. a video longer than even the next plan allows).
 */
export function upgradeReasonForError(
  code: string | null | undefined,
  detail: Record<string, unknown> | null | undefined,
  nextPlanYoutubeMaxMinutes: number | undefined,
): UpgradeReason | null {
  if (code === "SUBSCRIPTION_REQUIRED") {
    const billing = detail?.billing as { plan?: string; subscription_status?: string } | undefined;
    const isPaid = isPaidBillingPlan(billing?.plan) && ["active", "trialing"].includes(billing?.subscription_status ?? "");
    return { kind: isPaid ? "limit" : "start" };
  }
  if (code === "VIDEO_TOO_LONG" && nextPlanYoutubeMaxMinutes) {
    const durationSeconds = Number(detail?.duration_seconds);
    const maxSeconds = Number(detail?.max_duration_seconds);
    if (Number.isFinite(durationSeconds) && Number.isFinite(maxSeconds) && durationSeconds <= nextPlanYoutubeMaxMinutes * 60) {
      return { kind: "video_too_long", durationSeconds, maxSeconds };
    }
  }
  return null;
}

/** Everything the prompts need to decide what (if anything) to offer. */
export function getUpgradeState(billing: UpgradeBilling | null | undefined) {
  if (!billing) return null;
  const isPaid = isPaidBillingPlan(billing.plan) && ["active", "trialing"].includes(billing.subscription_status);
  const nextPlan = getNextBillingPlan(isPaid ? billing.plan : "free");
  // App Store subscriptions can only change plans inside the iOS app.
  const managedByAppStore = isPaid && billing.subscription_provider === "apple";
  const limit = billing.usage_limit;
  const remaining = billing.remaining;
  const atLimit = isPaid && billing.upgrade_required;
  const nearLimit =
    isPaid && !atLimit && limit !== null && limit > 0 && remaining !== null && remaining <= Math.max(2, Math.ceil(limit * 0.2));

  return {
    isPaid,
    nextPlan: managedByAppStore ? null : nextPlan,
    managedByAppStore,
    atLimit,
    nearLimit,
    remaining,
    limit,
    resetsOn: billing.period_end ? formatDate(billing.period_end) : null,
  };
}

function formatDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleDateString(undefined, { month: "long", day: "numeric" });
}

function formatRuntime(seconds: number) {
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

function copyFor(reason: UpgradeReason, billing: UpgradeBilling | null | undefined, target: PublicBillingPlan | undefined) {
  const planName = formatBillingPlanName(billing?.plan);
  const state = getUpgradeState(billing);
  switch (reason.kind) {
    case "start":
      return {
        eyebrow: "Ready when you are",
        title: "Turn long videos into ready-to-post clips",
        body: "Choose a hosted plan and we handle the processing, or self-host the open-source app on your own computer for free.",
      };
    case "limit":
      return {
        eyebrow: "What a month",
        title: state?.limit ? `You've clipped all ${state.limit} videos on ${planName}` : `You've used everything on ${planName}`,
        body: target
          ? `Your allowance refreshes${state?.resetsOn ? ` on ${state.resetsOn}` : " next billing period"}. Keep the momentum going with ${target.name}: ${target.generationLimit} videos a month.`
          : `Your allowance refreshes${state?.resetsOn ? ` on ${state.resetsOn}` : " next billing period"}.`,
      };
    case "video_too_long":
      return {
        eyebrow: "A long one",
        title: `${target?.name ?? "A bigger plan"} can take this ${formatRuntime(reason.durationSeconds)} video`,
        body: `${planName} covers YouTube videos up to ${formatMinutes(Math.round(reason.maxSeconds / 60))}. ${target ? `${target.name} handles up to ${formatMinutes(target.youtubeMaxMinutes)}, so this one goes straight through.` : ""} This try didn't use a generation.`,
      };
    case "custom_fonts":
      return {
        eyebrow: "Make it yours",
        title: "Captions in your brand's own font",
        body: "Upload any .ttf or .otf and use it across every clip, so your shorts look like they come from you.",
      };
    default:
      return {
        eyebrow: "More room to create",
        title: target ? `Do more with ${target.name}` : "Plans for every creator",
        body: "More videos each month, longer sources and priority processing.",
      };
  }
}

interface UpgradeDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  reason: UpgradeReason;
  billing: UpgradeBilling | null | undefined;
  /** Where the prompt was shown, for analytics. */
  source: string;
}

export function UpgradeDialog({ open, onOpenChange, reason, billing, source }: UpgradeDialogProps) {
  const state = getUpgradeState(billing);
  const allPlans = getPublicBillingPlans();
  // Free users choose between every plan; subscribers see their plan next to the step up.
  const options: PublicBillingPlan[] = state?.isPaid ? (state.nextPlan ? [state.nextPlan] : []) : allPlans;
  const currentPlan = state?.isPaid ? allPlans.find((plan) => plan.id === billing?.plan) : undefined;
  const preferred = preferredPlan(reason, options);
  const [selected, setSelected] = useState<BillingPlanId | undefined>(preferred);
  const [isRedirecting, setIsRedirecting] = useState(false);

  useEffect(() => {
    if (open) {
      setSelected(preferred);
      track("upgrade_prompt_viewed", { reason: reason.kind, source, plan: billing?.plan ?? "free" });
    }
    // Only re-run when the dialog opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const target = options.find((plan) => plan.id === selected) ?? options[0];
  const copy = copyFor(reason, billing, target);

  const handleContinue = async () => {
    if (!target) return;
    setIsRedirecting(true);
    try {
      await startUpgrade(target.id, `${source}:${reason.kind}`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "We couldn't open checkout. Please try again.");
      setIsRedirecting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent showCloseButton={false} className="max-h-[calc(100dvh-2rem)] max-w-3xl gap-0 overflow-x-hidden overflow-y-auto p-0 sm:grid-cols-[1.1fr_1fr]">
        <DialogClose className="absolute right-4 top-4 z-10 rounded-md p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground">
          <X className="size-4" />
          <span className="sr-only">Close</span>
        </DialogClose>
        <div className="flex flex-col p-6 sm:p-8">
          <p className="text-xs font-medium text-muted-foreground">{copy.eyebrow}</p>
          <DialogTitle className="mt-2 font-display text-2xl font-bold leading-tight tracking-tight">{copy.title}</DialogTitle>
          <DialogDescription className="mt-2 text-sm leading-relaxed text-muted-foreground">{copy.body}</DialogDescription>

          {state?.managedByAppStore ? (
            <p className="mt-6 rounded-xl border bg-muted/40 px-4 py-3 text-sm text-muted-foreground">
              Your subscription is managed through the App Store. You can change plans in the SupoClip iOS app.
            </p>
          ) : (
            <div className="mt-6 space-y-2" role="radiogroup" aria-label="Plans">
              {currentPlan && <PlanOption plan={currentPlan} current />}
              {options.map((plan) => (
                <PlanOption
                  key={plan.id}
                  plan={plan}
                  selected={plan.id === target?.id}
                  recommended={options.length > 1 && plan.id === preferred}
                  onSelect={() => setSelected(plan.id)}
                />
              ))}
            </div>
          )}

          {!state?.isPaid && (
            <div className="mt-3 rounded-lg border bg-muted/30 px-4 py-3">
              <div className="flex items-center gap-3">
                <Server className="size-4 shrink-0 text-muted-foreground" />
                <p className="flex-1 text-sm font-semibold">Self-hosted</p>
                <span className="text-sm font-semibold">Free</span>
              </div>
              <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
                Run SupoClip on your own computer with no subscription or monthly video limit.
                Setup required; you provide the hardware and any paid AI services you choose.
              </p>
              <Link
                href="/blog/self-host-supoclip-docker"
                className="mt-3 inline-flex items-center gap-1 text-sm font-medium underline underline-offset-4 hover:text-muted-foreground"
              >
                Self-hosting guide <ArrowRight className="size-3.5" />
              </Link>
            </div>
          )}

          <div className="mt-6 flex flex-col gap-2">
            {target && !state?.managedByAppStore && (
              <Button size="lg" onClick={handleContinue} disabled={isRedirecting}>
                {isRedirecting ? <Loader2 className="size-4 animate-spin" /> : null}
                {isRedirecting ? "Opening secure checkout…" : `Continue with ${target.name}`}
                {!isRedirecting && <ArrowRight className="size-4" />}
              </Button>
            )}
            <Button variant="ghost" onClick={() => onOpenChange(false)} className="text-muted-foreground">
              Maybe later
            </Button>
          </div>
          {target && !state?.managedByAppStore && (
            <p className="mt-3 text-center text-[11px] text-muted-foreground">
              {state?.isPaid
                ? "You'll confirm the change on Stripe and only pay the prorated difference."
                : "Cancel anytime · Secure checkout with Stripe"}
            </p>
          )}
        </div>

        <PlanShowcase reason={reason} plan={target ?? currentPlan} currentPlan={currentPlan} />
      </DialogContent>
    </Dialog>
  );
}

function preferredPlan(reason: UpgradeReason, options: PublicBillingPlan[]): BillingPlanId | undefined {
  if (reason.kind === "video_too_long") {
    const fits = options.find((plan) => plan.youtubeMaxMinutes * 60 >= reason.durationSeconds);
    if (fits) return fits.id;
  }
  return (options.find((plan) => plan.highlighted) ?? options[0])?.id;
}

function PlanOption({
  plan,
  current,
  selected,
  recommended,
  onSelect,
}: {
  plan: PublicBillingPlan;
  current?: boolean;
  selected?: boolean;
  recommended?: boolean;
  onSelect?: () => void;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={Boolean(selected)}
      disabled={current}
      onClick={onSelect}
      className={cn(
        "flex w-full items-center gap-3 rounded-lg border px-4 py-3 text-left transition-colors",
        current && "cursor-default bg-muted/40 text-muted-foreground",
        !current && "hover:border-foreground/30",
        selected && "border-foreground ring-1 ring-foreground",
      )}
    >
      <span
        className={cn(
          "flex size-4 shrink-0 items-center justify-center rounded-full border",
          selected ? "border-foreground bg-foreground text-background" : "border-muted-foreground/40",
          current && "invisible",
        )}
      >
        {selected && <Check className="size-2.5" strokeWidth={3} />}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2 text-sm font-semibold">
          {plan.name}
          {current && <span className="text-xs font-normal text-muted-foreground">Current plan</span>}
          {recommended && <span className="text-xs font-normal text-muted-foreground">Most popular</span>}
        </span>
        <span className="block text-xs text-muted-foreground">
          {plan.generationLimit} videos/mo · up to {formatMinutes(plan.youtubeMaxMinutes)} each
        </span>
      </span>
      <span className="text-sm font-semibold tabular-nums">
        ${plan.priceMonthly}
        <span className="text-xs font-normal text-muted-foreground">/mo</span>
      </span>
    </button>
  );
}

/** The right-hand panel: an illustration for why the prompt opened, plus what the plan includes. */
function PlanShowcase({
  reason,
  plan,
  currentPlan,
}: {
  reason: UpgradeReason;
  plan: PublicBillingPlan | undefined;
  currentPlan: PublicBillingPlan | undefined;
}) {
  return (
    <div className="hidden flex-col border-l bg-muted/50 p-8 sm:flex">
      <div className="flex flex-1 flex-col justify-center">
        {plan &&
          (reason.kind === "video_too_long" ? (
            <LengthIllustration
              durationSeconds={reason.durationSeconds}
              currentLimitSeconds={reason.maxSeconds}
              nextLimitSeconds={plan.youtubeMaxMinutes * 60}
              durationLabel={formatRuntime(reason.durationSeconds)}
              currentLabel={`${currentPlan?.name ?? "Today"} · ${formatMinutes(Math.round(reason.maxSeconds / 60))}`}
              nextLabel={`${plan.name} · ${formatMinutes(plan.youtubeMaxMinutes)}`}
            />
          ) : reason.kind === "custom_fonts" ? (
            <FontIllustration />
          ) : (
            <CapacityIllustration
              key={plan.id}
              from={currentPlan?.generationLimit ?? 0}
              to={plan.generationLimit}
              label={currentPlan ? `videos a month, up from ${currentPlan.generationLimit}` : `videos a month with ${plan.name}`}
            />
          ))}
      </div>

      {plan && (
        <ul className="mt-8 space-y-2 border-t pt-5">
          {plan.highlights.map((highlight) => (
            <li key={highlight} className="flex items-start gap-2 text-sm">
              <Check className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
              {highlight}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

interface UpgradeNudgeProps {
  title: string;
  description?: string;
  action?: string;
  onAction?: () => void;
  loading?: boolean;
  className?: string;
}

/** A quiet inline row for places where a modal would be too much. */
export function UpgradeNudge({ title, description, action, onAction, loading, className }: UpgradeNudgeProps) {
  return (
    <div className={cn("flex items-center gap-4 rounded-lg border px-4 py-3", className)}>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{title}</p>
        {description && <p className="text-xs text-muted-foreground">{description}</p>}
      </div>
      {action && onAction && (
        <Button type="button" size="sm" variant="outline" onClick={onAction} disabled={loading} className="shrink-0">
          {loading && <Loader2 className="size-3.5 animate-spin" />}
          {action}
        </Button>
      )}
    </div>
  );
}
