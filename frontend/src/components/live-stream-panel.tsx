"use client";

import { useId } from "react";
import { useTranslations } from "next-intl";
import { Check, Loader2 } from "lucide-react";

import { useBillingPlanText } from "@/components/billing/upgrade-prompt";
import { VideoSourceIcon } from "@/components/video-source-icon";
import { Button } from "@/components/ui/button";
import { liveMinutesNow, liveWindowOptions, type LiveChoice, type LiveOffer } from "@/lib/live-clipping";
import { cn } from "@/lib/utils";

/** `1:36:12` / `24:05`: how long the stream has been live. */
export function streamClock(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = String(total % 60).padStart(2, "0");
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}` : `${minutes}:${rest}`;
}

/** The small red "Live" mark, the same shape the platforms use. */
export function LivePill({ label, className }: { label: string; className?: string }) {
  return (
    <span className={cn("inline-flex shrink-0 items-center gap-1 rounded-[5px] bg-red-600 px-1.5 py-px text-[11px] font-semibold leading-4 text-white", className)}>
      <span aria-hidden className="relative flex size-1.5">
        <span className="absolute inline-flex size-full animate-ping rounded-full bg-white opacity-70 motion-reduce:animate-none" />
        <span className="relative inline-flex size-1.5 rounded-full bg-white" />
      </span>
      {label}
    </span>
  );
}

/**
 * Shown when the pasted link is a stream that is live right now: clip its newest part once,
 * or keep clipping until it ends. Accounts without Creator see the same choices, inert,
 * with a quiet line about the plan.
 */
export function LiveStreamPanel({ ref, offer, choice, onChoiceChange, onSubmit, onCancel, onUpgrade, busy }: {
  ref?: React.Ref<HTMLElement>;
  offer: LiveOffer;
  choice: LiveChoice;
  onChoiceChange: (choice: LiveChoice) => void;
  onSubmit: () => void;
  onCancel: () => void;
  onUpgrade: () => void;
  busy: boolean;
}) {
  const t = useTranslations("home.livePanel");
  const plans = useBillingPlanText();
  const headingId = useId();
  const options = liveWindowOptions(offer);
  const locked = !offer.canClipLive;
  const disabled = busy || locked;
  const channel = offer.channel || t(`platforms.${offer.platform}`);
  const interval = plans.formatRuntime(offer.followIntervalSeconds);
  const selected = options.find((option) => option.seconds === choice.windowSeconds);
  const windowLabel = selected?.wholeStream ? t("wholeStreamShort") : plans.formatRuntime(choice.windowSeconds);
  const submitLabel = choice.follow
    ? t("submitFollow")
    : selected?.wholeStream ? t("submitAll") : t("submitOnce", { duration: windowLabel });

  return (
    <section
      ref={ref}
      aria-labelledby={headingId}
      data-testid="live-stream-panel"
      data-tour="live-panel"
      className="mt-4 overflow-hidden rounded-2xl border bg-background shadow-sm"
    >
      <header className="flex items-center gap-3 p-4 sm:px-5">
        <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-muted">
          <VideoSourceIcon source={offer.platform} className="size-5" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-2">
            <h2 id={headingId} className="truncate font-display text-base font-bold tracking-tight">{channel}</h2>
            <LivePill label={t("live")} />
          </div>
          <p className="truncate text-sm text-muted-foreground">{offer.title ?? t("noTitle")}</p>
        </div>
        {offer.elapsedSeconds > 0 && (
          <div className="shrink-0 text-right">
            <p className="text-sm font-semibold tabular-nums">{streamClock(offer.elapsedSeconds)}</p>
            <p className="text-xs text-muted-foreground">{t("elapsed")}</p>
          </div>
        )}
      </header>

      <div className="border-t p-4 sm:px-5">
        {options.length > 0 ? (
          <>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-sm font-medium">{t("windowLabel")}</p>
              <div role="radiogroup" aria-label={t("windowLabel")} className="inline-flex rounded-lg border p-0.5 text-sm">
                {options.map((option) => {
                  const active = choice.windowSeconds === option.seconds;
                  return (
                    <button
                      key={option.seconds}
                      type="button"
                      role="radio"
                      aria-checked={active}
                      disabled={disabled}
                      onClick={() => onChoiceChange({ ...choice, windowSeconds: option.seconds })}
                      className={cn(
                        "rounded-md px-3 py-1 font-medium transition-colors disabled:cursor-not-allowed",
                        active ? "bg-foreground text-background" : "text-muted-foreground hover:text-foreground disabled:hover:text-muted-foreground",
                      )}
                    >
                      {option.wholeStream ? t("wholeStreamShort") : plans.formatRuntime(option.seconds)}
                    </button>
                  );
                })}
              </div>
            </div>
            <StreamTimeline
              elapsedSeconds={offer.elapsedSeconds}
              windowSeconds={Math.min(choice.windowSeconds, offer.elapsedSeconds)}
              follow={choice.follow}
              labels={{ start: t("timeline.start"), now: t("timeline.now"), next: t("timeline.next"), clipped: windowLabel }}
            />
          </>
        ) : (
          <p className="text-sm text-muted-foreground">{t("tooNew")}</p>
        )}

        <div role="radiogroup" aria-label={t("modeLabel")} className="mt-4 grid gap-2 sm:grid-cols-2">
          <ModeOption
            selected={!choice.follow}
            disabled={disabled}
            title={t("modeOnce")}
            body={selected?.wholeStream ? t("modeOnceBodyAll") : t("modeOnceBody", { duration: windowLabel })}
            onSelect={() => onChoiceChange({ ...choice, follow: false })}
          />
          <ModeOption
            selected={choice.follow}
            disabled={disabled}
            title={t("modeFollow")}
            body={t("modeFollowBody", { interval })}
            onSelect={() => onChoiceChange({ ...choice, follow: true })}
          />
        </div>
      </div>

      <footer className="flex flex-col-reverse gap-3 border-t bg-muted/30 px-4 py-3 sm:flex-row sm:items-center sm:px-5">
        <p className="text-xs text-muted-foreground sm:mr-auto">
          {locked ? t("locked") : choice.follow ? t("usageFollow", { minutes: liveMinutesNow(offer, choice) }) : t("usage", { minutes: liveMinutesNow(offer, choice) })}
        </p>
        <div className="flex gap-2 sm:justify-end">
          <Button type="button" variant="ghost" onClick={onCancel} disabled={busy}>{t("cancel")}</Button>
          {locked ? (
            <Button type="button" onClick={onUpgrade}>{t("seeCreator")}</Button>
          ) : (
            <Button type="button" onClick={onSubmit} disabled={busy || options.length === 0}>
              {busy ? <Loader2 className="size-4 animate-spin" /> : null}
              {submitLabel}
            </Button>
          )}
        </div>
      </footer>
    </section>
  );
}

function ModeOption({ selected, disabled, title, body, onSelect }: {
  selected: boolean;
  disabled: boolean;
  title: string;
  body: string;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      disabled={disabled}
      onClick={onSelect}
      className={cn(
        "flex w-full items-start gap-3 rounded-lg border px-3.5 py-3 text-left transition-colors disabled:cursor-not-allowed",
        !disabled && "hover:border-foreground/30",
        selected && "border-foreground ring-1 ring-foreground",
        disabled && !selected && "opacity-60",
      )}
    >
      <span className={cn(
        "mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border",
        selected ? "border-foreground bg-foreground text-background" : "border-muted-foreground/40",
      )}>
        {selected && <Check className="size-2.5" strokeWidth={3} />}
      </span>
      <span className="min-w-0">
        <span className="block text-sm font-semibold">{title}</span>
        <span className="block text-xs text-muted-foreground">{body}</span>
      </span>
    </button>
  );
}

/** The stream so far, with the part that will be clipped and, when following, the batches to come. */
function StreamTimeline({ elapsedSeconds, windowSeconds, follow, labels }: {
  elapsedSeconds: number;
  windowSeconds: number;
  follow: boolean;
  labels: { start: string; now: string; next: string; clipped: string };
}) {
  const total = Math.max(1, elapsedSeconds);
  // When following, the bar keeps a strip for what comes next after "now".
  const future = follow ? 0.16 : 0;
  const scale = (1 - future) / total;
  const width = Math.max(0.04, windowSeconds * scale);
  const left = 1 - future - width;
  return (
    <div className="mt-4" aria-hidden>
      <div className="relative h-8">
        <div className="absolute inset-x-0 top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-muted" />
        <div
          className="absolute top-1/2 flex h-6 -translate-y-1/2 items-center justify-center overflow-hidden rounded-md bg-foreground px-2 text-[11px] font-medium text-background transition-all duration-300"
          style={{ left: `${left * 100}%`, width: `${width * 100}%` }}
        >
          <span className="truncate">{labels.clipped}</span>
        </div>
        {follow && (
          <div
            className="absolute top-1/2 flex h-6 -translate-y-1/2 items-center justify-center overflow-hidden rounded-md border border-dashed border-foreground/30 bg-[repeating-linear-gradient(135deg,transparent_0_5px,var(--color-muted)_5px_10px)] px-1.5 text-[11px] text-muted-foreground"
            style={{ left: `calc(${(1 - future) * 100}% + 4px)`, right: 0 }}
          >
            <span className="truncate">{labels.next}</span>
          </div>
        )}
        <span
          className="absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-background bg-red-600 shadow-sm"
          style={{ left: follow ? `${(1 - future) * 100}%` : "calc(100% - 6px)" }}
        />
      </div>
      <div className="relative mt-1 flex justify-between text-[11px] text-muted-foreground">
        <span>{labels.start}</span>
        {follow
          ? <span className="absolute -translate-x-1/2 font-medium text-foreground" style={{ left: `${(1 - future) * 100}%` }}>{labels.now}</span>
          : <span className="font-medium text-foreground">{labels.now}</span>}
      </div>
    </div>
  );
}
