"use client";

import { useEffect, useState } from "react";
import { ArrowRight, Check, Loader2, Sparkles, X } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
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
        body: "Pick a plan and SupoClip gets to work: it finds the best moments, frames the speaker and adds captions for you.",
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
      <DialogContent showCloseButton={false} className="max-w-3xl gap-0 overflow-hidden p-0 sm:grid-cols-[1.1fr_1fr]">
        <DialogClose className="absolute right-4 top-4 z-10 rounded-full p-1.5 text-muted-foreground transition-colors hover:bg-muted sm:text-white/60 sm:hover:bg-white/10 sm:hover:text-white">
          <X className="size-4" />
          <span className="sr-only">Close</span>
        </DialogClose>
        <div className="flex flex-col p-6 sm:p-8">
          <span className="inline-flex w-fit items-center gap-1.5 rounded-full bg-brand-soft px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wider text-brand">
            <Sparkles className="size-3" />
            {copy.eyebrow}
          </span>
          <DialogTitle className="mt-4 font-display text-2xl font-bold leading-tight tracking-tight">{copy.title}</DialogTitle>
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

          <div className="mt-6 flex flex-col gap-2">
            {target && !state?.managedByAppStore && (
              <Button size="lg" onClick={handleContinue} disabled={isRedirecting} className="h-11 rounded-xl bg-brand text-brand-foreground hover:bg-brand/90">
                {isRedirecting ? <Loader2 className="size-4 animate-spin" /> : null}
                {isRedirecting ? "Opening secure checkout…" : `Continue with ${target.name}`}
                {!isRedirecting && <ArrowRight className="size-4" />}
              </Button>
            )}
            <Button variant="ghost" onClick={() => onOpenChange(false)} className="h-10 rounded-xl text-muted-foreground">
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
        "flex w-full items-center gap-3 rounded-xl border px-4 py-3 text-left transition-all",
        current && "cursor-default bg-muted/40 opacity-70",
        !current && "hover:border-foreground/30",
        selected && "border-brand bg-brand-soft/40 ring-1 ring-brand",
      )}
    >
      <span
        className={cn(
          "flex size-4 shrink-0 items-center justify-center rounded-full border",
          selected ? "border-brand bg-brand text-brand-foreground" : "border-muted-foreground/40",
          current && "invisible",
        )}
      >
        {selected && <Check className="size-2.5" strokeWidth={3} />}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2 text-sm font-semibold">
          {plan.name}
          {current && <span className="rounded-full bg-background px-2 py-0.5 text-[10px] font-medium text-muted-foreground">Current plan</span>}
          {recommended && <span className="rounded-full bg-brand px-2 py-0.5 text-[10px] font-medium text-brand-foreground">Popular</span>}
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

const VISUAL_CSS = `
@keyframes uv-pop { from { opacity: 0; transform: scale(0.3) } to { opacity: 1; transform: none } }
@keyframes uv-grow { from { width: 0 } }
.uv-pop { animation: uv-pop 360ms cubic-bezier(.2,.9,.3,1.3) both }
.uv-grow { animation: uv-grow 1.4s cubic-bezier(.3,.7,.2,1) 200ms both }
@media (prefers-reduced-motion: reduce) { .uv-pop, .uv-grow { animation: none } }
`;

