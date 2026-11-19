"use client";

// Caption styles as 9:16 tiles. Animated styles are drawn live by fx-core (the renderer the server uses) over the procedural
// demo backdrop. One shared clock drives every visible tile; with reduced motion each tile is one still frame.
import { useCallback, useEffect, useId, useMemo, useRef, useState, useSyncExternalStore, type KeyboardEvent } from "react";
import { useTranslations } from "next-intl";
import { Check, ScanFace } from "lucide-react";

import { createBrowserHost, createFxCanvas, loadFxFont, type BrowserHost } from "@/lib/fx/browser-host";
import { loadFxAtlas } from "@/lib/fx/assets-client";
import { clockAt } from "@/lib/fx/core/frame";
import { renderFrame } from "@/lib/fx/core/render";
import { prepareScene, type PreparedScene } from "@/lib/fx/core/scene";
import type { EmojiAtlas, FxCanvas, FxContext2D } from "@/lib/fx/core/host";
import { isFxTemplate, type FxCaptionTemplate } from "@/lib/fx/core/types";
import {
  DEMO_DURATION, DEMO_FONTS, DEMO_HEIGHT, DEMO_STATIC_T, DEMO_WIDTH, demoScene, demoWords, paintDemoBackdrop, paintDemoPerson,
  TILE_CAPTION_SCALE,
} from "@/lib/fx/demo-scene";
import { FX_NEEDS_FACE } from "@/lib/fx/edit-style-request";
import { cn } from "@/lib/utils";

/** Tiles redraw at most this often: plenty for a preview, and ten of them stay cheap. */
const FRAME_INTERVAL_MS = 1000 / 30;
/** Backdrop layers are painted once at this size and scaled into every tile. */
const LAYER_WIDTH = 360;
const LAYER_HEIGHT = 640;

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

function paintLayer(paint: (ctx: FxContext2D, width: number, height: number) => void): FxCanvas | null {
  try {
    const canvas = createFxCanvas(LAYER_WIDTH, LAYER_HEIGHT);
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    paint(ctx, LAYER_WIDTH, LAYER_HEIGHT);
    return canvas;
  } catch {
    return null;
  }
}

/** The one browser host every tile draws with: backdrop + speaker as the picture, the speaker alone as the cutout. */
let layers: { backdrop: FxCanvas | null; person: FxCanvas | null } | null = null;
/** The demo room and its speaker, painted once and shared by every tile. */
function getLayers() {
  layers ??= { backdrop: paintLayer(paintDemoBackdrop), person: paintLayer(paintDemoPerson) };
  return layers;
}

function getStage(): Stage {
  if (stage) return stage;
  const { backdrop, person } = getLayers();
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

// ----------------------------------------------------------------------------------------------- the group

/** How a classic style draws its words: the fields `GET /caption-templates` lists for it. */
export type ClassicCaptionLook = { font: string; size: number; color: string; highlight: string };
/** An animated style is drawn live by fx-core; a style with `classic` is drawn from its own font and colours. */
export type CaptionStyleOption = { id: string; name?: string; description?: string; classic?: ClassicCaptionLook };

// What the list endpoint leaves out of a classic style (== CAPTION_TEMPLATES in backend/src/caption_templates.py).
const CLASSIC_EXTRAS: Readonly<Record<string, { stroke?: string; box?: string; background?: string; uppercase?: boolean; glow?: boolean }>> = {
  default: { stroke: "#000000" },
  hormozi: { stroke: "#000000", box: "#00BF49", uppercase: true },
  mrbeast: { stroke: "#000000", uppercase: true },
  minimal: { background: "#00000080" },
  tiktok: { stroke: "#000000" },
  neon: { stroke: "#002A6B", glow: true },
  podcast: { stroke: "#1A1A1A", background: "#1A1A1ACC" },
};

/** Same enlargement as the animated tiles: a classic size is in px of a 560-wide frame. */
const classicFontSize = (size: number) => `${((size / 560) * TILE_CAPTION_SCALE * 100).toFixed(2)}cqw`;

/**
 * One classic style on the tiles' backdrop: the first words of the demo line in the style's font and colours, the
 * second word as the spoken one. A still: these styles animate word by word only in the render.
 */
export function ClassicCaptionTile({ id, look, line, className }: { id: string; look: ClassicCaptionLook; line?: string; className?: string }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    try {
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      const { backdrop, person } = getLayers();
      for (const layer of [backdrop, person]) {
        if (layer) ctx.drawImage(layer as unknown as CanvasImageSource, 0, 0, canvas.width, canvas.height);
      }
    } catch {
      // A preview never breaks the form.
    }
  }, []);
  const extras = CLASSIC_EXTRAS[id] ?? { stroke: "#000000" };
  const words = demoWords(line).slice(0, 3).map((word) => (extras.uppercase ? word.text.toUpperCase() : word.text));
  const outline = extras.stroke
    ? `0 0 0.08em ${extras.stroke}, 0.06em 0.06em 0 ${extras.stroke}, -0.06em -0.06em 0 ${extras.stroke}, 0.06em -0.06em 0 ${extras.stroke}, -0.06em 0.06em 0 ${extras.stroke}`
    : "none";
  return (
    <span aria-hidden className={cn("relative block aspect-[9/16] w-full overflow-hidden bg-[#1B1F3A]", className)} style={{ containerType: "inline-size" }}>
      <canvas ref={canvasRef} width={LAYER_WIDTH / 2} height={LAYER_HEIGHT / 2} className="absolute inset-0 size-full" />
      <span className="absolute inset-x-[4%] top-[76%] flex -translate-y-1/2 justify-center">
        <span
          className="flex flex-wrap justify-center gap-x-[0.26em] rounded-[0.3em] text-center leading-[1.15]"
          style={{
            fontFamily: `'${look.font}', system-ui, sans-serif`,
            fontSize: classicFontSize(look.size),
            fontWeight: 700,
            color: look.color,
            textShadow: extras.glow ? `0 0 0.35em ${look.color}, ${outline}` : outline,
            ...(extras.background ? { backgroundColor: extras.background, padding: "0.2em 0.45em" } : {}),
          }}
        >
          {words.map((word, index) => (
            <span
              key={index}
              className="rounded-[0.22em]"
              style={index === 1 ? { color: extras.box ? "#FFFFFF" : look.highlight, ...(extras.box ? { backgroundColor: extras.box, padding: "0 0.18em" } : {}) } : undefined}
            >
              {word}
            </span>
          ))}
        </span>
      </span>
    </span>
  );
}

