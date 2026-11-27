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

/**
 * The sale's loop as a wide card under the hero; the whole card goes to pricing.
 * The copy is in the video, so the link carries the same line as the pill for
 * screen readers. Visitors who prefer reduced motion get the still.
 */
export function SaleFeature() {
  const t = useTranslations("marketing");
  const sale = useActiveSale();
  const lastDay = useSaleLastDay(sale);
  const [still, setStill] = useState(false);
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setStill(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  if (!sale?.media) return null;
  const label = t("sale.pill", { percent: sale.percentOff, date: lastDay });
  return (
    <section aria-label={label} className="px-6 pb-16 md:pb-24" data-testid="sale-feature">
      <a
        href="#pricing"
        aria-label={label}
        className="mx-auto block max-w-2xl overflow-hidden rounded-2xl border bg-black transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {still ? (
          // eslint-disable-next-line @next/next/no-img-element -- a remote poster, shown as is
          <img src={sale.media.poster} alt="" width={1600} height={800} className="block aspect-[2/1] w-full" />
        ) : (
          <video
            className="block aspect-[2/1] w-full"
            poster={sale.media.poster}
            autoPlay
            muted
            loop
            playsInline
            preload="metadata"
            aria-hidden
          >
            <source src={sale.media.webm} type="video/webm" />
            <source src={sale.media.mp4} type="video/mp4" />
          </video>
        )}
      </a>
    </section>
  );
}
