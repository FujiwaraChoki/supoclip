"use client";

import { useId, useMemo, useState } from "react";

import { Slider } from "@/components/ui/slider";
import { intlLocale } from "@/i18n/config";
import { getGta6Copy, type GuideLocale } from "@/lib/localized-guides";

/** Hosted plans by monthly minutes of source video (see BillingService). */
const PLANS = [
  { name: "Pro", minutes: 600, price: 9.99 },
  { name: "Creator", minutes: 1500, price: 24.99 },
] as const;

const WEEKS_PER_MONTH = 52 / 12;

export interface EarningsInput {
  clipsPerWeek: number;
  viewsPerClip: number;
  ratePerThousand: number;
  capPerClip: number;
  vodHours: number;
}

export function estimateEarnings({ clipsPerWeek, viewsPerClip, ratePerThousand, capPerClip, vodHours }: EarningsInput) {
  const clipsPerMonth = Math.round(clipsPerWeek * WEEKS_PER_MONTH);
  const uncappedPerClip = (viewsPerClip / 1000) * ratePerThousand;
  const perClip = capPerClip > 0 ? Math.min(uncappedPerClip, capPerClip) : uncappedPerClip;
  const payout = perClip * clipsPerMonth;
  const minutes = vodHours * 60;
  const plan = PLANS.find((candidate) => minutes <= candidate.minutes) ?? null;
  return {
    clipsPerMonth,
    views: viewsPerClip * clipsPerMonth,
    perClip,
    capped: capPerClip > 0 && uncappedPerClip > capPerClip,
    payout,
    minutes,
    plan,
    net: plan ? payout - plan.price : null,
  };
}

function Control({ label, value, display, min, max, step, onChange, hint }: {
  label: string; value: number; display: string; min: number; max: number; step: number;
  onChange: (value: number) => void; hint: string;
}) {
  const id = useId();
  return (
    <div className="space-y-3">
      <div className="flex items-baseline justify-between gap-4">
        <label id={id} className="text-sm font-medium text-foreground">{label}</label>
        <output aria-labelledby={id} className="font-mono text-sm font-semibold tabular-nums text-foreground">{display}</output>
      </div>
      <Slider aria-labelledby={id} value={[value]} min={min} max={max} step={step} onValueChange={([next]) => onChange(next)} />
      <p className="text-xs leading-5 text-muted-foreground">{hint}</p>
    </div>
  );
}

function Tile({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="rounded-xl border bg-background p-4">
      <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{label}</p>
      <div className="mt-1 text-2xl font-bold tabular-nums tracking-tight text-foreground">{value}</div>
      <p className="mt-1 text-xs leading-5 text-muted-foreground">{detail}</p>
    </div>
  );
}

/** Interactive campaign-payout estimate. Inputs are the reader's assumptions, not data. */
export function EarningsCalculator({ locale = "en" }: { locale?: GuideLocale }) {
  const [clipsPerWeek, setClipsPerWeek] = useState(15);
  const [viewsPerClip, setViewsPerClip] = useState(3000);
  const [ratePerThousand, setRate] = useState(1);
  const [capPerClip, setCap] = useState(150);
  const [vodHours, setVodHours] = useState(8);
  const result = estimateEarnings({ clipsPerWeek, viewsPerClip, ratePerThousand, capPerClip, vodHours });
  const copy = getGta6Copy(locale).calculator;
  const tag = intlLocale(locale);
  const { usd, usdCents, count } = useMemo(() => ({
    usd: new Intl.NumberFormat(tag, { style: "currency", currency: "USD", maximumFractionDigits: 0 }),
    usdCents: new Intl.NumberFormat(tag, { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 }),
    count: new Intl.NumberFormat(tag, { notation: "compact", maximumFractionDigits: 1 }),
  }), [tag]);

  return (
    <figure className="not-prose space-y-6 rounded-2xl border bg-muted/20 p-5 sm:p-7" aria-labelledby="calculator-title">
      <div className="space-y-2">
        <p className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">{copy.eyebrow}</p>
        <h3 id="calculator-title" className="text-xl font-bold tracking-tight text-foreground">{copy.title}</h3>
        <p className="text-sm leading-6 text-muted-foreground">{copy.description}</p>
      </div>

      <div className="grid gap-6 sm:grid-cols-2">
        <Control label={copy.clipsPerWeekLabel} value={clipsPerWeek} display={String(clipsPerWeek)} min={1} max={70} step={1} onChange={setClipsPerWeek}
          hint={copy.clipsPerWeekHint} />
        <Control label={copy.viewsPerClipLabel} value={viewsPerClip} display={count.format(viewsPerClip)} min={0} max={50000} step={500} onChange={setViewsPerClip}
          hint={copy.viewsPerClipHint} />
        <Control label={copy.rateLabel} value={ratePerThousand} display={usdCents.format(ratePerThousand)} min={0.1} max={5} step={0.1} onChange={setRate}
          hint={copy.rateHint} />
        <Control label={copy.capLabel} value={capPerClip} display={capPerClip === 0 ? copy.capNone : usd.format(capPerClip)} min={0} max={500} step={10} onChange={setCap}
          hint={copy.capHint} />
        <Control label={copy.vodHoursLabel} value={vodHours} display={`${vodHours} ${copy.vodHoursUnit}`} min={1} max={30} step={1} onChange={setVodHours}
          hint={copy.vodHoursHint} />
      </div>

      <div className="grid gap-3 sm:grid-cols-3" aria-live="polite">
        <Tile label={copy.payoutLabel} value={usd.format(result.payout)}
          detail={copy.payoutDetail(result.clipsPerMonth, count.format(result.views))} />
        <Tile label={copy.perClipLabel} value={usdCents.format(result.perClip)}
          detail={result.capped ? copy.cappedDetail : copy.uncappedDetail} />
        <Tile label={copy.afterPlanLabel} value={result.net === null ? "—" : usd.format(result.net)}
          detail={result.plan
            ? copy.planFitsDetail(result.minutes, result.plan.name, usdCents.format(result.plan.price), result.plan.minutes)
            : copy.planOverDetail(result.minutes)} />
      </div>

      <figcaption className="space-y-2 text-xs leading-5 text-muted-foreground">
        <p><strong className="text-foreground">{copy.averageLead}</strong>{copy.averageBody}</p>
        <p>{copy.disclaimer}</p>
      </figcaption>
    </figure>
  );
}
