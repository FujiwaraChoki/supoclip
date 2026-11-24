"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Popover, PopoverAnchor, PopoverContent } from "@/components/ui/popover";
import { celebrate } from "@/lib/confetti";
import { track } from "@/lib/datafast";
import { crossesPages, dueTours, FEATURE_TOURS, TOUR_COOLDOWN_MS, type FeatureTour, type TourFlag, type TourStep } from "@/lib/feature-tours";
import type { Workflow } from "@/lib/workflows";
import { WorkflowGlyph } from "@/components/workflow-picker";

const LAST_SHOWN_KEY = "supoclip.featureTour.lastShown";
const SEEN_KEY = "supoclip.featureTour.seen";
const HANDOFF_KEY = "supoclip.featureTour.handoff";
const START_DELAY_MS = 900;
/** How long a page gets to show what a step points at before the step is passed over. */
const TARGET_WAIT_MS = 4000;
/** A handoff older than this is from an abandoned tab, not a page change. */
const HANDOFF_TTL_MS = 30_000;

type Running = { tour: FeatureTour; steps: TourStep[]; preview: boolean };
/** `steps` are indexes into the tour's steps: the ones it kept when it started. */
type Handoff = { id: string; steps: number[]; index: number; preview: boolean; path: string; direction: 1 | -1; counted: number; at: number };

function writeHandoff(handoff: Handoff) {
  try { window.sessionStorage.setItem(HANDOFF_KEY, JSON.stringify(handoff)); } catch { /* storage blocked: the tour ends at the page change */ }
}

function clearHandoff() {
  try { window.sessionStorage.removeItem(HANDOFF_KEY); } catch { /* nothing to clear */ }
}

/** The tour the page before sent here, read once. */
function takeHandoff(): Handoff | null {
  try {
    const raw = window.sessionStorage.getItem(HANDOFF_KEY);
    window.sessionStorage.removeItem(HANDOFF_KEY);
    const handoff = raw ? JSON.parse(raw) as Handoff : null;
    return handoff && typeof handoff.id === "string" && Number.isInteger(handoff.index) && Array.isArray(handoff.steps) && Date.now() - handoff.at < HANDOFF_TTL_MS ? handoff : null;
  } catch {
    return null;
  }
}

/** Room the phone's sticky header and tab bar take (desktop just gets a margin). */
const CLEAR_TOP = 72;
const CLEAR_BOTTOM = 88;

/** Scroll the target clear of the header and tab bar; one taller than the room is shown from its top. */
export function bringIntoView(element: HTMLElement) {
  const rect = element.getBoundingClientRect();
  // Sideways first: the workflow cards scroll horizontally on a phone.
  if (rect.left < 0 || rect.right > window.innerWidth) {
    element.scrollIntoView?.({ block: "nearest", inline: "center", behavior: "smooth" });
    return;
  }
  const bottom = window.innerHeight - CLEAR_BOTTOM;
  const delta = rect.height > bottom - CLEAR_TOP || rect.top < CLEAR_TOP ? rect.top - CLEAR_TOP : rect.bottom > bottom ? rect.bottom - bottom : 0;
  if (delta !== 0) window.scrollBy?.({ top: delta, behavior: "smooth" });
}

/**
 * Where the card is placed: the target's box cut to the screen, and at most the top part of a tall
 * one, so the card fits below it instead of covering it or the tab bar.
 */
function visiblePart(element: HTMLElement): DOMRect {
  const rect = element.getBoundingClientRect();
  const top = Math.max(rect.top, 0);
  const bottom = Math.min(rect.bottom, window.innerHeight, top + window.innerHeight * 0.4);
  return bottom > top ? new DOMRect(rect.left, top, rect.width, bottom - top) : rect;
}

/** Whether this step is on the page being shown. */
function onPage(step: TourStep, pathname: string): boolean {
  return step.path === undefined || step.path === pathname;
}

/** The steps that count towards "3 of 9": not the opening question. */
function countedSteps(steps: readonly TourStep[]): TourStep[] {
  return steps.filter((step) => !step.intro);
}

