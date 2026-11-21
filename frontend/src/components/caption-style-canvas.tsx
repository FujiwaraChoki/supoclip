"use client";

// The animated caption tile: drawn live by fx-core (the renderer the server uses) over the procedural demo backdrop.
// One shared clock drives every visible tile; with reduced motion each tile is one still frame. This module carries
// the whole renderer, so it is only loaded (by caption-style-tiles.tsx) when an animated tile is on the page.
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import { getDemoLayers } from "@/components/caption-style-tiles";
import { createBrowserHost, loadFxFont, type BrowserHost } from "@/lib/fx/browser-host";
import { loadFxAtlas } from "@/lib/fx/assets-client";
import { clockAt } from "@/lib/fx/core/frame";
import { renderFrame } from "@/lib/fx/core/render";
import { prepareScene, type PreparedScene } from "@/lib/fx/core/scene";
import type { EmojiAtlas, FxCanvas, FxContext2D } from "@/lib/fx/core/host";
import type { FxCaptionTemplate } from "@/lib/fx/core/types";
import { DEMO_DURATION, DEMO_FONTS, DEMO_HEIGHT, DEMO_STATIC_T, DEMO_WIDTH, demoScene, TILE_CAPTION_SCALE } from "@/lib/fx/demo-scene";
import { cn } from "@/lib/utils";

/** Tiles redraw at most this often: plenty for a preview, and ten of them stay cheap. */
const FRAME_INTERVAL_MS = 1000 / 30;

// ----------------------------------------------------------------------------------------------- shared stage

type Stage = {
  host: BrowserHost;
  /** Bumped when fonts or the emoji atlas arrive: scenes are measured again and still tiles redraw. */
  version: number;
};

let stage: Stage | null = null;
let stageVersion = 0;
const stageListeners = new Set<() => void>();

function bumpStage() {
  stageVersion += 1;
  if (stage) stage = { ...stage, version: stageVersion };
  for (const listener of stageListeners) listener();
}

function getStage(): Stage {
  if (stage) return stage;
  const { backdrop, person } = getDemoLayers();
  let atlas: EmojiAtlas | null = null;
  const drawLayer = (ctx: FxContext2D, layer: FxCanvas | null) => {
    if (layer) ctx.drawImage(layer as unknown as CanvasImageSource, 0, 0, DEMO_WIDTH, DEMO_HEIGHT);
  };
  const host = createBrowserHost({
    drawBase: (ctx) => { drawLayer(ctx, backdrop); drawLayer(ctx, person); },
    drawCutout: (ctx) => drawLayer(ctx, person),
    atlas: () => atlas,
  });
  stage = { host, version: stageVersion };
  // Fonts and emoji arrive in the background; until then tiles draw with fallbacks (and without emoji).
  for (const font of DEMO_FONTS) void loadFxFont(font).then(bumpStage, () => {});
  void loadFxAtlas().then((loaded) => { atlas = loaded; bumpStage(); }, () => {});
  return stage;
}

let serverStage: Stage | null = null;
/** Server render: no canvases, fonts or fetches; tiles draw once they reach the browser. */
function getServerStage(): Stage {
  serverStage ??= { host: createBrowserHost({ drawBase: () => {}, supportsFilter: false }), version: -1 };
  return serverStage;
}

function useStage(): Stage {
  return useSyncExternalStore(
    (listener) => { stageListeners.add(listener); return () => { stageListeners.delete(listener); }; },
    getStage,
    getServerStage,
  );
}

// ----------------------------------------------------------------------------------------------- shared clock

type Painter = (seconds: number) => void;
const painters = new Set<Painter>();
let frame = 0;
let lastPaint = 0;
const epoch = typeof performance !== "undefined" ? performance.now() : 0;

function tick(now: number) {
  frame = painters.size ? requestAnimationFrame(tick) : 0;
  if (now - lastPaint < FRAME_INTERVAL_MS) return;
  lastPaint = now;
  const seconds = (((now - epoch) / 1000) % DEMO_DURATION + DEMO_DURATION) % DEMO_DURATION;
  for (const paint of painters) paint(seconds);
}

function animate(paint: Painter): () => void {
  painters.add(paint);
  if (!frame && typeof requestAnimationFrame === "function") frame = requestAnimationFrame(tick);
  return () => {
    painters.delete(paint);
    if (!painters.size && frame) {
      cancelAnimationFrame(frame);
      frame = 0;
    }
  };
}

function subscribeReducedMotion(listener: () => void) {
  const query = typeof window !== "undefined" ? window.matchMedia?.("(prefers-reduced-motion: reduce)") : undefined;
  query?.addEventListener?.("change", listener);
  return () => query?.removeEventListener?.("change", listener);
}

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
}

// ----------------------------------------------------------------------------------------------- one tile

/** One animated template on a 9:16 canvas. It only draws while on screen; reduced motion shows t = 1.2 s. */
export function CaptionStyleCanvas({ template, line, sticker, className }: {
  template: FxCaptionTemplate;
  /** The localised demo sentence (space-separated words, one with a number). */
  line?: string;
  sticker?: string;
  className?: string;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const { host, version } = useStage();
  const reduced = useSyncExternalStore(subscribeReducedMotion, prefersReducedMotion, () => false);
  const [visible, setVisible] = useState(true);
  // A new font or atlas changes text widths: measure the scene again.
  const prepared = useMemo<PreparedScene | null>(() => {
    try {
      return version >= 0 ? prepareScene(demoScene(template, { line, sticker, captionScale: TILE_CAPTION_SCALE }), host) : null;
    } catch {
      return null;
    }
  }, [template, line, sticker, host, version]);

  const paint = useCallback((seconds: number) => {
    const canvas = canvasRef.current;
    if (!canvas || !prepared) return;
    try {
      const ctx = canvas.getContext("2d") as unknown as FxContext2D | null;
      if (!ctx) return;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      renderFrame(ctx, prepared, clockAt(prepared, seconds), host);
    } catch {
      // A preview never breaks the form.
    }
  }, [prepared, host]);

  // Backing store at the tile's real size (crisp on retina, capped so ten tiles stay light).
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const fit = () => {
      const ratio = Math.min(2, window.devicePixelRatio || 1);
      const width = Math.max(54, Math.round((canvas.clientWidth || 108) * ratio));
      const height = Math.round((width * DEMO_HEIGHT) / DEMO_WIDTH);
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
        if (reduced) paint(DEMO_STATIC_T);
      }
    };
    fit();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(fit);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [paint, reduced]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(([entry]) => setVisible(entry?.isIntersecting ?? true));
    observer.observe(canvas);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (reduced) {
      paint(DEMO_STATIC_T);
      return;
    }
    if (!visible) return;
    return animate(paint);
  }, [paint, reduced, visible]);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden
      data-motion={reduced ? "static" : "animated"}
      className={cn("block aspect-[9/16] w-full bg-[#1B1F3A]", className)}
    />
  );
}
