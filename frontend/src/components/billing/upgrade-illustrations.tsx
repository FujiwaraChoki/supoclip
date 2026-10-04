"use client";

import { useEffect, useState } from "react";
import { AnimatePresence, animate, motion, useMotionValue, useReducedMotion, useTransform } from "motion/react";

/*
 * Line-art illustrations for the upgrade dialog. Drawn in currentColor so they
 * follow the theme, animated with Motion, and static under reduced motion.
 */

const stroke = {
  fill: "none",
  stroke: "currentColor",
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  strokeWidth: 1.5,
};

/** A count that ticks up from `from` to `to`. */
function CountUp({ from, to }: { from: number; to: number }) {
  const reduceMotion = useReducedMotion();
  const value = useMotionValue(reduceMotion ? to : from);
  const rounded = useTransform(value, (latest) => Math.round(latest).toString());
  useEffect(() => {
    if (reduceMotion) return;
    const controls = animate(value, to, { duration: 1.1, ease: [0.2, 0.8, 0.2, 1], delay: 0.25 });
    return () => controls.stop();
  }, [reduceMotion, to, value]);
  return <motion.span>{rounded}</motion.span>;
}

/** A clip outline with a speaker and captions, at the origin, 60×106. */
function ClipGlyph({ highlight }: { highlight?: boolean }) {
  return (
    <g {...stroke} strokeOpacity={highlight ? 1 : 0.45}>
      <rect width={60} height={106} rx={9} />
      <circle cx={30} cy={52} r={9} />
      <path d="M11 106 C11 76 49 76 49 106" />
      <line x1={12} x2={48} y1={84} y2={84} strokeWidth={2.5} />
      <line x1={18} x2={42} y1={91} y2={91} strokeWidth={2.5} />
    </g>
  );
}

const fanSize = (videos: number) => (videos <= 0 ? 0 : videos <= 60 ? 5 : 9);

/**
 * More clips per month: a fan whose size follows the plan (5 cards for Pro,
 * 9 for Scale). Cards you already have stay faint; the new ones fan in.
 */
export function CapacityIllustration({ from, to, label }: { from: number; to: number; label: string }) {
  const reduceMotion = useReducedMotion();
  const total = fanSize(to);
  const existing = fanSize(from);
  const half = (total - 1) / 2;
  const spread = total > 5 ? 22 : 32;
  // Outer cards first so the centre card is drawn on top.
  const slots = Array.from({ length: total }, (_, i) => i - half).sort((a, b) => Math.abs(b) - Math.abs(a));
  return (
    <div>
      <p className="font-display text-5xl font-bold tracking-tight tabular-nums">
        <CountUp from={from} to={to} />
      </p>
      <p className="mt-1 text-sm text-muted-foreground">{label}</p>
      <svg viewBox="-150 -10 300 150" className="mt-8 w-full overflow-visible" aria-hidden>
        {slots.map((slot) => {
          const isNew = Math.abs(slot) > (existing - 1) / 2;
          return (
            <motion.g
              key={slot}
              initial={reduceMotion ? false : { x: -30, y: 12, rotate: 0, opacity: isNew ? 0 : 1 }}
              animate={{ x: slot * spread - 30, y: Math.abs(slot) * 5, rotate: slot * 6, opacity: 1 }}
              transition={{ type: "spring", stiffness: 140, damping: 17, delay: 0.15 + Math.abs(slot) * 0.06 }}
              style={{ originX: 0.5, originY: 1 }}
            >
              <rect width={60} height={106} rx={9} className="fill-background" />
              <ClipGlyph highlight={slot === 0 || (isNew && existing > 0)} />
            </motion.g>
          );
        })}
      </svg>
    </div>
  );
}

