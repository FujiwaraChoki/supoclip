"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
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
export function CountUp({ from, to }: { from: number; to: number }) {
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
  const t = useTranslations("billing");
  const reduceMotion = useReducedMotion();
  const width = 280;
  const span = Math.max(nextLimitSeconds, durationSeconds);
  const x = (seconds: number) => (Math.min(seconds, span) / span) * width;
  const ticks = Array.from({ length: Math.floor(span / 1800) + 1 }, (_, i) => i * 1800);
  return (
    <div>
      <p className="font-display text-5xl font-bold tracking-tight">{durationLabel}</p>
      <p className="mt-1 text-sm text-muted-foreground">{t("illustration.fitsIn", { plan: nextLabel.split(" · ")[0] })}</p>
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
          <text x={x(durationSeconds)} y={-14} textAnchor="middle" className="font-medium">{t("illustration.yourVideo")}</text>
          <text x={x(currentLimitSeconds)} y={56} textAnchor="middle" fillOpacity={0.6}>{currentLabel}</text>
          <text x={width} y={56} textAnchor="end" fillOpacity={0.6}>{nextLabel}</text>
        </g>
      </svg>
    </div>
  );
}

const TYPEFACES = [
  { id: "serif", family: "Georgia, 'Times New Roman', serif", style: "italic" as const, weight: 400 },
  { id: "display", family: "var(--font-syne), system-ui, sans-serif", style: "normal" as const, weight: 700 },
  { id: "mono", family: "ui-monospace, SFMono-Regular, Menlo, monospace", style: "normal" as const, weight: 700 },
] as const;

/** A clip whose caption switches typeface, standing in for the user's own fonts. */
export function FontIllustration() {
  const t = useTranslations("billing");
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
              key={typeface.id}
              className="text-center text-[11px] leading-tight"
              style={{ fontFamily: typeface.family, fontStyle: typeface.style, fontWeight: typeface.weight }}
              initial={{ y: 16, opacity: 0 }}
              animate={{ y: 0, opacity: 1 }}
              exit={{ y: -16, opacity: 0 }}
              transition={{ type: "spring", stiffness: 260, damping: 24 }}
            >
              {t("illustration.sample")}
            </motion.span>
          </AnimatePresence>
        </div>
      </div>
      <ul className="space-y-1.5 text-sm">
        {TYPEFACES.map((face, index) => (
          <li key={face.id} className="relative flex items-center gap-2 px-2 py-1">
            {index === active && (
              <motion.span layoutId="font-active" className="absolute inset-0 rounded-md bg-background shadow-sm ring-1 ring-border" transition={{ type: "spring", stiffness: 300, damping: 30 }} />
            )}
            <span className="relative w-8 text-sm" style={{ fontFamily: face.family, fontStyle: face.style, fontWeight: face.weight }}>Aa</span>
            <span className={index === active ? "relative" : "relative text-muted-foreground"}>{t(`illustration.${face.id}`)}</span>
          </li>
        ))}
        <li className="px-2 pt-1 text-xs text-muted-foreground">{t("illustration.ownFont")}</li>
      </ul>
    </div>
  );
}
