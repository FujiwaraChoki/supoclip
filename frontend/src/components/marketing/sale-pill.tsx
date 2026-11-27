"use client";

import { useEffect, useState } from "react";
import { BadgePercent } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";

import { intlLocale } from "@/i18n/config";
import { activeSale, type Sale } from "@/lib/sale";

/**
 * The sale running now, read after mount: the landing page is prerendered, so the
 * build's clock must not decide whether a visitor sees it.
 */
export function useActiveSale(): Sale | null {
  const [sale, setSale] = useState<Sale | null>(null);
  useEffect(() => {
    const update = () => setSale(activeSale());
    update();
    const timer = window.setInterval(update, 60_000);
    return () => window.clearInterval(timer);
  }, []);
  return sale;
}

/** "Dec 6": the sale's last day, in Pacific time (it ends at midnight there). */
export function useSaleLastDay(sale: Sale | null): string {
  const locale = useLocale();
  if (!sale) return "";
  return new Date(sale.endsAt.getTime() - 1).toLocaleDateString(intlLocale(locale), {
    month: "short",
    day: "numeric",
    timeZone: "America/Los_Angeles",
  });
}

/** One quiet line in the hero's announcement row while a sale runs. */
export function SalePill() {
  const t = useTranslations("marketing");
  const sale = useActiveSale();
  const lastDay = useSaleLastDay(sale);
  if (!sale) return null;
  return (
    <a
      href="#pricing"
      data-testid="sale-pill"
      className="flex items-center gap-2 rounded-full border px-2.5 py-2 text-sm font-medium transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <BadgePercent className="size-4 shrink-0 text-brand" aria-hidden />
      <span>{t("sale.pill", { percent: sale.percentOff, date: lastDay })}</span>
    </a>
  );
}