/** The right-hand panel: a visual tailored to why the prompt opened, plus what the plan includes. */
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
    <div className="relative hidden flex-col overflow-hidden bg-stone-950 p-8 text-white sm:flex">
      <style>{VISUAL_CSS}</style>
      <div aria-hidden className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_20%_0%,oklch(0.45_0.12_40/0.5),transparent_60%),radial-gradient(ellipse_at_90%_100%,oklch(0.4_0.1_260/0.35),transparent_55%)]" />

      <div className="relative flex flex-1 flex-col justify-center" aria-hidden>
        {plan &&
          (reason.kind === "video_too_long" ? (
            <LengthRuler plan={plan} currentPlan={currentPlan} durationSeconds={reason.durationSeconds} maxSeconds={reason.maxSeconds} />
          ) : reason.kind === "custom_fonts" ? (
            <FontSpecimen />
          ) : (
            <CapacityGrid key={plan.id} plan={plan} currentPlan={currentPlan} />
          ))}
      </div>

      {plan && (
        <ul className="relative mt-8 space-y-2 border-t border-white/10 pt-5">
          {plan.highlights.map((highlight) => (
            <li key={highlight} className="flex items-start gap-2 text-sm text-white/80">
              <Check className="mt-0.5 size-3.5 shrink-0 text-brand" strokeWidth={3} />
              {highlight}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** One tile per video: what you have today in white, what the plan adds popping in. */
function CapacityGrid({ plan, currentPlan }: { plan: PublicBillingPlan; currentPlan: PublicBillingPlan | undefined }) {
  const total = plan.generationLimit;
  const existing = Math.min(currentPlan?.generationLimit ?? 0, total);
  // Small plans get clip-shaped tiles; big ones a denser grid that still fits the panel.
  const columns = total <= 60 ? 10 : 25;
  const step = Math.min(8, 1200 / Math.max(1, total - existing));
  return (
    <div>
      <p className="font-display text-5xl font-bold tracking-tight tabular-nums">{total}</p>
      <p className="mt-1 text-sm text-white/60">
        {existing ? `videos a month, up from ${existing} on ${currentPlan?.name}` : `videos a month with ${plan.name}`}
      </p>
      <div className={cn("mt-6 grid", total <= 60 ? "max-w-[15rem] gap-1" : "gap-[3px]")} style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}>
        {Array.from({ length: total }, (_, i) => (
          <span
            key={i}
            className={cn(
              total <= 60 ? "aspect-[9/16] rounded-[4px]" : "aspect-[3/4] rounded-[2px]",
              i < existing ? "bg-white/25" : "uv-pop bg-gradient-to-b from-brand to-brand/70",
            )}
            style={i < existing ? undefined : { animationDelay: `${Math.round((i - existing) * step)}ms` }}
          />
        ))}
      </div>
      {existing > 0 && (
        <div className="mt-3 flex gap-4 text-[11px] text-white/50">
          <span className="flex items-center gap-1.5"><span className="size-2 rounded-[2px] bg-white/30" />{currentPlan?.name} today</span>
          <span className="flex items-center gap-1.5"><span className="size-2 rounded-[2px] bg-brand" />Added with {plan.name}</span>
        </div>
      )}
    </div>
  );
}

/** The video's length on a ruler that runs to the bigger plan's limit, with today's limit marked. */
function LengthRuler({
  plan,
  currentPlan,
  durationSeconds,
  maxSeconds,
}: {
  plan: PublicBillingPlan;
  currentPlan: PublicBillingPlan | undefined;
  durationSeconds: number;
  maxSeconds: number;
}) {
  const span = Math.max(plan.youtubeMaxMinutes * 60, durationSeconds);
  const at = (seconds: number) => `${Math.min(100, (seconds / span) * 100)}%`;
  return (
    <div>
      <p className="font-display text-5xl font-bold tracking-tight">{formatRuntime(durationSeconds)}</p>
      <p className="mt-1 text-sm text-white/60">fits comfortably in {plan.name}</p>

      <div className="relative mt-10 pb-10 pt-7">
        <div className="relative h-3 rounded-full bg-white/10">
          <div className="absolute inset-y-0 left-0 rounded-full bg-white/15" style={{ width: at(maxSeconds) }} />
          <div className="uv-grow absolute inset-y-0 left-0 rounded-full bg-gradient-to-r from-brand/70 to-brand shadow-[0_0_18px_var(--color-brand)]" style={{ width: at(durationSeconds) }} />
        </div>

        <span className="absolute top-0 -translate-x-1/2 rounded-full bg-white px-2 py-0.5 text-[10px] font-semibold text-stone-950" style={{ left: at(durationSeconds) }}>
          Your video
        </span>
        <span className="absolute top-5 h-7 w-px bg-white/60" style={{ left: at(maxSeconds) }} />
        <span className="absolute bottom-2 -translate-x-1/2 whitespace-nowrap text-[11px] text-white/60" style={{ left: at(maxSeconds) }}>
          {currentPlan?.name ?? "Today"} · {formatMinutes(Math.round(maxSeconds / 60))}
        </span>
        <span className="absolute bottom-2 right-0 whitespace-nowrap text-[11px] font-medium text-brand">
          {plan.name} · {formatMinutes(plan.youtubeMaxMinutes)}
        </span>
      </div>
    </div>
  );
}

const SPECIMENS = [
  { label: "Your brand serif", style: { fontFamily: "Georgia, 'Times New Roman', serif", fontStyle: "italic", textTransform: "none" as const } },
  { label: "Your display face", style: { fontFamily: "var(--font-syne), var(--font-display), system-ui", fontWeight: 800 } },
  { label: "Your mono", style: { fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontWeight: 700 } },
];

/** A clip whose caption cycles through typefaces, standing in for the user's own fonts. */
function FontSpecimen() {
  const [active, setActive] = useState(0);
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const timer = window.setInterval(() => setActive((index) => (index + 1) % SPECIMENS.length), 1600);
    return () => window.clearInterval(timer);
  }, []);
  return (
    <div className="flex items-center justify-center gap-6">
      <div className="relative aspect-[9/16] w-36 overflow-hidden rounded-2xl bg-gradient-to-b from-stone-700 to-stone-900 shadow-2xl ring-1 ring-white/10">
        <span className="absolute bottom-0 left-1/2 h-16 w-28 -translate-x-1/2 rounded-t-full bg-black/30" />
        <span className="absolute bottom-[3.6rem] left-1/2 size-12 -translate-x-1/2 rounded-full bg-black/30" />
        <div className="absolute inset-x-2 bottom-[30%] h-10">
          {SPECIMENS.map((specimen, index) => (
            <span
              key={specimen.label}
              className={cn(
                "absolute inset-0 flex items-center justify-center text-center text-sm uppercase leading-tight text-yellow-300 transition-all duration-500 [text-shadow:0_2px_6px_rgb(0_0_0/0.7)]",
                index === active ? "translate-y-0 opacity-100" : "translate-y-1.5 opacity-0",
              )}
              style={specimen.style}
            >
              and that&apos;s why
            </span>
          ))}
        </div>
      </div>
      <div className="space-y-2">
        {SPECIMENS.map((specimen, index) => (
          <p
            key={specimen.label}
            className={cn(
              "rounded-lg px-3 py-2 text-xs ring-1 transition-colors duration-500",
              index === active ? "bg-white/10 text-white ring-brand" : "bg-white/5 text-white/60 ring-white/10",
            )}
          >
            <span className="block text-base text-white" style={{ ...specimen.style, textTransform: "none" }}>Aa</span>
            {specimen.label}
          </p>
        ))}
      </div>
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

/** A calm inline row (think "Get 5× more with Ultra") for places where a modal would be too much. */
export function UpgradeNudge({ title, description, action, onAction, loading, className }: UpgradeNudgeProps) {
  return (
    <div
      className={cn(
        "flex items-center gap-3 rounded-2xl border bg-gradient-to-r from-brand-soft/70 via-background to-background px-4 py-3",
        className,
      )}
    >
      <span className="flex size-8 shrink-0 items-center justify-center rounded-xl bg-brand text-brand-foreground shadow-sm">
        <Sparkles className="size-4" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold">{title}</p>
        {description && <p className="text-xs text-muted-foreground">{description}</p>}
      </div>
      {action && onAction && (
        <Button type="button" size="sm" onClick={onAction} disabled={loading} className="shrink-0 rounded-full bg-foreground text-background hover:bg-foreground/90">
          {loading && <Loader2 className="size-3.5 animate-spin" />}
          {action}
        </Button>
      )}
    </div>
  );
}
