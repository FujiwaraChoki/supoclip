"use client";

import { useId, useState } from "react";

import { Slider } from "@/components/ui/slider";

/** Hosted SupoClip plans by monthly minutes of source video (mirrors billing-plans.ts defaults). */
const SOLO_PLANS = [
  { name: "Pro", minutes: 600, price: 9.99 },
  { name: "Creator", minutes: 1500, price: 24.99 },
] as const;

const COMMERCIAL_PLANS = [
  { name: "Pro", minutes: 600, price: 9.99 },
  { name: "Creator", minutes: 1500, price: 24.99 },
  { name: "Team", minutes: 3600, price: 69 },
] as const;

const WEEKS_PER_MONTH = 52 / 12;
export const CONTENT_REWARDS_CREATOR_FEE = 0.1;

const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const usdCents = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const count = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

export interface WhopCampaignInput {
  clipsPerWeek: number;
  viewsPerClip: number;
  ratePerThousand: number;
  capPerClip: number;
  sourceHours: number;
}

export function estimateWhopCampaign({
  clipsPerWeek,
  viewsPerClip,
  ratePerThousand,
  capPerClip,
  sourceHours,
}: WhopCampaignInput) {
  const clipsPerMonth = Math.round(clipsPerWeek * WEEKS_PER_MONTH);
  const uncappedPerClip = (viewsPerClip / 1000) * ratePerThousand;
  const grossPerClip = capPerClip > 0 ? Math.min(uncappedPerClip, capPerClip) : uncappedPerClip;
  const grossMonthly = grossPerClip * clipsPerMonth;
  const platformFee = grossMonthly * CONTENT_REWARDS_CREATOR_FEE;
  const afterFeeMonthly = grossMonthly - platformFee;
  const minutes = sourceHours * 60;
  const plan = SOLO_PLANS.find((candidate) => minutes <= candidate.minutes) ?? null;

  return {
    clipsPerMonth,
    views: viewsPerClip * clipsPerMonth,
    grossPerClip,
    netPerClip: grossPerClip * (1 - CONTENT_REWARDS_CREATOR_FEE),
    capped: capPerClip > 0 && uncappedPerClip > capPerClip,
    grossMonthly,
    platformFee,
    afterFeeMonthly,
    minutes,
    plan,
    netAfterPlan: plan ? afterFeeMonthly - plan.price : null,
  };
}

export interface RetainerInput {
  clients: number;
  retainerPerClient: number;
  clipsPerClient: number;
  sourceHours: number;
}

export function estimateRetainerIncome({
  clients,
  retainerPerClient,
  clipsPerClient,
  sourceHours,
}: RetainerInput) {
  const grossMonthly = clients * retainerPerClient;
  const totalClips = clients * clipsPerClient;
  const effectivePerClip = totalClips > 0 ? grossMonthly / totalClips : 0;
  const minutes = sourceHours * 60;
  const plan = COMMERCIAL_PLANS.find((candidate) => minutes <= candidate.minutes) ?? null;

  return {
    grossMonthly,
    totalClips,
    effectivePerClip,
    minutes,
    plan,
    netAfterPlan: plan ? grossMonthly - plan.price : null,
  };
}

