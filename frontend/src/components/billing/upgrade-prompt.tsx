"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { ArrowRight, Check, Clock, Loader2, Server, X } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { CreatorCodeField } from "@/components/billing/creator-code-field";
import { CapacityIllustration, FontIllustration, LengthIllustration } from "@/components/billing/upgrade-illustrations";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import type { BillingSummary } from "@/hooks/use-billing-summary";
import {
  annualMonthlyEquivalent,
  annualMonthsFree,
  billingPlanMessageKey,
  getNextBillingPlan,
  getPublicBillingPlans,
  isPaidBillingPlan,
  planPrice,
  socialPublishingStage,
  type BillingInterval,
  type BillingPlanId,
  type PublicBillingPlan,
} from "@/lib/billing-plans";
import { track } from "@/lib/datafast";
import { startUpgrade } from "@/lib/start-upgrade";
import { cn } from "@/lib/utils";

export type UpgradeReason =
  | { kind: "start" }
  | { kind: "limit"; requiredMinutes?: number; remainingMinutes?: number }
  | { kind: "video_too_long"; durationSeconds: number; maxSeconds: number }
  | { kind: "custom_fonts" }
  | { kind: "explore" };

type UpgradeBilling = Pick<
  BillingSummary,
  | "plan"
  | "subscription_status"
  | "subscription_provider"
  | "usage_count"
  | "minute_limit"
  | "remaining_minutes"
  | "period_end"
  | "upgrade_required"
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
    const billing = detail?.billing as
      | { plan?: string; subscription_status?: string; required_minutes?: number; remaining_minutes?: number | null }
      | undefined;
    const isPaid = isPaidBillingPlan(billing?.plan) && ["active", "trialing"].includes(billing?.subscription_status ?? "");
    if (!isPaid) return { kind: "start" };
    // The video is longer than the minutes left this month.
    if (typeof billing?.required_minutes === "number" && typeof billing.remaining_minutes === "number") {
      return { kind: "limit", requiredMinutes: billing.required_minutes, remainingMinutes: billing.remaining_minutes };
    }
    return { kind: "limit" };
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
  // Allowances are minutes of source video per month.
  const limit = billing.minute_limit ?? null;
  const remaining = billing.remaining_minutes ?? null;
  const atLimit = isPaid && billing.upgrade_required;
  const nearLimit =
    isPaid && !atLimit && limit !== null && limit > 0 && remaining !== null && remaining <= Math.ceil(limit * 0.2);

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

export function useBillingPlanText() {
  const t = useTranslations("billing");
  const common = useTranslations("common");
  const formatMinutes = (minutes: number) =>
    minutes >= 60 && minutes % 60 === 0
      ? common("duration.hours", { count: minutes / 60 })
      : common("duration.minutes", { count: minutes });
  const formatRuntime = (seconds: number) => {
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return t("runtime.minutes", { count: minutes });
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;
    return rest ? t("runtime.hoursMinutes", { hours, minutes: rest }) : t("runtime.hours", { count: hours });
  };
  const planName = (plan: string | null | undefined) => common(`plans.${billingPlanMessageKey(plan)}`);
  const highlights = (plan: PublicBillingPlan) => [
    t("highlights.minutes", { length: formatMinutes(plan.minuteLimit) }),
    plan.resolution === "1080p" ? t("highlights.fullHd") : t("highlights.hd"),
    t("highlights.youtube", { length: formatMinutes(plan.youtubeMaxMinutes) }),
    ...(plan.scheduling && socialPublishingStage() === "live" ? [t("highlights.scheduling")] : []),
    plan.id === "pro" ? t("highlights.fonts") : t("highlights.priority"),
  ];
  // Shown apart from the highlights with a "Coming soon" tag until publishing launches.
  const comingSoon = (plan: PublicBillingPlan) =>
    plan.scheduling && socialPublishingStage() === "soon" ? t("highlights.scheduling") : null;
  return { t, common, formatMinutes, formatRuntime, planName, highlights, comingSoon };
}

