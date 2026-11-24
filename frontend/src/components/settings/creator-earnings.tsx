"use client";

import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { AlertCircle, ExternalLink, Loader2 } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useCountryName } from "@/components/settings/creator-country-select";
import { formatMinor } from "@/lib/creator-commission";

type Totals = { currency: string; pending: number; available: number; in_transit: number; paid: number };
type Activity = {
  occurred_at: string;
  kind: "earned" | "refund" | "dispute" | "dispute_won";
  amount: number;
  currency: string;
  available_at: string;
  payout_status: string | null;
};
type Earnings = {
  terms: { percent: number; months: number; hold_days: number; minimum_payout: number };
  totals: Totals[];
  referrals: { total: number; customers: number };
  activity: Activity[];
  payout_history: { created_at: string; amount: number; currency: string; status: string }[];
  payouts: { status: "not_started" | "incomplete" | "enabled" | "unavailable"; country: string | null; details_submitted: boolean };
};

export const creatorEarningsKey = ["creator-earnings"] as const;

export function CreatorEarnings() {
  const t = useTranslations("settings.creator.earnings");
  const locale = useLocale();
  const countryName = useCountryName();
  const [error, setError] = useState<string | null>(null);

  const earnings = useQuery({
    queryKey: creatorEarningsKey,
    queryFn: async (): Promise<Earnings> => {
      const response = await fetch("/api/affiliates/earnings", { cache: "no-store" });
      if (!response.ok) throw new Error(t("loadFailed"));
      return response.json();
    },
  });

  const openStripe = useMutation({
    mutationFn: async (action: "onboard" | "dashboard") => {
      const response = await fetch("/api/affiliates/payouts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.url) throw new Error(data.error || t("setupFailed"));
      return data.url as string;
    },
    onMutate: () => setError(null),
    onSuccess: (url) => window.location.assign(url),
    onError: (err) => setError(err instanceof Error ? err.message : t("setupFailed")),
  });

  if (earnings.isPending) return <Skeleton className="h-48 w-full rounded-2xl" />;
  if (earnings.isError) {
    return (
      <Alert variant="destructive">
        <AlertCircle className="h-4 w-4" />
        <AlertDescription>{earnings.error.message}</AlertDescription>
      </Alert>
    );
  }

  const { terms, totals, referrals, activity, payouts } = earnings.data;
  const money = (amount: number, currency: string) => formatMinor(amount, currency, locale);
  const date = (value: string) => new Date(value).toLocaleDateString(locale, { day: "numeric", month: "short", year: "numeric" });
  const shown: Totals[] = totals.length > 0 ? totals : [{ currency: "usd", pending: 0, available: 0, in_transit: 0, paid: 0 }];
  const minimum = money(terms.minimum_payout, shown[0].currency);
  const busy = openStripe.isPending;

  const status = (item: Activity) => {
    if (item.payout_status === "paid") return t("status.paid");
    if (item.payout_status === "pending") return t("status.onTheWay");
    if (item.kind !== "earned") return t("status.deducted");
    return new Date(item.available_at).getTime() > Date.now()
      ? t("status.heldUntil", { date: date(item.available_at) })
      : t("status.available");
  };

  return (
    <section className="space-y-5 rounded-2xl border bg-background p-5">
      <div>
        <h2 className="text-sm font-semibold">{t("title")}</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          {t("terms", { percent: terms.percent, months: terms.months, days: terms.hold_days, minimum })}
        </p>
      </div>

      {shown.map((total) => (
        <dl key={total.currency} className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[
            { label: t("customers"), value: String(referrals.total) },
            { label: t("pending"), value: money(total.pending, total.currency), hint: t("pendingHint", { days: terms.hold_days }) },
            { label: t("available"), value: money(total.available + total.in_transit, total.currency), hint: t("availableHint") },
            { label: t("paid"), value: money(total.paid, total.currency) },
          ].map((stat) => (
            <div key={stat.label} className="rounded-xl bg-muted/40 p-3">
              <dt className="text-xs text-muted-foreground">{stat.label}</dt>
              <dd className="mt-1 text-lg font-semibold tabular-nums">{stat.value}</dd>
              {stat.hint && <p className="mt-0.5 text-[11px] text-muted-foreground">{stat.hint}</p>}
            </div>
          ))}
        </dl>
      ))}

      <div className="rounded-xl border p-4">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-medium">{t("payoutsTitle")}</h3>
          {payouts.status === "enabled" && <Badge variant="outline">{t("payoutsOn")}</Badge>}
        </div>

        {payouts.status === "not_started" && (
          <>
            <p className="mt-1 text-sm text-muted-foreground">{t("setupBody")}</p>
            {payouts.country && (
              <p className="mt-1 text-xs text-muted-foreground">{t("payoutCountry", { country: countryName(payouts.country) })}</p>
            )}
            <Button className="mt-3" onClick={() => openStripe.mutate("onboard")} disabled={busy}>
              {busy && <Loader2 className="size-4 animate-spin" />}
              {t("setup")}
            </Button>
          </>
        )}

        {payouts.status === "incomplete" && (
          <>
            <p className="mt-1 text-sm text-muted-foreground">
              {payouts.details_submitted ? t("reviewBody") : t("incompleteBody")}
            </p>
            <Button className="mt-3" onClick={() => openStripe.mutate("onboard")} disabled={busy}>
              {busy && <Loader2 className="size-4 animate-spin" />}
              {t("finish")}
            </Button>
          </>
        )}

        {payouts.status === "enabled" && (
          <>
            <p className="mt-1 text-sm text-muted-foreground">{t("enabledBody", { minimum })}</p>
            <Button variant="outline" className="mt-3" onClick={() => openStripe.mutate("dashboard")} disabled={busy}>
              {busy ? <Loader2 className="size-4 animate-spin" /> : <ExternalLink className="size-4" />}
              {t("dashboard")}
            </Button>
          </>
        )}

        {payouts.status === "unavailable" && <p className="mt-1 text-sm text-muted-foreground">{t("unavailableBody")}</p>}

        {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
      </div>

      <div>
        <h3 className="text-sm font-medium">{t("activityTitle")}</h3>
        {activity.length === 0 ? (
          <p className="mt-1 text-sm text-muted-foreground">{t("activityEmpty")}</p>
        ) : (
          <table className="mt-2 w-full text-sm">
            <tbody className="divide-y">
              {activity.map((item, index) => (
                <tr key={`${item.occurred_at}-${index}`}>
                  <td className="py-2 pr-3 text-muted-foreground">{date(item.occurred_at)}</td>
                  <td className="py-2 pr-3">{t(`kind.${item.kind}`)}</td>
                  <td className="py-2 pr-3 text-right font-medium tabular-nums">{money(item.amount, item.currency)}</td>
                  <td className="py-2 text-right text-xs text-muted-foreground">{status(item)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}
