"use client";

import { useId } from "react";
import { useTranslations } from "next-intl";
import { Loader2, Radio } from "lucide-react";

import { useBillingPlanText } from "@/components/billing/upgrade-prompt";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { liveMinutesNow, liveWindowOptions, type LiveChoice, type LiveOffer } from "@/lib/live-clipping";
import { cn } from "@/lib/utils";

/**
 * Shown when the pasted link is a stream that is live right now: clip its newest part,
 * optionally keep clipping until it ends. Accounts without the Creator plan see what the
 * plan adds instead of the controls doing anything.
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
  const followId = useId();
  const options = liveWindowOptions(offer);
  const locked = !offer.canClipLive;
  const channel = offer.channel || t(`platforms.${offer.platform}`);
  const intervalMinutes = Math.round(offer.followIntervalSeconds / 60);
  return (
    <section
      ref={ref}
      aria-labelledby={headingId}
      data-testid="live-stream-panel"
      data-tour="live-panel"
      className="mt-4 rounded-2xl border bg-background p-4 shadow-sm sm:p-5"
    >
      <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-red-600 dark:text-red-400">
        <span aria-hidden className="relative flex size-2">
          <span className="absolute inline-flex size-full animate-ping rounded-full bg-red-500 opacity-60 motion-reduce:animate-none" />
          <span className="relative inline-flex size-2 rounded-full bg-red-500" />
        </span>
        {t("liveNow")}
      </p>
      <h2 id={headingId} className="mt-1.5 font-display text-base font-bold tracking-tight">
        {t("title", { channel })}
      </h2>
      <p className="mt-1 text-sm text-muted-foreground">
        {offer.elapsedSeconds > 0
          ? t("body", { elapsed: plans.formatRuntime(offer.elapsedSeconds) })
          : t("bodyNoElapsed")}
      </p>

      {options.length > 0 ? (
        <div role="radiogroup" aria-label={t("windowLabel")} className="mt-4 flex flex-wrap gap-2">
          {options.map((option) => {
            const selected = choice.windowSeconds === option.seconds;
            return (
              <button
                key={option.seconds}
                type="button"
                role="radio"
                aria-checked={selected}
                disabled={busy || locked}
                onClick={() => onChoiceChange({ ...choice, windowSeconds: option.seconds })}
                className={cn(
                  "rounded-full border px-3 py-1.5 text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-60",
                  selected ? "border-foreground bg-foreground text-background" : "hover:bg-muted",
                )}
              >
                {option.wholeStream
                  ? t("wholeStream")
                  : t("lastMinutes", { minutes: Math.round(option.seconds / 60) })}
              </button>
            );
          })}
        </div>
      ) : (
        <p className="mt-4 text-sm text-muted-foreground">{t("tooNew")}</p>
      )}

      <div className="mt-4 flex items-start gap-3 rounded-xl border bg-muted/30 p-3">
        <Radio aria-hidden className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <label htmlFor={followId} className="text-sm font-medium">{t("followTitle")}</label>
          <p className="mt-0.5 text-xs text-muted-foreground">{t("followBody", { minutes: intervalMinutes })}</p>
        </div>
        <Switch
          id={followId}
          checked={choice.follow}
          disabled={busy || locked}
          onCheckedChange={(follow) => onChoiceChange({ ...choice, follow })}
        />
      </div>

      {locked ? (
        <p className="mt-3 flex flex-wrap items-center gap-1.5 text-sm text-muted-foreground">
          <span className="rounded-full border border-brand/25 bg-brand/5 px-1.5 py-px text-[10px] font-medium leading-4 text-brand">
            {t("creatorTag")}
          </span>
          {t("locked")}
        </p>
      ) : (
        <p className="mt-3 text-xs text-muted-foreground">
          {t("usage", { minutes: liveMinutesNow(offer, choice) })}
          {choice.follow ? ` ${t("usageFollow")}` : ""}
        </p>
      )}

      <div className="mt-4 flex flex-col-reverse gap-2 sm:flex-row sm:items-center">
        <Button type="button" variant="ghost" onClick={onCancel} disabled={busy} className="sm:mr-auto">{t("cancel")}</Button>
        {locked ? (
          <Button type="button" onClick={onUpgrade} className="bg-brand text-brand-foreground hover:bg-brand/90">
            {t("seeCreator")}
          </Button>
        ) : (
          <Button
            type="button"
            onClick={onSubmit}
            disabled={busy || options.length === 0}
            className="bg-brand text-brand-foreground hover:bg-brand/90"
          >
            {busy ? <Loader2 className="size-4 animate-spin" /> : null}
            {choice.follow ? t("submitFollow") : t("submit")}
          </Button>
        )}
      </div>
    </section>
  );
}
