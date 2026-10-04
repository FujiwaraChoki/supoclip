"use client";

import { useEffect, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";

/**
 * Line-art loop of what SupoClip does: a long video is scanned, its best
 * moments are marked, scissors cut them out, and they become vertical clips.
 * Everything is drawn in currentColor.
 */

const STRIP = { x: 20, y: 44, width: 440, height: 56 };
const SEGMENTS = [
  { from: 56, to: 132 },
  { from: 196, to: 284 },
  { from: 340, to: 420 },
];
const CLIP = { y: 176, width: 76, height: 136 };
const CLIP_X = [82, 202, 322];

const SCAN_MS = 1500;
const CUT_MS = 1800;
// When each phase starts within one loop.
const PHASES = { scan: 0, mark: 1500, cut: 2300, clips: 4300, fade: 7600, restart: 8200 };
type Phase = keyof typeof PHASES;

const cutDelay = (x: number) => ((x - STRIP.x) / STRIP.width) * (CUT_MS / 1000);

const WAVE = Array.from({ length: 54 }, (_, i) => {
  const x = STRIP.x + 10 + i * 7.8;
  const amp = 4 + Math.abs(Math.sin(i * 0.9) * 9 + Math.sin(i * 0.37 + 1) * 6);
  return `M${x.toFixed(1)} ${(72 - amp).toFixed(1)}V${(72 + amp).toFixed(1)}`;
}).join("");

export function ClipCutAnimation() {
  const reduceMotion = useReducedMotion();
  const [cycle, setCycle] = useState(0);
  const [phase, setPhase] = useState<Phase>("scan");

  useEffect(() => {
    if (reduceMotion) return;
    const timers = (Object.keys(PHASES) as Phase[]).map((name) =>
      window.setTimeout(() => {
        if (name === "restart") {
          setPhase("scan");
          setCycle((value) => value + 1);
        } else {
          setPhase(name);
        }
      }, PHASES[name]),
    );
    return () => timers.forEach(window.clearTimeout);
  }, [cycle, reduceMotion]);

  const shown: Phase = reduceMotion ? "clips" : phase;
  const at = (name: Phase) => PHASES[shown] >= PHASES[name];
  const label = at("clips") ? "3 clips · captioned · ready to post" : "podcast-episode-42.mp4 · 1:24:10";

  return (
    <motion.svg
      key={cycle}
      viewBox="0 0 480 330"
      className="mx-auto w-full max-w-[36rem] select-none overflow-visible"
      aria-hidden
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      animate={{ opacity: shown === "fade" ? 0 : 1 }}
      transition={{ duration: 0.5 }}
    >
      {/* The source video: a long strip of frames over a waveform. */}
      <rect {...STRIP} rx={10} strokeOpacity={0.55} strokeWidth={1.5} />
      {Array.from({ length: 7 }, (_, i) => (
        <line key={i} x1={STRIP.x + (i + 1) * 55} x2={STRIP.x + (i + 1) * 55} y1={STRIP.y} y2={STRIP.y + STRIP.height} strokeOpacity={0.12} />
      ))}
      <path d={WAVE} strokeOpacity={0.4} strokeWidth={2} />

      <AnimatePresence mode="wait" initial={false}>
        <motion.text
          key={label}
          x={STRIP.x}
          y={STRIP.y + STRIP.height + 26}
          fill="currentColor"
          stroke="none"
          fontSize={11}
          className="font-mono"
          initial={{ opacity: 0 }}
          animate={{ opacity: 0.55 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.25 }}
        >
          {label}
        </motion.text>
      </AnimatePresence>

      {!reduceMotion && shown === "scan" && (
        <motion.line
          x1={STRIP.x}
          x2={STRIP.x}
          y1={STRIP.y - 8}
          y2={STRIP.y + STRIP.height + 8}
          strokeWidth={2}
          initial={{ x: 0 }}
          animate={{ x: STRIP.width }}
          transition={{ duration: SCAN_MS / 1000, ease: "linear" }}
        />
      )}

      {SEGMENTS.map((segment, index) => {
        const lifted = at("clips");
        return (
          <g key={index}>
            {/* The marked moment; once lifted out it leaves a dashed hole. */}
            <motion.rect
              x={segment.from}
              y={STRIP.y}
              width={segment.to - segment.from}
              height={STRIP.height}
              rx={6}
              strokeWidth={1.5}
              initial={{ opacity: 0 }}
              animate={{
                opacity: at("mark") ? 1 : 0,
                fillOpacity: lifted ? 0 : 0.14,
                strokeOpacity: lifted ? 0.35 : 1,
              }}
              fill="currentColor"
              strokeDasharray={lifted ? "4 5" : undefined}
              transition={{ duration: 0.4, delay: lifted ? 0 : index * 0.15 }}
            />

            {[segment.from, segment.to].map((x) => (
              <motion.line
                key={x}
                x1={x}
                x2={x}
                y1={STRIP.y - 10}
                y2={STRIP.y + STRIP.height + 10}
                strokeWidth={1.5}
                strokeDasharray="3 4"
                initial={{ opacity: 0, scaleY: 0 }}
                animate={at("cut") ? { opacity: 0.8, scaleY: 1 } : { opacity: 0, scaleY: 0 }}
                style={{ originY: "0px" }}
                transition={{ duration: 0.2, delay: reduceMotion ? 0 : cutDelay(x) }}
              />
            ))}

            {/* The clip the moment becomes. */}
            {lifted && (
              <g>
                <motion.rect
                  rx={10}
                  strokeWidth={1.5}
                  initial={{ x: segment.from, y: STRIP.y, width: segment.to - segment.from, height: STRIP.height }}
                  animate={{ x: CLIP_X[index], y: CLIP.y, width: CLIP.width, height: CLIP.height }}
                  transition={reduceMotion ? { duration: 0 } : { type: "spring", stiffness: 140, damping: 18, delay: index * 0.18 }}
                />
                <motion.g
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.4, delay: reduceMotion ? 0 : 0.55 + index * 0.18 }}
                >
                  <ClipContents x={CLIP_X[index]} />
                </motion.g>
              </g>
            )}
          </g>
        );
      })}

      {!reduceMotion && shown === "cut" && (
        <motion.g initial={{ x: STRIP.x }} animate={{ x: STRIP.x + STRIP.width }} transition={{ duration: CUT_MS / 1000, ease: "linear" }}>
          <Scissors />
        </motion.g>
      )}
    </motion.svg>
  );
}

