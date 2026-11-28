"use client";

import { useId } from "react";
import { useTranslations } from "next-intl";
import { Loader2 } from "lucide-react";

import { useBillingPlanText } from "@/components/billing/upgrade-prompt";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { liveWindowOptions, type LiveChoice, type LiveOffer } from "@/lib/live-clipping";
import { cn } from "@/lib/utils";

/** `1:36:12` / `24:05`: how long the stream has been live, or a second of it. */
export function streamClock(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = String(total % 60).padStart(2, "0");
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}` : `${minutes}:${rest}`;
}

/** The pasted link is live: pick how much of it to clip, and whether to keep clipping. */
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
  const followId = useId();
  const options = liveWindowOptions(offer);
  const selected = options.find((option) => option.seconds === choice.windowSeconds);
  const channel = offer.channel || t(`platforms.${offer.platform}`);
  const submitLabel = choice.follow
    ? t("submitFollow")
    : selected?.wholeStream ? t("submitAll") : t("submitOnce", { duration: plans.formatRuntime(choice.windowSeconds) });

  return (
    <section ref={ref} aria-labelledby={headingId} data-testid="live-stream-panel" data-tour="live-panel" className="mt-4 rounded-2xl border bg-background p-4 sm:p-5">
      <h2 id={headingId} className="flex items-center gap-2 text-sm font-medium">
        <span aria-hidden className="size-2 shrink-0 rounded-full bg-red-500" />
        <span className="truncate">{t("title", { channel })}</span>
        {offer.elapsedSeconds > 0 && <span className="shrink-0 font-normal tabular-nums text-muted-foreground">{streamClock(offer.elapsedSeconds)}</span>}
      </h2>

      {!offer.canClipLive ? (
        <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-center">
          <p className="text-sm text-muted-foreground sm:mr-auto">{t("locked")}</p>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={onCancel}>{t("cancel")}</Button>
            <Button type="button" onClick={onUpgrade}>{t("seeCreator")}</Button>
          </div>
        </div>
      ) : options.length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">{t("tooNew")}</p>
      ) : (
        <>
          <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-3">
            <div role="radiogroup" aria-label={t("windowLabel")} className="inline-flex rounded-lg border p-0.5 text-sm">
              {options.map((option) => {
                const active = choice.windowSeconds === option.seconds;
                return (
                  <button
                    key={option.seconds}
                    type="button"
                    role="radio"
                    aria-checked={active}
                    disabled={busy}
                    onClick={() => onChoiceChange({ ...choice, windowSeconds: option.seconds })}
                    className={cn(
                      "rounded-md px-3 py-1 font-medium transition-colors",
                      active ? "bg-foreground text-background" : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {option.wholeStream ? t("wholeStream") : plans.formatRuntime(option.seconds)}
                  </button>
                );
              })}
            </div>
            <label htmlFor={followId} className="flex items-center gap-2 text-sm">
              <Switch id={followId} checked={choice.follow} disabled={busy} onCheckedChange={(follow) => onChoiceChange({ ...choice, follow })} />
              {t("follow", { interval: plans.formatRuntime(offer.followIntervalSeconds) })}
            </label>
          </div>
          <div className="mt-4 flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={onCancel} disabled={busy}>{t("cancel")}</Button>
            <Button type="button" onClick={onSubmit} disabled={busy}>
              {busy ? <Loader2 className="size-4 animate-spin" /> : null}
              {submitLabel}
            </Button>
          </div>
        </>
      )}
    </section>
  );
}
