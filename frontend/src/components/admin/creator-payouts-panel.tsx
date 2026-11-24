"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { Loader2 } from "lucide-react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatMinor } from "@/lib/creator-commission";

type Balance = {
  user_id: string;
  name: string | null;
  email: string;
  slug: string | null;
  currency: string;
  available: number;
  pending: number;
  payouts_enabled: boolean;
};
type Result = { user_id: string; currency: string; amount: number; outcome: "paid" | "failed" | "pending" | "skipped"; reason?: string };

const key = ["admin-creator-payouts"] as const;
const th = "px-5 py-2.5 text-left text-[11px] font-medium uppercase tracking-wider text-muted-foreground";
const td = "px-5 py-3 text-sm";

/** Unpaid creator commission and the button that pays it through Stripe Connect. */
export function CreatorPayoutsPanel() {
  const t = useTranslations("admin.creatorPayouts");
  const locale = useLocale();
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [results, setResults] = useState<Result[] | null>(null);

  const data = useQuery({
    queryKey: key,
    queryFn: async (): Promise<{ balances: Balance[]; minimum_payout: number }> => {
      const response = await fetch("/api/admin/affiliates/payouts", { cache: "no-store" });
      if (!response.ok) throw new Error(t("loadFailed"));
      return response.json();
    },
  });

  const run = useMutation({
    mutationFn: async () => {
      const response = await fetch("/api/admin/affiliates/payouts", { method: "POST" });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error || t("runFailed"));
      return body as { results: Result[]; balances: Balance[] };
    },
    onSuccess: (body) => {
      setResults(body.results);
      queryClient.setQueryData(key, (previous: { minimum_payout: number } | undefined) => ({
        minimum_payout: previous?.minimum_payout ?? 0,
        balances: body.balances,
      }));
    },
    onSettled: () => setConfirming(false),
  });

  const money = (amount: number, currency: string) => formatMinor(amount, currency, locale);
  const balances = data.data?.balances ?? [];
  const minimum = data.data?.minimum_payout ?? 0;
  const payable = balances.filter((balance) => balance.payouts_enabled && balance.available >= minimum);
  const totals = new Map<string, number>();
  for (const balance of payable) totals.set(balance.currency, (totals.get(balance.currency) ?? 0) + balance.available);
  const totalLabel = [...totals].map(([currency, amount]) => money(amount, currency)).join(" + ");
  const nameOf = (userId: string) => {
    const balance = balances.find((row) => row.user_id === userId);
    return balance?.slug?.toUpperCase() ?? balance?.email ?? userId;
  };

  return (
    <div className="border-b">
      <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-4">
        <div>
          <h3 className="text-sm font-medium">{t("title")}</h3>
          <p className="text-xs text-muted-foreground">{t("description", { minimum: money(minimum || 2500, "usd") })}</p>
        </div>
        <Button size="sm" onClick={() => setConfirming(true)} disabled={payable.length === 0 || run.isPending}>
          {run.isPending && <Loader2 className="size-4 animate-spin" />}
          {payable.length === 0 ? t("nothingToPay") : t("pay", { count: payable.length, total: totalLabel })}
        </Button>
      </div>

      {data.isError && <p className="px-5 pb-4 text-sm text-red-600">{data.error.message}</p>}
      {run.isError && <p className="px-5 pb-4 text-sm text-red-600">{run.error.message}</p>}

      {results && results.length > 0 && (
        <ul className="space-y-1 px-5 pb-4 text-xs">
          {results.map((result, index) => (
            <li key={`${result.user_id}-${result.currency}-${index}`} className={result.outcome === "paid" ? "" : "text-red-600"}>
              {t(`outcome.${result.outcome}`, { name: nameOf(result.user_id), amount: money(result.amount, result.currency) })}
              {result.reason ? ` (${result.reason})` : ""}
            </li>
          ))}
        </ul>
      )}

      {balances.length > 0 && (
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y">
            <thead className="bg-muted/40">
              <tr>
                <th className={th}>{t("creator")}</th>
                <th className={`${th} text-right`}>{t("available")}</th>
                <th className={`${th} text-right`}>{t("pending")}</th>
                <th className={th}>{t("payouts")}</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {balances.map((balance) => (
                <tr key={`${balance.user_id}-${balance.currency}`}>
                  <td className={td}>
                    <span className="font-mono">{balance.slug?.toUpperCase()}</span>
                    <p className="text-xs text-muted-foreground">{balance.email}</p>
                  </td>
                  <td className={`${td} text-right tabular-nums`}>{money(balance.available, balance.currency)}</td>
                  <td className={`${td} text-right tabular-nums text-muted-foreground`}>{money(balance.pending, balance.currency)}</td>
                  <td className={td}>
                    <Badge variant="outline">{balance.payouts_enabled ? t("ready") : t("notSetUp")}</Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <AlertDialog open={confirming} onOpenChange={(open) => !run.isPending && setConfirming(open)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("confirmTitle", { total: totalLabel })}</AlertDialogTitle>
            <AlertDialogDescription>{t("confirmBody", { count: payable.length })}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={run.isPending}>{t("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={(event) => {
                event.preventDefault();
                run.mutate();
              }}
              disabled={run.isPending}
            >
              {run.isPending && <Loader2 className="size-4 animate-spin" />}
              {t("confirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
