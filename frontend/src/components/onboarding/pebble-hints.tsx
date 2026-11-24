"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { AnimatePresence, motion } from "motion/react";
import { X } from "lucide-react";

import { AgentMark } from "@/components/agent/agent-mark";
import { Spotlight, bringIntoView } from "@/components/feature-tour";
import { usePrefersReducedMotion } from "@/hooks/use-prefers-reduced-motion";
import { trackEvent } from "@/lib/analytics";
import { PEBBLE_HINTS, type PebbleHint } from "@/lib/pebble-hints";

const SEEN_KEY = "supoclip.pebble.hints";

function seenOnDevice(): string[] {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(SEEN_KEY) ?? "[]");
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function remember(id: string, event: "shown" | "completed" | "dismissed") {
  try { window.localStorage.setItem(SEEN_KEY, JSON.stringify([...new Set([...seenOnDevice(), id])])); } catch { /* storage blocked */ }
  void fetch("/api/feature-tours", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id, event }), keepalive: true }).catch(() => {});
}

/**
 * Agent pops up the first time a page is opened, points at one thing and says what it's for.
 * One hint at a time, after the page has settled, never while the onboarding guide is talking.
 */
export function PebbleHints({ pathname, enabled }: { pathname: string; enabled: boolean }) {
  const t = useTranslations("onboarding.hints");
  const reduced = usePrefersReducedMotion();
  const [seen, setSeen] = useState<string[] | null>(null);
  const [hint, setHint] = useState<PebbleHint | null>(null);
  const [target, setTarget] = useState<HTMLElement | null>(null);
  const [typed, setTyped] = useState(0);
  const shownHere = useRef<string | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    void fetch("/api/feature-tours", { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => { if (!cancelled) setSeen([...(Array.isArray(data?.seen) ? data.seen : []), ...seenOnDevice()]); })
      .catch(() => { if (!cancelled) setSeen(seenOnDevice()); });
    return () => { cancelled = true; };
  }, [enabled]);

  const candidate = useMemo(
    () => (seen ? PEBBLE_HINTS.find((entry) => entry.matches(pathname) && !seen.includes(entry.id)) ?? null : null),
    [pathname, seen],
  );

  useEffect(() => {
    setHint(null);
    setTarget(null);
    if (!enabled || !candidate || shownHere.current === pathname) return;
    let tries = 0;
    let timer = 0;
    const look = () => {
      const element = Array.from(document.querySelectorAll<HTMLElement>(`[data-tour="${candidate.target}"]`)).find((node) => node.getClientRects().length > 0);
      if (element) {
        shownHere.current = pathname;
        bringIntoView(element);
        setTarget(element);
        setHint(candidate);
        setTyped(0);
        remember(candidate.id, "shown");
        trackEvent("pebble_hint_shown", { page: candidate.id.replace("pebble-", "") });
        return;
      }
      if (++tries < 16) timer = window.setTimeout(look, 250);
    };
    // Let the page settle first.
    timer = window.setTimeout(look, 1200);
    return () => window.clearTimeout(timer);
  }, [candidate, enabled, pathname]);

  const text = hint ? t(hint.message) : "";
  useEffect(() => {
    if (!hint) return;
    if (reduced) { setTyped(text.length); return; }
    const timer = window.setInterval(() => setTyped((count) => {
      if (count >= text.length) { window.clearInterval(timer); return count; }
      return count + 3;
    }), 16);
    return () => window.clearInterval(timer);
  }, [hint, reduced, text]);

  const close = (completed: boolean) => {
    if (hint) {
      remember(hint.id, completed ? "completed" : "dismissed");
      setSeen((current) => [...(current ?? []), hint.id]);
    }
    setHint(null);
    setTarget(null);
  };

  return (
    <>
      {hint && target && <Spotlight target={target} />}
      <AnimatePresence>
        {hint && (
          <motion.aside
            key={hint.id}
            data-pebble-dock
            role="dialog"
            aria-label={t("label")}
            initial={{ opacity: 0, y: 20, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 16, scale: 0.96 }}
            transition={{ type: "spring", stiffness: 320, damping: 28 }}
            className="fixed inset-x-3 bottom-[calc(4.75rem+env(safe-area-inset-bottom))] z-50 rounded-3xl border bg-background/95 p-4 shadow-[0_30px_80px_-30px_rgb(28_25_23/0.6)] backdrop-blur-xl md:inset-x-auto md:bottom-6 md:right-6 md:w-[22rem]"
          >
            <div className="flex items-start gap-3">
              <AgentMark size="sm" mood={typed < text.length ? "listening" : "happy"} />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold">{t(`${hint.message}Title`)}</p>
                <p className="mt-1 text-[13.5px] leading-relaxed text-foreground/85">
                  {text.slice(0, typed)}
                  {typed < text.length && <span className="ml-0.5 inline-block h-[1em] w-[2px] translate-y-[2px] animate-pulse bg-foreground/60" />}
                </p>
              </div>
              <button type="button" onClick={() => close(false)} aria-label={t("close")} className="-mr-1 -mt-1 rounded-full p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground">
                <X className="size-4" />
              </button>
            </div>
            <div className="mt-3 flex justify-end">
              <button type="button" onClick={() => close(true)} className="h-8 rounded-full bg-foreground px-4 text-[13px] font-medium text-background hover:opacity-90">
                {t("gotIt")}
              </button>
            </div>
          </motion.aside>
        )}
      </AnimatePresence>
    </>
  );
}