function copyFor(
  reason: UpgradeReason,
  billing: UpgradeBilling | null | undefined,
  target: PublicBillingPlan | undefined,
  text: ReturnType<typeof useBillingPlanText>,
) {
  const planName = text.planName(billing?.plan);
  const targetName = target ? text.planName(target.id) : undefined;
  const state = getUpgradeState(billing);
  const t = text.t;
  switch (reason.kind) {
    case "start":
      return {
        eyebrow: t("upgrade.start.eyebrow"),
        title: t("upgrade.start.title"),
        body: t("upgrade.start.body"),
      };
    case "limit":
      return {
        eyebrow: t("upgrade.limit.eyebrow"),
        title:
          reason.requiredMinutes !== undefined && reason.remainingMinutes !== undefined
            ? t("upgrade.limit.titleShort", { required: reason.requiredMinutes, remaining: reason.remainingMinutes })
            : state?.limit
              ? t("upgrade.limit.titleMinutes", { length: text.formatMinutes(state.limit), plan: planName })
              : t("upgrade.limit.titleAll", { plan: planName }),
        body: targetName
          ? t(state?.resetsOn ? "upgrade.limit.bodyRefreshKeep" : "upgrade.limit.bodyPeriodKeep", {
              date: state?.resetsOn ?? "",
              plan: targetName,
              length: text.formatMinutes(target?.minuteLimit ?? 0),
            })
          : t(state?.resetsOn ? "upgrade.limit.bodyRefresh" : "upgrade.limit.bodyPeriod", {
              date: state?.resetsOn ?? "",
            }),
      };
    case "video_too_long":
      return {
        eyebrow: t("upgrade.long.eyebrow"),
        title: t("upgrade.long.title", {
          plan: targetName ?? t("upgrade.long.fallbackPlan"),
          runtime: text.formatRuntime(reason.durationSeconds),
        }),
        body: target
          ? t("upgrade.long.body", {
              plan: planName,
              length: text.formatMinutes(Math.round(reason.maxSeconds / 60)),
              next: targetName ?? "",
              nextLength: text.formatMinutes(target.youtubeMaxMinutes),
            })
          : t("upgrade.long.bodyNoNext", {
              plan: planName,
              length: text.formatMinutes(Math.round(reason.maxSeconds / 60)),
            }),
      };
    case "custom_fonts":
      return {
        eyebrow: t("upgrade.fonts.eyebrow"),
        title: t("upgrade.fonts.title"),
        body: t("upgrade.fonts.body"),
      };
    default:
      return {
        eyebrow: t("upgrade.explore.eyebrow"),
        title: targetName ? t("upgrade.explore.title", { plan: targetName }) : t("upgrade.explore.titleFallback"),
        body: t("upgrade.explore.body"),
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
  const text = useBillingPlanText();
  const state = getUpgradeState(billing);
  const allPlans = getPublicBillingPlans();
  // Free users choose between every plan; subscribers see their plan next to the step up.
  const options: PublicBillingPlan[] = state?.isPaid ? (state.nextPlan ? [state.nextPlan] : []) : allPlans;
  const currentPlan = state?.isPaid ? allPlans.find((plan) => plan.id === billing?.plan) : undefined;
  const preferred = preferredPlan(reason, options);
  const [selected, setSelected] = useState<BillingPlanId | undefined>(preferred);
  const [billingInterval, setBillingInterval] = useState<BillingInterval>("month");
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
  const copy = copyFor(reason, billing, target, text);

  const handleContinue = async () => {
    if (!target) return;
    setIsRedirecting(true);
    try {
      await startUpgrade(target.id, `${source}:${reason.kind}`, billingInterval);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : text.t("checkoutError"));
      setIsRedirecting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent showCloseButton={false} className="max-h-[calc(100dvh-2rem)] max-w-3xl gap-0 overflow-x-hidden overflow-y-auto p-0 sm:grid-cols-[1.1fr_1fr]">
        <DialogClose className="absolute right-4 top-4 z-10 rounded-md p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground">
          <X className="size-4" />
          <span className="sr-only">{text.common("close")}</span>
        </DialogClose>
        <div className="flex flex-col p-6 sm:p-8">
          <p className="text-xs font-medium text-muted-foreground">{copy.eyebrow}</p>
          <DialogTitle className="mt-2 font-display text-2xl font-bold leading-tight tracking-tight">{copy.title}</DialogTitle>
          <DialogDescription className="mt-2 text-sm leading-relaxed text-muted-foreground">{copy.body}</DialogDescription>

          {state?.managedByAppStore ? (
            <p className="mt-6 rounded-xl border bg-muted/40 px-4 py-3 text-sm text-muted-foreground">
              {text.t("appStore")}
            </p>
          ) : (
            <>
              {options.length > 0 && (
                <BillingIntervalToggle className="mt-6 self-start" value={billingInterval} onChange={setBillingInterval} plan={options[0]} />
              )}
              <div className="mt-3 space-y-2" role="radiogroup" aria-label={text.t("plansLabel")}>
                {currentPlan && <PlanOption plan={currentPlan} interval={billingInterval} current />}
                {options.map((plan) => (
                  <PlanOption
                    key={plan.id}
                    plan={plan}
                    interval={billingInterval}
                    selected={plan.id === target?.id}
                    recommended={options.length > 1 && plan.id === preferred}
                    onSelect={() => setSelected(plan.id)}
                  />
                ))}
              </div>
            </>
          )}

          {!state?.isPaid && (
            <div className="mt-3 rounded-lg border bg-muted/30 px-4 py-3">
              <div className="flex items-center gap-3">
                <Server className="size-4 shrink-0 text-muted-foreground" />
                <p className="flex-1 text-sm font-semibold">{text.t("selfHosted.title")}</p>
                <span className="text-sm font-semibold">{text.common("plans.free")}</span>
              </div>
              <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{text.t("selfHosted.body")}</p>
              <Link
                href="/blog/self-host-supoclip-docker"
                className="mt-3 inline-flex items-center gap-1 text-sm font-medium underline underline-offset-4 hover:text-muted-foreground"
              >
                {text.t("selfHosted.guide")} <ArrowRight className="size-3.5" />
              </Link>
            </div>
          )}

          {target && !state?.isPaid && <CreatorCodeField className="mt-4 self-start" />}

          <div className="mt-6 flex flex-col gap-2">
            {target && !state?.managedByAppStore && (
              <Button size="lg" onClick={handleContinue} disabled={isRedirecting}>
                {isRedirecting ? <Loader2 className="size-4 animate-spin" /> : null}
                {isRedirecting ? text.t("openingCheckout") : text.t("continueWith", { name: text.planName(target.id) })}
                {!isRedirecting && <ArrowRight className="size-4" />}
              </Button>
            )}
            <Button variant="ghost" onClick={() => onOpenChange(false)} className="text-muted-foreground">
              {text.t("maybeLater")}
            </Button>
          </div>
          {target && !state?.managedByAppStore && (
            <p className="mt-3 text-center text-[11px] text-muted-foreground">
              {state?.isPaid ? text.t("prorated") : text.t("cancelAnytime")}
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

/** Monthly / yearly switch; the yearly label shows what it saves. */
export function BillingIntervalToggle({
  value,
  onChange,
  plan,
  className,
}: {
  value: BillingInterval;
  onChange: (interval: BillingInterval) => void;
  /** Used to state the yearly saving. */
  plan?: PublicBillingPlan;
  className?: string;
}) {
  const text = useBillingPlanText();
  const monthsFree = plan ? annualMonthsFree(plan) : 0;
  return (
    <div role="radiogroup" aria-label={text.t("interval.label")} className={cn("inline-flex rounded-lg border p-0.5 text-sm", className)}>
      {(["month", "year"] as const).map((option) => (
        <button
          key={option}
          type="button"
          role="radio"
          aria-checked={value === option}
          onClick={() => onChange(option)}
          className={cn(
            "rounded-md px-3 py-1 font-medium transition-colors",
            value === option ? "bg-foreground text-background" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {option === "month" ? text.t("interval.monthly") : text.t("interval.yearly")}
          {option === "year" && monthsFree > 0 && (
            <span className={cn("ml-1.5 text-xs font-normal", value === option ? "text-background/70" : "text-muted-foreground")}>
              {text.t("interval.monthsFree", { count: monthsFree })}
            </span>
          )}
        </button>
      ))}
    </div>
  );
}

function PlanOption({
  plan,
  interval,
  current,
  selected,
  recommended,
  onSelect,
}: {
  plan: PublicBillingPlan;
  interval: BillingInterval;
  current?: boolean;
  selected?: boolean;
  recommended?: boolean;
  onSelect?: () => void;
}) {
  const text = useBillingPlanText();
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
          {text.planName(plan.id)}
          {current && <span className="text-xs font-normal text-muted-foreground">{text.t("currentPlan")}</span>}
          {recommended && <span className="text-xs font-normal text-muted-foreground">{text.t("mostPopular")}</span>}
        </span>
        <span className="block text-xs text-muted-foreground">
          {text.t("minutesEach", {
            minutes: text.formatMinutes(plan.minuteLimit),
            resolution: plan.resolution,
            length: text.formatMinutes(plan.youtubeMaxMinutes),
          })}
        </span>
      </span>
      <span className="text-right text-sm font-semibold tabular-nums">
        ${planPrice(plan, interval)}
        <span className="text-xs font-normal text-muted-foreground">{interval === "year" ? text.t("perYear") : text.t("perMonth")}</span>
        {interval === "year" && (
          <span className="block text-[11px] font-normal text-muted-foreground">
            {text.t("monthlyEquivalent", { price: annualMonthlyEquivalent(plan) })}
          </span>
        )}
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
  const text = useBillingPlanText();
  return (
    <div className="hidden flex-col border-l bg-muted/50 p-8 sm:flex">
      <div className="flex flex-1 flex-col justify-center">
        {plan &&
          (reason.kind === "video_too_long" ? (
            <LengthIllustration
              durationSeconds={reason.durationSeconds}
              currentLimitSeconds={reason.maxSeconds}
              nextLimitSeconds={plan.youtubeMaxMinutes * 60}
              durationLabel={text.formatRuntime(reason.durationSeconds)}
              currentLabel={`${currentPlan ? text.planName(currentPlan.id) : text.t("illustration.today")} · ${text.formatMinutes(Math.round(reason.maxSeconds / 60))}`}
              nextLabel={`${text.planName(plan.id)} · ${text.formatMinutes(plan.youtubeMaxMinutes)}`}
            />
          ) : reason.kind === "custom_fonts" ? (
            <FontIllustration />
          ) : (
            <CapacityIllustration
              key={plan.id}
              from={currentPlan ? currentPlan.minuteLimit / 60 : 0}
              to={plan.minuteLimit / 60}
              label={
                currentPlan
                  ? text.t("illustration.hoursUpFrom", { count: currentPlan.minuteLimit / 60 })
                  : text.t("illustration.hoursWith", { plan: text.planName(plan.id) })
              }
            />
          ))}
      </div>

      {plan && (
        <ul className="mt-8 space-y-2 border-t pt-5">
          {text.highlights(plan).map((highlight) => (
            <li key={highlight} className="flex items-start gap-2 text-sm">
              <Check className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
              {highlight}
            </li>
          ))}
          {text.comingSoon(plan) && (
            <li className="flex items-start gap-2 text-sm text-muted-foreground">
              <Clock className="mt-0.5 size-3.5 shrink-0" />
              <span>
                {text.comingSoon(plan)} <ComingSoonTag />
              </span>
            </li>
          )}
        </ul>
      )}
    </div>
  );
}

/** Small muted tag for features that are announced but not live yet. */
export function ComingSoonTag({ className }: { className?: string }) {
  const text = useBillingPlanText();
  return (
    <span className={cn("ml-1 inline-block rounded-full border px-1.5 py-px align-middle text-[10px] font-medium leading-4", className)}>
      {text.t("comingSoon")}
    </span>
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
