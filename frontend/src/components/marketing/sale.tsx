"use client";

import { useEffect, useState } from "react";
import { BadgePercent, X } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";

import { intlLocale } from "@/i18n/config";
import { activeSale, type Sale, type SaleMedia } from "@/lib/sale";
import { cn } from "@/lib/utils";

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

function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return reduced;
}

/** The sale's loop, or its still for visitors who prefer reduced motion. Decorative. */
function SaleLoop({ media, className }: { media: SaleMedia; className?: string }) {
  const reduced = useReducedMotion();
  if (reduced) {
    // eslint-disable-next-line @next/next/no-img-element -- a remote poster, shown as is
    return <img src={media.poster} alt="" className={cn("block", className)} />;
  }
  return (
    <video className={cn("block", className)} poster={media.poster} autoPlay muted loop playsInline preload="metadata" aria-hidden>
      <source src={media.webm} type="video/webm" />
      <source src={media.mp4} type="video/mp4" />
    </video>
  );
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
 * The sale's 2:1 loop in a black band that runs edge to edge under the hero, so it
 * reads as part of the page rather than a box. The render's background is black, and
 * its cropped left side and bottom fade into the band. The copy is in the video, so
 * the link carries the pill's line for screen readers.
 */
export function SaleBand() {
  const t = useTranslations("marketing");
  const sale = useActiveSale();
  const lastDay = useSaleLastDay(sale);
  if (!sale?.media) return null;
  return (
    <section className="bg-black" data-testid="sale-band">
      <a
        href="#pricing"
        aria-label={t("sale.pill", { percent: sale.percentOff, date: lastDay })}
        className="mx-auto block max-w-6xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      >
        <div className="[mask-image:linear-gradient(to_right,transparent,black_14%,black_100%)]">
          <div className="[mask-image:linear-gradient(to_bottom,black_80%,transparent)]">
            <SaleLoop media={sale.media} className="aspect-[2/1] w-full" />
          </div>
        </div>
      </a>
    </section>
  );
}

const noteDismissedKey = (sale: Sale) => `supoclip:sale-note-dismissed:${sale.id}`;

/** A small note in the bottom-left corner that stays while the visitor scrolls, until dismissed. */
export function SaleNote() {
  const t = useTranslations("marketing");
  const sale = useActiveSale();
  const lastDay = useSaleLastDay(sale);
  const [dismissed, setDismissed] = useState(true);
  useEffect(() => {
    if (!sale) return;
    try {
      setDismissed(window.localStorage.getItem(noteDismissedKey(sale)) === "1");
    } catch {
      setDismissed(false);
    }
  }, [sale]);
  if (!sale || dismissed) return null;
  const dismiss = () => {
    setDismissed(true);
    try {
      window.localStorage.setItem(noteDismissedKey(sale), "1");
    } catch {
      // Dismissal just won't persist.
    }
  };
  return (
    <div
      data-testid="sale-note"
      className="fixed bottom-4 left-4 z-50 flex w-[min(22rem,calc(100vw-2rem))] items-center gap-3 rounded-2xl border bg-background/95 p-2 shadow-lg backdrop-blur"
    >
      {sale.scene && <SaleLoop media={sale.scene} className="size-14 shrink-0 rounded-xl bg-black" />}
      <a href="#pricing" className="min-w-0 flex-1 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <p className="text-sm font-semibold tracking-tight">{t("sale.headline", { percent: sale.percentOff })}</p>
        <p className="text-xs text-muted-foreground">{t("sale.note", { date: lastDay })}</p>
      </a>
      <button
        type="button"
        onClick={dismiss}
        className="self-start rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
        aria-label={t("sale.dismiss")}
      >
        <X className="size-4" />
      </button>
    </div>
  );
}
