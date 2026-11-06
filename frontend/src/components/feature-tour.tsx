"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Popover, PopoverAnchor, PopoverContent } from "@/components/ui/popover";
import { celebrate } from "@/lib/confetti";
import { track } from "@/lib/datafast";
import { dueTours, FEATURE_TOURS, TOUR_COOLDOWN_MS, type FeatureTour, type TourStep } from "@/lib/feature-tours";
import type { Workflow } from "@/lib/workflows";
import { WorkflowGlyph } from "@/components/workflow-picker";

const LAST_SHOWN_KEY = "supoclip.featureTour.lastShown";
const SEEN_KEY = "supoclip.featureTour.seen";
const START_DELAY_MS = 900;

function readLocal(key: string): string | null {
  try { return window.localStorage.getItem(key); } catch { return null; }
}

function writeLocal(key: string, value: string) {
  try { window.localStorage.setItem(key, value); } catch { /* storage blocked: the account still records it */ }
}

function seenOnDevice(): string[] {
  try {
    const parsed = JSON.parse(readLocal(SEEN_KEY) ?? "[]");
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function record(id: string, event: "shown" | "completed" | "dismissed", step?: number) {
  void fetch("/api/feature-tours", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id, event, step }), keepalive: true }).catch(() => {});
}

function targetOf(step: TourStep): HTMLElement | null {
  const element = document.querySelector<HTMLElement>(`[data-tour="${step.target}"]`);
  return element && element.getClientRects().length > 0 && !element.matches(":disabled") ? element : null;
}

/** `?tour=<id>` replays a tour for checking it, ignoring who has seen what. */
function requestedTour(): FeatureTour | null {
  const id = new URLSearchParams(window.location.search).get("tour");
  return FEATURE_TOURS.find((tour) => tour.id === id) ?? null;
}

/** Dims everything but the target, without blocking clicks. It follows the target as the page moves. */
function Spotlight({ target }: { target: HTMLElement }) {
  const [box, setBox] = useState<DOMRect | null>(null);
  useEffect(() => {
    let frame = 0;
    const follow = () => {
      const rect = target.getBoundingClientRect();
      setBox((current) => (current && current.x === rect.x && current.y === rect.y && current.width === rect.width && current.height === rect.height ? current : rect));
      frame = requestAnimationFrame(follow);
    };
    follow();
    return () => cancelAnimationFrame(frame);
  }, [target]);
  if (!box) return null;
  const pad = 6;
  // On the body: a transformed parent would otherwise position it relative to itself.
  return createPortal(
    <div
      aria-hidden
      className="pointer-events-none fixed z-40 rounded-2xl ring-2 ring-brand/70 transition-all duration-300 ease-out motion-reduce:transition-none"
      style={{ left: box.x - pad, top: box.y - pad, width: box.width + pad * 2, height: box.height + pad * 2, boxShadow: "0 0 0 100vmax rgb(0 0 0 / 0.32)" }}
    />,
    document.body,
  );
}

type Media = NonNullable<FeatureTour["media"]>;
type Variant = { slug: string; name: string };