/**
 * Whether what a step requires is in the document. Checked on a page that has settled (where the tour
 * starts, and before leaving for the step's page), never on arrival: a fresh page's sidebar is still loading.
 */
function requirementMet(step: TourStep): boolean {
  return !step.requires || document.querySelector(`[data-tour="${step.requires}"]`) !== null;
}

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

const richText = { b: (chunks: ReactNode) => <strong className="font-semibold text-foreground">{chunks}</strong> };

/** Ways the page can bring a hidden target on screen (a closed settings menu, another inspector tab). */
type Openers = {
  onSettingsSection?: (id: string) => void;
  onInspectorTab?: (id: string) => void;
};

/** A step the tour can show: its target is on the page, or the page can open where it lives. */
function reachable(step: TourStep, openers: Openers): boolean {
  if (targetOf(step)) return true;
  return Boolean((step.settingsSection && openers.onSettingsSection) || (step.inspectorTab && openers.onInspectorTab));
}

/** The first visible, enabled element for this step: the sidebar and the mobile tab bar can carry the same target. */
function targetOf(step: TourStep): HTMLElement | null {
  const elements = document.querySelectorAll<HTMLElement>(`[data-tour="${step.target}"]`);
  return Array.from(elements).find((element) => element.getClientRects().length > 0 && !element.matches(":disabled")) ?? null;
}

/** Beside a sidebar link on desktop; above it in the mobile tab bar, where there's no room beside it. */
function sideOf(step: TourStep): NonNullable<TourStep["side"]> {
  const side = step.side ?? "bottom";
  const narrow = window.matchMedia?.("(max-width: 767px)").matches ?? false;
  return narrow && (side === "left" || side === "right") ? "top" : side;
}

/** `?tour=<id>` replays a tour for checking it, ignoring who has seen what. */
function requestedTour(): FeatureTour | null {
  const id = new URLSearchParams(window.location.search).get("tour");
  return FEATURE_TOURS.find((tour) => tour.id === id) ?? null;
}