/** A speaker silhouette, a hook title, a score dot and captions inside a clip at `x`. */
function ClipContents({ x }: { x: number }) {
  const cx = x + CLIP.width / 2;
  const top = CLIP.y;
  return (
    <g strokeWidth={1.5}>
      <line x1={x + 12} x2={x + CLIP.width - 12} y1={top + 14} y2={top + 14} strokeOpacity={0.5} />
      <line x1={x + 20} x2={x + CLIP.width - 20} y1={top + 21} y2={top + 21} strokeOpacity={0.5} />
      <circle cx={x + CLIP.width - 13} cy={top + 36} r={6} strokeOpacity={0.7} />
      <circle cx={cx} cy={top + 74} r={11} strokeOpacity={0.6} />
      <path d={`M${cx - 24} ${top + CLIP.height} C${cx - 24} ${top + 96} ${cx + 24} ${top + 96} ${cx + 24} ${top + CLIP.height}`} strokeOpacity={0.6} />
      <line x1={x + 14} x2={x + CLIP.width - 14} y1={top + 106} y2={top + 106} strokeWidth={3} />
      <line x1={x + 22} x2={x + CLIP.width - 22} y1={top + 114} y2={top + 114} strokeWidth={3} />
    </g>
  );
}

/** Scissors pointing down at the strip, blades snipping around the pivot as they travel. */
function Scissors() {
  const blade = (side: 1 | -1) => (
    <g>
      {/* SMIL rotates around the local origin, which is the pivot screw. */}
      <animateTransform attributeName="transform" type="rotate" values={`0;${side * 12};0`} dur="0.4s" repeatCount="indefinite" />
      <circle cx={side * -9} cy={-22} r={6} strokeWidth={1.8} />
      <line x1={side * -6} y1={-17} x2={side * 5} y2={16} strokeWidth={1.8} />
    </g>
  );
  return (
    <g transform={`translate(0 ${STRIP.y - 4})`}>
      {blade(1)}
      {blade(-1)}
      <circle r={1.8} fill="currentColor" />
    </g>
  );
}
