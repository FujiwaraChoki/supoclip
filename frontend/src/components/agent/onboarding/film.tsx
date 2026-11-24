"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";

import { usePrefersReducedMotion } from "@/hooks/use-prefers-reduced-motion";

/*
 * The onboarding films: scripted UI drawn at a fixed canvas size and scaled to fit, like the
 * landing page's product film. One clock drives each film; scenes derive what they show from it.
 */

export function ease(x: number) {
  return x < 0.5 ? 4 * x * x * x : 1 - (-2 * x + 2) ** 3 / 2;
}

export function between(t: number, from: number, to: number) {
  return Math.min(1, Math.max(0, (t - from) / (to - from)));
}

/** A looping clock in seconds, at ~30 fps, paused while `playing` is false. Reduced motion holds `still`. */
export function useFilmClock(length: number, playing: boolean, still: number) {
  const reduced = usePrefersReducedMotion();
  const [t, setT] = useState(reduced ? still : 0);
  const time = useRef(0);
  useEffect(() => {
    if (reduced) {
      setT(still);
      return;
    }
    if (!playing) return;
    let raf = 0;
    let last = performance.now();
    let shown = 0;
    const tick = (now: number) => {
      time.current = (time.current + Math.min(now - last, 100) / 1000) % length;
      last = now;
      if (now - shown > 33) {
        shown = now;
        setT(time.current);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [length, playing, reduced, still]);
  return { t, reduced: Boolean(reduced) };
}

/** Scale for a canvas of `width` px drawn inside `ref`. */
export function useCanvasScale(ref: React.RefObject<HTMLDivElement | null>, width: number) {
  const [scale, setScale] = useState(0.5);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    const update = () => setScale(element.clientWidth / width);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref, width]);
  return scale;
}

/** The pointer that clicks through a film. Positions are canvas pixels. */
export function FilmCursor({ x, y, pressed }: { x: number; y: number; pressed: boolean }) {
  return (
    <div className="pointer-events-none absolute left-0 top-0 z-[70]" style={{ transform: `translate(${x}px, ${y}px)` }}>
      <motion.svg
        width="22"
        height="26"
        viewBox="0 0 22 26"
        className="-translate-x-[3px] -translate-y-[2px] drop-shadow-[0_2px_3px_rgb(0_0_0/0.35)]"
        animate={{ scale: pressed ? 0.82 : 1 }}
        transition={{ duration: 0.12 }}
      >
        <path d="M3 2.5v18.2l4.9-4.6 3.1 7.1 3.2-1.4-3.1-7h6.8z" fill="#111" stroke="#fff" strokeWidth="1.6" strokeLinejoin="round" />
      </motion.svg>
      <AnimatePresence>
        {pressed && (
          <motion.span
            className="absolute -left-4 -top-4 size-8 rounded-full border-2 border-brand/70"
            initial={{ scale: 0.3, opacity: 0.9 }}
            animate={{ scale: 1.3, opacity: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.45 }}
          />
        )}
      </AnimatePresence>
    </div>
  );
}

export type Waypoint = { start: number; end: number; x: number; y: number };

/** Where the cursor is at `t` along a path of eased moves, starting from `home`. */
export function cursorAt(t: number, home: { x: number; y: number }, path: readonly Waypoint[]) {
  let from = home;
  let pos = home;
  for (const point of path) {
    if (t < point.start) break;
    const k = ease(between(t, point.start, point.end));
    pos = t >= point.end ? point : { x: from.x + (point.x - from.x) * k, y: from.y + (point.y - from.y) * k };
    from = point;
  }
  return pos;
}

/**
 * A real SupoClip clip playing silently in a film. Plays only while its film is on screen, so
 * the page never decodes videos nobody can see.
 */
export function FilmClip({ src, poster, playing, className }: { src: string; poster: string; playing: boolean; className?: string }) {
  const ref = useRef<HTMLVideoElement | null>(null);
  useEffect(() => {
    const video = ref.current;
    if (!video) return;
    if (playing) void video.play().catch(() => {});
    else video.pause();
  }, [playing]);
  return (
    <video
      ref={ref}
      src={src}
      poster={poster}
      muted
      loop
      playsInline
      preload="metadata"
      aria-hidden
      tabIndex={-1}
      className={className}
    />
  );
}

/** The text of `full` typed out between `from` and `to`. */
export function typed(full: string, t: number, from: number, to: number) {
  return full.slice(0, Math.round(full.length * between(t, from, to)));
}

/**
 * A scripted camera: each shot names a point to look at (scene pixels) and a zoom, from time `t`,
 * reached over `dur` seconds. The view never shows past the scene's edges.
 */
export type Shot = { t: number; x: number; y: number; s: number; dur?: number };

export function cameraAt(t: number, shots: readonly Shot[], width: number, height: number) {
  let index = 0;
  for (let i = 0; i < shots.length; i++) if (t >= shots[i].t) index = i;
  const shot = shots[index];
  const from = shots[Math.max(0, index - 1)];
  const k = index === 0 ? 1 : ease(between(t, shot.t, shot.t + (shot.dur ?? 0.9)));
  const s = from.s + (shot.s - from.s) * k;
  const fx = from.x + (shot.x - from.x) * k;
  const fy = from.y + (shot.y - from.y) * k;
  const x = Math.min(0, Math.max(width - width * s, width / 2 - fx * s));
  const y = Math.min(0, Math.max(height - height * s, height / 2 - fy * s));
  return { transform: `translate(${x}px, ${y}px) scale(${s})` };
}