function Control({
  label,
  value,
  display,
  min,
  max,
  step,
  onChange,
  hint,
}: {
  label: string;
  value: number;
  display: string;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
  hint: string;
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

/** Interactive clipping income estimator supporting Whop CPM campaigns and direct creator retainers. */
export function ClippingIncomeCalculator() {
  const [mode, setMode] = useState<"whop" | "retainer">("whop");

  // Whop CPM defaults: realistic part-time clipper
  const [clipsPerWeek, setClipsPerWeek] = useState(15);
  const [viewsPerClip, setViewsPerClip] = useState(4000);
  const [ratePerThousand, setRatePerThousand] = useState(1.2);
  const [capPerClip, setCapPerClip] = useState(150);
  const [whopSourceHours, setWhopSourceHours] = useState(8);

  // Retainer defaults: 2 mid-tier podcast clients
  const [clients, setClients] = useState(2);
  const [retainerPerClient, setRetainerPerClient] = useState(1000);
  const [clipsPerClient, setClipsPerClient] = useState(25);
  const [retainerSourceHours, setRetainerSourceHours] = useState(16);

  const whop = estimateWhopCampaign({
    clipsPerWeek,
    viewsPerClip,
    ratePerThousand,
    capPerClip,
    sourceHours: whopSourceHours,
  });

  const retainer = estimateRetainerIncome({
    clients,
    retainerPerClient,
    clipsPerClient,
    sourceHours: retainerSourceHours,
  });

  return (
    <figure className="not-prose space-y-6 rounded-2xl border bg-muted/20 p-5 sm:p-7" aria-labelledby="clipping-calculator-title">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div className="space-y-1.5">
          <p className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">Interactive calculator</p>
          <h3 id="clipping-calculator-title" className="text-xl font-bold tracking-tight text-foreground">
            Compare Whop CPM payouts vs. creator retainers
          </h3>
        </div>
        <div role="tablist" aria-label="Income model" className="inline-flex rounded-lg border bg-background p-1">
          <button
            type="button"
            role="tab"
            aria-selected={mode === "whop"}
            onClick={() => setMode("whop")}
            className={`rounded-md px-3 py-1.5 text-xs font-semibold transition-colors ${
              mode === "whop" ? "bg-foreground text-background" : "text-muted-foreground hover:text-foreground"
            }`}
          >
            Whop / Content Rewards
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "retainer"}
            onClick={() => setMode("retainer")}
            className={`rounded-md px-3 py-1.5 text-xs font-semibold transition-colors ${
              mode === "retainer" ? "bg-foreground text-background" : "text-muted-foreground hover:text-foreground"
            }`}
          >
            Client Retainers
          </button>
        </div>
      </div>

      {mode === "whop" ? (
        <>
          <p className="text-sm leading-6 text-muted-foreground">
            Adjust the sliders to your own assumptions. Includes Content Rewards&apos; standard 10% creator fee on CPM campaigns and caps each clip at the campaign ceiling.
          </p>
          <div className="grid gap-6 sm:grid-cols-2">
            <Control
              label="Approved clips posted per week"
              value={clipsPerWeek}
              display={String(clipsPerWeek)}
              min={1}
              max={60}
              step={1}
              onChange={setClipsPerWeek}
              hint="Count clips that actually clear review, not rejected drafts."
            />
            <Control
              label="Average verified views per clip"
              value={viewsPerClip}
              display={count.format(viewsPerClip)}
              min={0}
              max={50000}
              step={500}
              onChange={setViewsPerClip}
              hint="Views counted inside the campaign's tracking window (often up to 30 days)."
            />
            <Control
              label="Campaign CPM (per 1,000 views)"
              value={ratePerThousand}
              display={usdCents.format(ratePerThousand)}
              min={0.2}
              max={5}
              step={0.1}
              onChange={setRatePerThousand}
              hint="Typical clipping campaigns sit between $0.50 and $2.00; median is near $1.00–$1.50."
            />
            <Control
              label="Per-clip payout cap"
              value={capPerClip}
              display={capPerClip === 0 ? "No cap" : usd.format(capPerClip)}
              min={0}
              max={500}
              step={10}
              onChange={setCapPerClip}
              hint="Many campaigns cap individual clips at $100–$250. Set to 0 for uncapped."
            />
            <Control
              label="Source video hours clipped / month"
              value={whopSourceHours}
              display={`${whopSourceHours} h`}
              min={1}
              max={30}
              step={1}
              onChange={setWhopSourceHours}
              hint="Hosted SupoClip meters source video minutes. Solo clippers on authorized campaigns can also self-host for free."
            />
          </div>

          <div className="grid gap-3 sm:grid-cols-3" aria-live="polite">
            <Tile
              label="Net after 10% fee"
              value={usd.format(whop.afterFeeMonthly)}
              detail={`${usd.format(whop.grossMonthly)} gross · ${whop.clipsPerMonth} clips · ${count.format(whop.views)} views/mo`}
            />
            <Tile
              label="Net per clip"
              value={usdCents.format(whop.netPerClip)}
              detail={whop.capped ? "Every clip hits the per-clip cap at this average." : `${usdCents.format(whop.grossPerClip)} gross before the 10% creator fee.`}
            />
            <Tile
              label="After hosted SupoClip"
              value={whop.netAfterPlan === null ? "Self-host" : usd.format(whop.netAfterPlan)}
              detail={
                whop.plan
                  ? `${whop.minutes} min fits ${whop.plan.name} (${usdCents.format(whop.plan.price)}/mo). Solo authorized clippers can also self-host.`
                  : `${whop.minutes} min exceeds Creator's 1,500 min—self-host for free under the solo campaign grant.`
              }
            />
          </div>

          <figcaption className="space-y-2 text-xs leading-5 text-muted-foreground">
            <p>
              <strong className="text-foreground">Why averages flatter CPM clipping:</strong> Views follow a power law. Nineteen clips at 400 views and one breakout at 250,000 views average ~12,900 views—but at a $150 cap and $1.20 CPM, the breakout stops at $150 gross instead of $300, cutting the whole month roughly in half before the 10% fee.
            </p>
            <p>
              Estimate only, before taxes and bank/instant withdrawal fees. Campaign budgets can run out before pending views settle.
            </p>
          </figcaption>
        </>
      ) : (
        <>
          <p className="text-sm leading-6 text-muted-foreground">
            Estimate predictable monthly revenue from clipping podcasts, founders, or streamers on direct retainers—with zero per-view cap or platform fee.
          </p>
          <div className="grid gap-6 sm:grid-cols-2">
            <Control
              label="Active retainer clients"
              value={clients}
              display={String(clients)}
              min={1}
              max={8}
              step={1}
              onChange={setClients}
              hint="Solo editors typically manage 2–4 recurring podcasts comfortably."
            />
            <Control
              label="Monthly retainer per client"
              value={retainerPerClient}
              display={usd.format(retainerPerClient)}
              min={300}
              max={3000}
              step={50}
              onChange={setRetainerPerClient}
              hint="Market benchmarks run ~$300–$1,000 freelance and $1,000–$3,000 dedicated."
            />
            <Control
              label="Delivered clips per client / month"
              value={clipsPerClient}
              display={String(clipsPerClient)}
              min={10}
              max={60}
              step={5}
              onChange={setClipsPerClient}
              hint="A weekly podcast usually yields 20–30 strong clips a month."
            />
            <Control
              label="Total source video hours / month"
              value={retainerSourceHours}
              display={`${retainerSourceHours} h`}
              min={4}
              max={60}
              step={2}
              onChange={setRetainerSourceHours}
              hint="Combined client footage processed each month across hosted plans."
            />
          </div>

          <div className="grid gap-3 sm:grid-cols-3" aria-live="polite">
            <Tile
              label="Monthly retainer revenue"
              value={usd.format(retainer.grossMonthly)}
              detail={`${retainer.totalClips} finished clips across ${clients} ${clients === 1 ? "client" : "clients"}`}
            />
            <Tile
              label="Effective rate per clip"
              value={usdCents.format(retainer.effectivePerClip)}
              detail="Paid whether a clip gets 500 views or 500,000 views."
            />
            <Tile
              label="After hosted SupoClip"
              value={retainer.netAfterPlan === null ? "Custom" : usd.format(retainer.netAfterPlan)}
              detail={
                retainer.plan
                  ? `${retainer.minutes} min fits ${retainer.plan.name} (${usdCents.format(retainer.plan.price)}/mo). Client work requires hosted plans or commercial permission.`
                  : `${retainer.minutes} min exceeds base Team (3,600 min)—add Team seats (+15 h each) for agency volume.`
              }
            />
          </div>

          <figcaption className="text-xs leading-5 text-muted-foreground">
            Client editing and agency work are commercial uses under the SupoClip Noncommercial Source License, so use a hosted plan (Pro, Creator, or Team) or request written commercial permission for self-hosting.
          </figcaption>
        </>
      )}
    </figure>
  );
}