/** Dims everything but the target, without blocking clicks. It follows the target as the page moves. */
export function Spotlight({ target }: { target: HTMLElement }) {
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
 * Walks through the real page, or several: a welcome for new accounts, or what's new for everyone
 * else. It opens by asking, can be left at any step, shows once per account, and steps aside as
 * soon as the user gets to work or goes somewhere else.
 *
 * Mounted by the app shell on every signed-in page. A tour only starts on the create screen; a tour
 * that crosses pages hands itself to the next page's host through session storage.
 */
export function FeatureTourHost({ enabled, pathname, navigate, canStart = false, busy = false, onWorkflow, onEnd, onSettingsSection, onInspectorTab, skip }: {
  enabled: boolean;
  /** Tours this account never gets here (the Pebble onboarding group skips `welcome`). */
  skip?: readonly string[];
  /** The page this host is on. */
  pathname: string;
  /** Go to another page for the next step. */
  navigate: (path: string) => void;
  /** New tours start here (the create screen). Elsewhere a host only carries on a tour in progress. */
  canStart?: boolean;
  /** The user is doing something: don't start, and leave if already showing. */
  busy?: boolean;
  /** Show this workflow while a step talks about it. */
  onWorkflow?: (workflow: Workflow) => void;
  /** The tour is over: put back what it changed. */
  onEnd?: () => void;
  /** Open this settings section (a step's `settingsSection`) before its target is measured. Must be idempotent. */
  onSettingsSection?: (id: string) => void;
  /** Open this inspector tab (a step's `inspectorTab`) before its target is measured. Must be idempotent. */
  onInspectorTab?: (id: string) => void;
}) {
  const t = useTranslations("tours");
  const [account, setAccount] = useState<{ createdAt: string; seen: string[] } | null>(null);
  // Flags a tour can wait for; null until asked (a failure reads as every flag off).
  const [flags, setFlags] = useState<Partial<Record<TourFlag, boolean>> | null>(null);
  const openers = useRef<Openers>({ onSettingsSection, onInspectorTab });
  useEffect(() => { openers.current = { onSettingsSection, onInspectorTab }; }, [onSettingsSection, onInspectorTab]);
  const [running, setRunning] = useState<Running | null>(null);
  const [index, setIndex] = useState(0);
  const [target, setTarget] = useState<HTMLElement | null>(null);
  const started = useRef(false);
  const showMe = useRef<HTMLButtonElement>(null);
  /** Which way the user is going, so a step with nothing to show is passed in that direction. */
  const direction = useRef<1 | -1>(1);
  /** The page this host sent the tour to, so arriving there isn't mistaken for leaving. */
  const travelling = useRef<string | null>(null);
  /** The step last found on its own page: if the page then changes, the user left, and the tour doesn't follow them back. */
  const placed = useRef<TourStep | null>(null);
  /** When the tour arrived on this page: it gets a moment to load what a step points at. The page a tour starts on is loaded already. */
  const arrivedAt = useRef(0);

  const runningRef = useRef(running);
  const indexRef = useRef(index);
  useEffect(() => { runningRef.current = running; indexRef.current = index; }, [running, index]);

  // Marks the page while a tour runs.
  useEffect(() => {
    if (!running) return;
    document.body.setAttribute("data-feature-tour", running.tour.id);
    return () => document.body.removeAttribute("data-feature-tour");
  }, [running]);

  const end = useCallback((how: "completed" | "dismissed", restore = true) => {
    const current = runningRef.current;
    if (current && !current.preview) {
      const step = countedSteps(current.steps).indexOf(current.steps[indexRef.current]) + 1;
      record(current.tour.id, how, step);
      track(how === "completed" ? "feature_tour_finished" : "feature_tour_dismissed", { tour: current.tour.id, step });
    }
    clearHandoff();
    travelling.current = null;
    setRunning(null);
    setTarget(null);
    if (restore) onEnd?.();
  }, [onEnd]);

  // A tour carried over from the page before.
  useEffect(() => {
    if (!enabled || started.current) return;
    const handoff = takeHandoff();
    if (!handoff) return;
    const tour = FEATURE_TOURS.find((entry) => entry.id === handoff.id);
    if (!tour) return;
    started.current = true;
    if (handoff.path !== pathname) {
      // They went somewhere else while the tour was on its way: that's leaving it.
      if (!handoff.preview) {
        record(tour.id, "dismissed", handoff.counted);
        track("feature_tour_dismissed", { tour: tour.id, step: handoff.counted });
      }
      return;
    }
    direction.current = handoff.direction;
    arrivedAt.current = Date.now();
    const steps = handoff.steps.map((position) => tour.steps[position]).filter(Boolean);
    if (!steps[handoff.index]) return;
    setRunning({ tour, steps, preview: handoff.preview });
    setIndex(handoff.index);
  }, [enabled, pathname]);

  // The page is left (another layout takes over) while a step is showing and not on its way to the next page.
  useEffect(() => () => {
    const current = runningRef.current;
    if (!current || current.preview || travelling.current !== null) return;
    const step = countedSteps(current.steps).indexOf(current.steps[indexRef.current]) + 1;
    record(current.tour.id, "dismissed", step);
    track("feature_tour_dismissed", { tour: current.tour.id, step });
  }, []);

  useEffect(() => {
    if (!enabled || !canStart) return;
    let cancelled = false;
    void fetch("/api/feature-tours", { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => { if (!cancelled && data) setAccount({ createdAt: data.createdAt, seen: Array.isArray(data.seen) ? data.seen : [] }); })
      .catch(() => {});
    void fetch("/api/feature-flags", { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        if (cancelled) return;
        // `{"platform", "flags": {key: bool}}`: only an explicit true turns a flag on.
        const source: unknown = data?.flags;
        const known: Partial<Record<TourFlag, boolean>> = {};
        if (source && typeof source === "object") for (const [key, value] of Object.entries(source)) if (value === true) known[key as TourFlag] = true;
        setFlags(known);
      })
      .catch(() => { if (!cancelled) setFlags({}); });
    return () => { cancelled = true; };
  }, [enabled, canStart]);

  useEffect(() => {
    if (!account || !flags || !canStart || busy || started.current) return;
    const timer = setTimeout(() => {
      if (started.current) return;
      const preview = requestedTour();
      const recent = Date.now() - Number(readLocal(LAST_SHOWN_KEY) ?? 0) < TOUR_COOLDOWN_MS;
      if (!preview && recent) return;
      const candidates = preview
        ? [preview]
        // A gated feature's tour waits until the feature is on for this account.
        : dueTours(account.createdAt, [...account.seen, ...seenOnDevice()]).filter((tour) => (!tour.requiresFlag || flags[tour.requiresFlag] === true) && !skip?.includes(tour.id));
      // A tour with nothing to show waits: its feature may not be open to this account yet.
      for (const tour of candidates) {
        const here = tour.steps.filter((step) => onPage(step, pathname) && reachable(step, openers.current));
        if (!here.some((step) => !step.intro)) continue;
        // Without its opening there's no feature to introduce yet: save it, don't spend it on the leftovers.
        if (tour.steps.some((step) => step.intro) && !here.some((step) => step.intro)) continue;
        // A tour across pages keeps the steps for other pages: those are looked for when it gets there.
        const steps = crossesPages(tour) ? tour.steps.filter((step) => (!onPage(step, pathname) || here.includes(step)) && requirementMet(step)) : here;
        // An opener only promises a later step; the first one must be on the page, or the tour would be spent showing nothing.
        if (!steps[0] || (onPage(steps[0], pathname) && !targetOf(steps[0]))) continue;
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
  }, [account, flags, busy, canStart, pathname, skip]);

  useEffect(() => {
    // They started working: keep what they have selected.
    if (busy && runningRef.current) end("dismissed", false);
  }, [busy, end]);

  // Arriving where the tour sent us is fine; any other page change means they left the tour.
  useEffect(() => {
    const current = runningRef.current;
    if (!current) return;
    if (travelling.current === pathname) {
      travelling.current = null;
      arrivedAt.current = Date.now();
      clearHandoff();
      return;
    }
    if (travelling.current === null && !onPage(current.steps[indexRef.current], pathname)) end("dismissed", false);
  }, [pathname, end]);

  const step = running?.steps[index];
  // While a step points into the settings menu, the menu stays open under it (composer-settings). Other steps and
  // other tours leave it alone: a click outside closes it as always.
  const inSettingsMenu = Boolean(step?.settingsSection);
  useEffect(() => {
    if (!inSettingsMenu) return;
    document.body.setAttribute("data-feature-tour-settings", "");
    return () => document.body.removeAttribute("data-feature-tour-settings");
  }, [inSettingsMenu]);
  useEffect(() => {
    if (step?.workflow) onWorkflow?.(step.workflow);
  }, [step, onWorkflow]);

  /** Pass a step there's nothing for, the way the user was going. */
  const pass = useCallback(() => {
    const current = runningRef.current;
    if (!current) return;
    const nextIndex = indexRef.current + direction.current;
    if (nextIndex < 0) { direction.current = 1; setIndex(indexRef.current + 1); return; }
    if (nextIndex >= current.steps.length) { end("completed"); return; }
    setIndex(nextIndex);
  }, [end]);

  // Find what this step points at: on this page, after going to its page, or not at all.
  useEffect(() => {
    setTarget(null);
    if (!running || !step) return;
    if (!onPage(step, pathname)) {
      if (placed.current === step) return;
      if (!requirementMet(step)) { pass(); return; }
      const path = step.path!;
      const counted = countedSteps(running.steps).indexOf(step) + 1;
      writeHandoff({ id: running.tour.id, steps: running.steps.map((entry) => running.tour.steps.indexOf(entry)), index, preview: running.preview, path, direction: direction.current, counted, at: Date.now() });
      travelling.current = path;
      navigate(path);
      return;
    }
    placed.current = step;
    // Pages load their content after they appear: give the target a moment.
    const waitUntil = arrivedAt.current + TARGET_WAIT_MS;
    let timer: ReturnType<typeof setTimeout>;
    // A target in a closed settings menu or another inspector tab: ask the page to open it (idempotent), and again
    // while it is missing. A menu can close under the tour; then the card hides and the target is looked for anew.
    const open = () => {
      if (step.settingsSection) openers.current.onSettingsSection?.(step.settingsSection);
      if (step.inspectorTab) openers.current.onInspectorTab?.(step.inspectorTab);
    };
    const opens = Boolean(step.settingsSection || step.inspectorTab);
    let lostAt: number | null = null;
    const look = () => {
      const found = targetOf(step);
      if (found) {
        lostAt = null;
        setTarget((current) => (current === found ? current : found));
        if (opens) timer = setTimeout(look, 250);
        return;
      }
      setTarget(null);
      if (opens) open();
      lostAt ??= Date.now();
      if (Date.now() >= Math.max(waitUntil, lostAt + TARGET_WAIT_MS)) { pass(); return; }
      timer = setTimeout(look, 120);
    };
    if (opens && !step.intro) open();
    look();
    return () => clearTimeout(timer);
  }, [running, step, index, pathname, navigate, pass]);

  useEffect(() => {
    if (target) bringIntoView(target);
  }, [target]);
  // The card sits beside the part of the target that's on screen, so a tall one can't push it off.
  const anchor = useMemo(() => (target ? { getBoundingClientRect: () => visiblePart(target) } : null), [target]);

  const next = (event?: { currentTarget: HTMLElement }) => {
    direction.current = 1;
    if (running && index < running.steps.length - 1) {
      setIndex(index + 1);
      return;
    }
    const rect = event?.currentTarget.getBoundingClientRect();
    celebrate(rect ? { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 } : undefined);
    end("completed");
  };
  const back = () => {
    direction.current = -1;
    setIndex(index - 1);
  };

  if (!running || !step || !target) return null;
  // The opening question isn't one of the steps being counted.
  const counted = countedSteps(running.steps);
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
            <span className="inline-flex rounded-full bg-brand/10 px-2 py-0.5 text-[11px] font-medium text-brand">{running.tour.audience === "new" ? t("welcomeBadge") : t("new")}</span>
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
        <PopoverAnchor virtualRef={{ current: anchor! }} />
        <PopoverContent
          side={sideOf(step)}
          sideOffset={14}
          collisionPadding={12}
          sticky="always"
          role="status"
          className="w-[min(90vw,340px)] rounded-2xl p-4 shadow-xl"
          onOpenAutoFocus={(event) => event.preventDefault()}
          onCloseAutoFocus={(event) => event.preventDefault()}
          onInteractOutside={(event) => event.preventDefault()}
        >
          {step.workflow && <WorkflowGlyph id={step.workflow} className="mb-3" />}
          <p className="text-sm font-semibold">{t(`${keys}.title`)}</p>
          {/* Copy may set terms in <b> and put items on their own lines. */}
          <p className="mt-1 whitespace-pre-line text-sm text-muted-foreground">{t.rich(`${keys}.body`, richText)}</p>
          <div className="mt-3 flex items-center justify-between gap-2">
              {/* Always a quiet way out, without competing with Next. */}
              <button type="button" onClick={() => end("dismissed")} className="-ml-1 rounded-md px-1 py-1 text-xs text-muted-foreground/70 transition-colors hover:text-foreground">{t("skip")}</button>
              <span className="flex items-center gap-1">
                {counted.length > 1 && <span className="mr-1 text-xs tabular-nums text-muted-foreground">{t("progress", { current: position, total: counted.length })}</span>}
                {position > 1 && <Button type="button" variant="ghost" size="sm" className="h-8 rounded-full px-3 text-xs" onClick={back}>{t("back")}</Button>}
                <Button type="button" size="sm" className="h-8 rounded-full px-4 text-xs" onClick={next}>{index < running.steps.length - 1 ? t("next") : t("done")}</Button>
              </span>
          </div>
        </PopoverContent>
      </Popover>
    </>
  );
}