/** A long video on a ruler: today's limit is a dashed mark, the bigger plan's reach is the full line. */
export function LengthIllustration({
  durationSeconds,
  currentLimitSeconds,
  nextLimitSeconds,
  currentLabel,
  nextLabel,
  durationLabel,
}: {
  durationSeconds: number;
  currentLimitSeconds: number;
  nextLimitSeconds: number;
  currentLabel: string;
  nextLabel: string;
  durationLabel: string;
}) {
  const reduceMotion = useReducedMotion();
  const width = 280;
  const span = Math.max(nextLimitSeconds, durationSeconds);
  const x = (seconds: number) => (Math.min(seconds, span) / span) * width;
  const ticks = Array.from({ length: Math.floor(span / 1800) + 1 }, (_, i) => i * 1800);
  return (
    <div>
      <p className="font-display text-5xl font-bold tracking-tight">{durationLabel}</p>
      <p className="mt-1 text-sm text-muted-foreground">fits in {nextLabel.split(" · ")[0]}</p>
      <svg viewBox={`-10 -40 ${width + 20} 110`} className="mt-10 w-full overflow-visible" aria-hidden>
        <g {...stroke}>
          <line x1={0} x2={width} y1={20} y2={20} strokeOpacity={0.25} />
          {ticks.map((tick) => (
            <line key={tick} x1={x(tick)} x2={x(tick)} y1={14} y2={26} strokeOpacity={0.25} />
          ))}
          <line x1={x(currentLimitSeconds)} x2={x(currentLimitSeconds)} y1={-4} y2={40} strokeDasharray="3 4" strokeOpacity={0.6} />
          <motion.line
            x1={0}
            x2={x(durationSeconds)}
            y1={20}
            y2={20}
            strokeWidth={4}
            initial={reduceMotion ? false : { pathLength: 0 }}
            animate={{ pathLength: 1 }}
            transition={{ duration: 1.2, ease: [0.3, 0.7, 0.2, 1], delay: 0.2 }}
          />
          <motion.circle
            cx={x(durationSeconds)}
            cy={20}
            r={5}
            className="fill-background"
            initial={reduceMotion ? false : { scale: 0 }}
            animate={{ scale: 1 }}
            transition={{ type: "spring", stiffness: 300, damping: 15, delay: reduceMotion ? 0 : 1.3 }}
          />
        </g>
        <g fill="currentColor" fontSize={10}>
          <text x={x(durationSeconds)} y={-14} textAnchor="middle" className="font-medium">Your video</text>
          <text x={x(currentLimitSeconds)} y={56} textAnchor="middle" fillOpacity={0.6}>{currentLabel}</text>
          <text x={width} y={56} textAnchor="end" fillOpacity={0.6}>{nextLabel}</text>
        </g>
      </svg>
    </div>
  );
}

const TYPEFACES = [
  { name: "Serif", family: "Georgia, 'Times New Roman', serif", style: "italic" as const, weight: 400 },
  { name: "Display", family: "var(--font-syne), system-ui, sans-serif", style: "normal" as const, weight: 700 },
  { name: "Mono", family: "ui-monospace, SFMono-Regular, Menlo, monospace", style: "normal" as const, weight: 700 },
];

/** A clip whose caption switches typeface, standing in for the user's own fonts. */
export function FontIllustration() {
  const reduceMotion = useReducedMotion();
  const [active, setActive] = useState(0);
  useEffect(() => {
    if (reduceMotion) return;
    const timer = window.setInterval(() => setActive((index) => (index + 1) % TYPEFACES.length), 1700);
    return () => window.clearInterval(timer);
  }, [reduceMotion]);
  const typeface = TYPEFACES[active];
  return (
    <div className="flex items-center justify-center gap-8">
      <div className="relative">
        <svg viewBox="-2 -2 124 214" className="w-32" aria-hidden>
          <g {...stroke}>
            <rect width={120} height={210} rx={16} strokeOpacity={0.5} />
            <circle cx={60} cy={92} r={18} strokeOpacity={0.5} />
            <path d="M22 210 C22 150 98 150 98 210" strokeOpacity={0.5} />
          </g>
        </svg>
        <div className="absolute inset-x-3 bottom-[24%] flex h-9 items-center justify-center overflow-hidden">
          <AnimatePresence mode="popLayout" initial={false}>
            <motion.span
              key={typeface.name}
              className="text-center text-[11px] leading-tight"
              style={{ fontFamily: typeface.family, fontStyle: typeface.style, fontWeight: typeface.weight }}
              initial={{ y: 16, opacity: 0 }}
              animate={{ y: 0, opacity: 1 }}
              exit={{ y: -16, opacity: 0 }}
              transition={{ type: "spring", stiffness: 260, damping: 24 }}
            >
              and that&apos;s why
            </motion.span>
          </AnimatePresence>
        </div>
      </div>
      <ul className="space-y-1.5 text-sm">
        {TYPEFACES.map((face, index) => (
          <li key={face.name} className="relative flex items-center gap-2 px-2 py-1">
            {index === active && (
              <motion.span layoutId="font-active" className="absolute inset-0 rounded-md bg-background shadow-sm ring-1 ring-border" transition={{ type: "spring", stiffness: 300, damping: 30 }} />
            )}
            <span className="relative w-8 text-sm" style={{ fontFamily: face.family, fontStyle: face.style, fontWeight: face.weight }}>Aa</span>
            <span className={index === active ? "relative" : "relative text-muted-foreground"}>{face.name}</span>
          </li>
        ))}
        <li className="px-2 pt-1 text-xs text-muted-foreground">or your own .ttf</li>
      </ul>
    </div>
  );
}