/**
 * Every caption style as one radio group of preview tiles (`data-tour="caption-styles"`). Arrow keys move between
 * tiles; selecting one is the caption template.
 */
export function CaptionStyleTiles({ options, value, onChange, disabled, className }: {
  options: readonly CaptionStyleOption[];
  value: string;
  onChange: (template: string) => void;
  disabled?: boolean;
  className?: string;
}) {
  const t = useTranslations("home");
  const idPrefix = useId();
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  if (!options.length) return null;
  const focusable = options.some((option) => option.id === value) ? value : options[0].id;
  const label = (option: CaptionStyleOption) => {
    const key = `animatedCaptions.templates.${option.id}`;
    if (option.classic) return { name: option.name ?? option.id, description: option.description ?? "" };
    return {
      name: t.has(`${key}.name`) ? t(`${key}.name`) : option.name ?? option.id,
      description: t.has(`${key}.description`) ? t(`${key}.description`) : option.description ?? "",
    };
  };

  const move = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const step = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 0;
    if (!step) return;
    event.preventDefault();
    const next = options[(index + step + options.length) % options.length];
    onChange(next.id);
    buttons.current.get(next.id)?.focus();
  };

  return (
    <div
      role="radiogroup"
      aria-label={t("captionStyle")}
      data-tour="caption-styles"
      className={cn("grid grid-cols-3 gap-2 sm:grid-cols-5", className)}
    >
      {options.map((option, index) => {
        const checked = option.id === value;
        const { name, description } = label(option);
        return (
          <button
            key={option.id}
            ref={(node) => { if (node) buttons.current.set(option.id, node); else buttons.current.delete(option.id); }}
            type="button"
            role="radio"
            aria-checked={checked}
            aria-label={name}
            aria-describedby={description ? `${idPrefix}-${option.id}` : undefined}
            title={description || undefined}
            tabIndex={option.id === focusable ? 0 : -1}
            disabled={disabled}
            onClick={() => onChange(option.id)}
            onKeyDown={(event) => move(event, index)}
            className="group min-w-0 rounded-xl text-left outline-none disabled:opacity-50"
          >
            <span
              className={cn(
                "relative block overflow-hidden rounded-xl ring-1 ring-border transition-[box-shadow,transform] duration-200 group-hover:ring-foreground/40 group-focus-visible:ring-2 group-focus-visible:ring-ring motion-safe:group-active:scale-[0.98]",
                checked && "ring-2 ring-foreground group-hover:ring-foreground",
              )}
            >
              {option.classic
                ? <ClassicCaptionTile id={option.id} look={option.classic} line={t("animatedCaptions.demoLine")} />
                : isFxTemplate(option.id) && <CaptionStyleCanvas template={option.id} line={t("animatedCaptions.demoLine")} sticker={t("animatedCaptions.demoSticker")} />}
              {checked && (
                <span className="absolute right-1.5 top-1.5 flex size-5 items-center justify-center rounded-full bg-foreground text-background shadow">
                  <Check className="size-3" />
                </span>
              )}
              {isFxTemplate(option.id) && FX_NEEDS_FACE.has(option.id) && (
                <span className="absolute bottom-1.5 left-1.5 flex size-5 items-center justify-center rounded-full bg-black/55 text-white backdrop-blur" title={t("animatedCaptions.needsFace")}>
                  <ScanFace className="size-3" aria-hidden />
                  <span className="sr-only">{t("animatedCaptions.needsFace")}</span>
                </span>
              )}
            </span>
            <span className="mt-1.5 block truncate px-0.5 text-xs font-medium">{name}</span>
            {description && <span id={`${idPrefix}-${option.id}`} className="sr-only">{description}</span>}
          </button>
        );
      })}
    </div>
  );
}