/** The opening announcement's loop, rendered in Blender (assets/blender/announcement/). */
function AnnouncementMedia({ media, variantsUrl }: { media: Media; /** Previews only: offer every candidate render to compare. */ variantsUrl?: string }) {
  const [variants, setVariants] = useState<Variant[]>([]);
  const [chosen, setChosen] = useState<string | null>(null);
  useEffect(() => {
    if (!variantsUrl) return;
    let cancelled = false;
    void fetch(variantsUrl)
      .then((response) => (response.ok ? response.json() : []))
      .then((data) => { if (!cancelled && Array.isArray(data)) setVariants(data.filter((entry) => typeof entry?.slug === "string")); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [variantsUrl]);
  const base = variantsUrl?.replace(/[^/]*$/, "");
  const shown: Media = chosen && base
    ? { poster: `${base}${chosen}.webp`, videos: [{ src: `${base}${chosen}.webm`, type: "video/webm" }, { src: `${base}${chosen}.mp4`, type: "video/mp4" }] }
    : media;
  return (
    <div className="relative aspect-[2/1] overflow-hidden bg-[#020202]">
      <video
        // A new render needs a new element: browsers don't reload changed <source> children.
        key={shown.poster}
        className="size-full object-cover"
        poster={shown.poster}
        autoPlay
        loop
        muted
        playsInline
        preload="auto"
        aria-hidden
        // Reduced motion: the poster stays still.
        ref={(node) => { if (node && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) node.pause(); }}
      >
        {shown.videos.map((video) => <source key={video.src} src={video.src} type={video.type} />)}
      </video>
      {variants.length > 0 && (
        <select
          aria-label="Render"
          value={chosen ?? ""}
          onChange={(event) => setChosen(event.target.value || null)}
          className="absolute right-3 top-3 h-8 max-w-[60%] rounded-full border border-white/20 bg-black/55 px-3 text-xs text-white backdrop-blur"
        >
          <option value="">Current</option>
          {variants.map((variant, index) => <option key={variant.slug} value={variant.slug}>{index + 1}. {variant.name}</option>)}
        </select>
      )}
    </div>
  );
}

/**
 * Walks through what's new on the real page. It opens by asking, can be left at any
 * step, shows once per account, and steps aside as soon as the user gets to work.
 */
export function FeatureTourHost({ enabled, busy, onWorkflow, onEnd }: {
  enabled: boolean;
  /** The user is doing something: don't start, and leave if already showing. */
  busy: boolean;
  /** Show this workflow while a step talks about it. */
  onWorkflow?: (workflow: Workflow) => void;
  /** The tour is over: put back what it changed. */
  onEnd?: () => void;
}) {
  const t = useTranslations("tours");
  const [account, setAccount] = useState<{ createdAt: string; seen: string[] } | null>(null);
  const [running, setRunning] = useState<{ tour: FeatureTour; steps: TourStep[]; preview: boolean } | null>(null);
  const [index, setIndex] = useState(0);
  const started = useRef(false);
  const showMe = useRef<HTMLButtonElement>(null);

  const runningRef = useRef(running);
  const indexRef = useRef(index);
  useEffect(() => { runningRef.current = running; indexRef.current = index; }, [running, index]);

  const end = useCallback((how: "completed" | "dismissed", restore = true) => {
    const current = runningRef.current;
    if (current && !current.preview) {
      const step = current.steps.filter((entry) => !entry.intro).indexOf(current.steps[indexRef.current]) + 1;
      record(current.tour.id, how, step);
      track(how === "completed" ? "feature_tour_finished" : "feature_tour_dismissed", { tour: current.tour.id, step });
    }
    setRunning(null);
    if (restore) onEnd?.();
  }, [onEnd]);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    void fetch("/api/feature-tours", { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => { if (!cancelled && data) setAccount({ createdAt: data.createdAt, seen: Array.isArray(data.seen) ? data.seen : [] }); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [enabled]);

  useEffect(() => {
    if (!account || busy || started.current) return;
    const timer = setTimeout(() => {
      const preview = requestedTour();
      const recent = Date.now() - Number(readLocal(LAST_SHOWN_KEY) ?? 0) < TOUR_COOLDOWN_MS;
      if (!preview && recent) return;
      const candidates = preview ? [preview] : dueTours(account.createdAt, [...account.seen, ...seenOnDevice()]);
      // A tour with nothing to show waits: its feature may not be open to this account yet.
      for (const tour of candidates) {
        const steps = tour.steps.filter((step) => targetOf(step));
        if (!steps.some((step) => !step.intro)) continue;
        // Without its opening there's no feature to introduce yet: save it, don't spend it on the leftovers.
        if (tour.steps.some((step) => step.intro) && !steps.some((step) => step.intro)) continue;
        started.current = true;
        setRunning({ tour, steps, preview: Boolean(preview) });
        setIndex(0);
        if (!preview) {
          track("feature_tour_shown", { tour: tour.id });
          writeLocal(LAST_SHOWN_KEY, String(Date.now()));
          writeLocal(SEEN_KEY, JSON.stringify([...seenOnDevice(), tour.id]));
          record(tour.id, "shown");
        }
        return;
      }
    }, START_DELAY_MS);
    return () => clearTimeout(timer);
  }, [account, busy]);

  useEffect(() => {
    // They started working: keep what they have selected.
    if (busy && runningRef.current) end("dismissed", false);
  }, [busy, end]);

  const step = running?.steps[index];
  useEffect(() => {
    if (step?.workflow) onWorkflow?.(step.workflow);
  }, [step, onWorkflow]);
  const target = useMemo(() => (step ? targetOf(step) : null), [step]);
  useEffect(() => {
    target?.scrollIntoView?.({ block: "nearest", inline: "center", behavior: "smooth" });
  }, [target]);

  const next = (event?: { currentTarget: HTMLElement }) => {
    if (running && index < running.steps.length - 1) {
      setIndex(index + 1);
      return;
    }
    const rect = event?.currentTarget.getBoundingClientRect();
    celebrate(rect ? { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 } : undefined);
    end("completed");
  };

  if (!running || !step || !target) return null;
  // The opening question isn't one of the steps being counted.
  const counted = running.steps.filter((entry) => !entry.intro);
  const position = counted.indexOf(step) + 1;
  const keys = `${running.tour.id}.${step.message}`;

  if (step.intro) {
    return (
      <Dialog open onOpenChange={(open) => { if (!open) end("dismissed"); }}>
        <DialogContent
          showCloseButton={false}
          className="gap-0 overflow-hidden rounded-3xl p-0 sm:max-w-lg"
          // Start on the answer most people want, not on the way out.
          onOpenAutoFocus={(event) => { event.preventDefault(); showMe.current?.focus(); }}
        >
          {running.tour.media && <AnnouncementMedia media={running.tour.media} variantsUrl={running.preview ? running.tour.media.variants : undefined} />}
          <div className="px-6 pb-6 pt-5">
            <span className="inline-flex rounded-full bg-brand/10 px-2 py-0.5 text-[11px] font-medium text-brand">{t("new")}</span>
            <DialogTitle className="mt-2 font-display text-xl tracking-tight">{t(`${keys}.title`)}</DialogTitle>
            <DialogDescription className="mt-1.5 text-sm">{t(`${keys}.body`)}</DialogDescription>
            <div className="mt-5 flex items-center justify-end gap-2">
              <Button type="button" variant="ghost" className="rounded-full" onClick={() => end("dismissed")}>{t("notNow")}</Button>
              <Button ref={showMe} type="button" className="rounded-full px-5" onClick={next}>{t("start")}</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    );
  }

  return (
    <>
      <Spotlight target={target} />
      <Popover open onOpenChange={(open) => { if (!open) end("dismissed"); }} modal={false}>
        <PopoverAnchor virtualRef={{ current: target }} />
        <PopoverContent
          side={step.side ?? "bottom"}
          sideOffset={14}
          collisionPadding={12}
          role="status"
          className="w-[min(90vw,340px)] rounded-2xl p-4 shadow-xl"
          onOpenAutoFocus={(event) => event.preventDefault()}
          onCloseAutoFocus={(event) => event.preventDefault()}
          onInteractOutside={(event) => event.preventDefault()}
        >
          {step.workflow && <WorkflowGlyph id={step.workflow} className="mb-3" />}
          <p className="text-sm font-semibold">{t(`${keys}.title`)}</p>
          <p className="mt-1 text-sm text-muted-foreground">{t(`${keys}.body`)}</p>
          <div className="mt-3 flex items-center justify-between gap-2">
              {/* Always a quiet way out, without competing with Next. */}
              <button type="button" onClick={() => end("dismissed")} className="-ml-1 rounded-md px-1 py-1 text-xs text-muted-foreground/70 transition-colors hover:text-foreground">{t("skip")}</button>
              <span className="flex items-center gap-1">
                {counted.length > 1 && <span className="mr-1 text-xs tabular-nums text-muted-foreground">{t("progress", { current: position, total: counted.length })}</span>}
                {position > 1 && <Button type="button" variant="ghost" size="sm" className="h-8 rounded-full px-3 text-xs" onClick={() => setIndex(index - 1)}>{t("back")}</Button>}
                <Button type="button" size="sm" className="h-8 rounded-full px-4 text-xs" onClick={next}>{index < running.steps.length - 1 ? t("next") : t("done")}</Button>
              </span>
          </div>
        </PopoverContent>
      </Popover>
    </>
  );
}
