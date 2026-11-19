"use client";

import { useId, useState } from "react";

import { Slider } from "@/components/ui/slider";

/** Hosted plans by monthly minutes of source video (see BillingService). */
const PLANS = [
  { name: "Pro", minutes: 600, price: 9.99 },
  { name: "Creator", minutes: 1500, price: 24.99 },
] as const;

const WEEKS_PER_MONTH = 52 / 12;

const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const usdCents = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const count = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

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
export function EarningsCalculator() {
  const [clipsPerWeek, setClipsPerWeek] = useState(15);
  const [viewsPerClip, setViewsPerClip] = useState(3000);
  const [ratePerThousand, setRate] = useState(1);
  const [capPerClip, setCap] = useState(150);
  const [vodHours, setVodHours] = useState(8);
  const result = estimateEarnings({ clipsPerWeek, viewsPerClip, ratePerThousand, capPerClip, vodHours });

  return (
    <figure className="not-prose space-y-6 rounded-2xl border bg-muted/20 p-5 sm:p-7" aria-labelledby="calculator-title">
      <div className="space-y-2">
        <p className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">Interactive estimate</p>
        <h3 id="calculator-title" className="text-xl font-bold tracking-tight text-foreground">What a month of GTA 6 clips could pay</h3>
        <p className="text-sm leading-6 text-muted-foreground">Move the sliders to your own assumptions. Payout per clip is approved views × the campaign rate, stopped at the per-clip cap.</p>
      </div>

      <div className="grid gap-6 sm:grid-cols-2">
        <Control label="Clips posted per week" value={clipsPerWeek} display={String(clipsPerWeek)} min={1} max={70} step={1} onChange={setClipsPerWeek}
          hint="Count only clips you would actually be proud to post." />
        <Control label="Approved views per clip (average)" value={viewsPerClip} display={count.format(viewsPerClip)} min={0} max={50000} step={500} onChange={setViewsPerClip}
          hint="Campaigns pay on views that pass their review, inside their tracking window." />
        <Control label="Rate per 1,000 views" value={ratePerThousand} display={usdCents.format(ratePerThousand)} min={0.1} max={5} step={0.1} onChange={setRate}
          hint="Set by each campaign. Around $1 is a common reference point." />
        <Control label="Cap per clip" value={capPerClip} display={capPerClip === 0 ? "No cap" : usd.format(capPerClip)} min={0} max={500} step={10} onChange={setCap}
          hint="Many campaigns stop paying a clip past a set amount. 0 means no cap." />
        <Control label="Stream hours you clip per month" value={vodHours} display={`${vodHours} h`} min={1} max={30} step={1} onChange={setVodHours}
          hint="Hosted SupoClip meters minutes of source video, not clips." />
      </div>

      <div className="grid gap-3 sm:grid-cols-3" aria-live="polite">
        <Tile label="Estimated payout" value={usd.format(result.payout)}
          detail={`${result.clipsPerMonth} clips · ${count.format(result.views)} views a month`} />
        <Tile label="Per clip" value={usdCents.format(result.perClip)}
          detail={result.capped ? "Every clip hits the cap at this view count." : "Below the per-clip cap."} />
        <Tile label="After SupoClip plan" value={result.net === null ? "—" : usd.format(result.net)}
          detail={result.plan
            ? `${result.minutes} source minutes fits ${result.plan.name} (${usdCents.format(result.plan.price)}/mo, ${result.plan.minutes} min).`
            : `${result.minutes} minutes is more than Creator's 1,500. Clip fewer hours, or self-host.`} />
      </div>

      <figcaption className="space-y-2 text-xs leading-5 text-muted-foreground">
        <p><strong className="text-foreground">An average hides the shape.</strong> One clip at 200,000 views and nineteen at 500 average out to about 10,500 views per clip, but at a $150 cap that month pays roughly $160, not the $210 an even spread suggests. Expect most clips to earn little and a few to carry the month.</p>
        <p>Estimate only, before taxes and fees. It is not a forecast of what you will earn, and campaign budgets can run out before your views are counted.</p>
      </figcaption>
    </figure>
  );
}
