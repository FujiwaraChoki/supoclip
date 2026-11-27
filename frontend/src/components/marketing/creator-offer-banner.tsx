"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { X } from "lucide-react";
import { useTranslations } from "next-intl";

import { useActiveSale } from "@/components/marketing/sale";
import { captureCreatorCodeFromUrl } from "@/lib/creator-code";

const DISMISSED_KEY = "supoclip:creator-offer-dismissed";

/** Quiet note for visitors who arrived through a creator's link. */
export function CreatorOfferBanner() {
  const t = useTranslations("marketing");
  const [code, setCode] = useState<string | null>(null);
  // During a sale checkout applies the sale instead (it is the bigger discount), so this
  // note would promise the wrong price. The code is still remembered for attribution.
  const sale = useActiveSale();

  useEffect(() => {
    let dismissed = false;
    try {
      dismissed = window.sessionStorage.getItem(DISMISSED_KEY) === "1";
    } catch {
      // Storage unavailable: show the note.
    }
    if (!dismissed) void captureCreatorCodeFromUrl().then(setCode);
  }, []);

  if (!code || sale) return null;

  const dismiss = () => {
    setCode(null);
    try {
      window.sessionStorage.setItem(DISMISSED_KEY, "1");
    } catch {
      // Dismissal just won't persist.
    }
  };

  return (
    <div className="fixed inset-x-4 bottom-4 z-50 mx-auto flex max-w-md items-center gap-3 rounded-2xl border bg-background/95 py-3 pl-4 pr-2 text-sm shadow-lg backdrop-blur">
      <span className="size-1.5 shrink-0 rounded-full bg-brand" aria-hidden />
      <p className="min-w-0 flex-1 leading-snug">
        {t("creator.codeLead")} <span className="font-mono font-medium">{code}</span> {t("creator.codeTail")}{" "}
        <Link href="/sign-up" className="font-medium underline underline-offset-2">{t("creator.start")}</Link>
      </p>
      <button type="button" onClick={dismiss} className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground" aria-label={t("creator.dismiss")}>
        <X className="size-4" />
      </button>
    </div>
  );
}
