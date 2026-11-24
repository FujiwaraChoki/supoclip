"use client";

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion, useAnimate, useReducedMotion } from "motion/react";

import { cn } from "@/lib/utils";

/**
 * What Agent is doing:
 * - `idle`: breathes (its shape slowly shifts), blinks, glances around.
 * - `listening`: leans in and looks down at what you type.
 * - `thinking`: a thought bubble above its head; it raises an eyebrow, purses a wavy mouth and ponders
 *   through a few poses.
 * - `happy`: hops and smiles with its eyes (a turn just finished).
 * - `sad`: flat eyes, a little smaller (something didn't work).
 */
export type AgentMood = "idle" | "listening" | "thinking" | "happy" | "sad";

/** Everything is drawn in `em`, so one font size scales the whole character and its bubble. */
const SIZES = { sm: "text-[9px]", md: "text-[11px]", lg: "text-[19px]" } as const;

/*
 * The face is a 60×40 drawing centered on the pebble. Every mouth is two cubic curves, so any mouth
 * morphs smoothly into any other.
 */
const MOUTHS = {
  idle: "M25 28 C26.5 30 27.5 30.5 29 30.5 C30.5 30.5 31.5 30 33 28",
  listening: "M26 28 C27 29.3 28 29.6 29 29.6 C30 29.6 31 29.3 32 28",
  happy: "M23.5 27.5 C25.5 30.5 27 31.5 29 31.5 C31 31.5 32.5 30.5 34.5 27.5",
  sad: "M25 30.5 C26.5 28.5 27.5 28 29 28 C30.5 28 31.5 28.5 33 30.5",
} as const;

/** The poses Agent ponders through while it works: eyebrows, a wavy mouth, where it looks. */
const THINKING_POSES = [
  {
    // One eyebrow up, looking up at the bubble.
    brows: ["M14.5 7 Q19.5 2 25 5", "M33 7 Q38 6 43 7"],
    mouth: "M24.5 29 C26 27 27.5 27 29 29 C30.5 31 32 31 33.5 29",
    gaze: { x: 2.4, y: -2 },
    tilt: 5,
  },
  {
    // A little frown of concentration.
    brows: ["M15 5.5 Q20 5 25 7.5", "M33 7.5 Q38 5 43 5.5"],
    mouth: "M24.5 29 C26 31 27.5 31 29 29 C30.5 27 32 27 33.5 29",
    gaze: { x: -2, y: -1.6 },
    tilt: -3,
  },
  {
    // The other eyebrow, the wave settling.
    brows: ["M15 7 Q20 6 25 7", "M33 5 Q38.5 2 43.5 7"],
    mouth: "M25.5 29 C26.8 28 27.8 28 29 29 C30.2 30 31.2 30 32.5 29",
    gaze: { x: 0.6, y: -2.2 },
    tilt: 2,
  },
] as const;

const POSE_MS = 1600;
const MAX_LOOK = 3.2;

function randomBetween(low: number, high: number) {
  return low + Math.random() * (high - low);
}

/** Three dots in a little cloud, rising from two smaller bubbles. */
function ThoughtBubble({ still }: { still: boolean }) {
  const pop = { type: "spring" as const, stiffness: 420, damping: 18 };
  return (
    <motion.span
      aria-hidden
      className="pointer-events-none absolute bottom-[calc(100%-0.35em)] left-[62%] block origin-bottom-left"
      initial={{ opacity: 0, scale: 0.4 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.5, transition: { duration: 0.15 } }}
      transition={pop}
    >
      <motion.span
        className="absolute bottom-0 left-0 block size-[0.42em] rounded-full border border-border bg-background shadow-xs"
        initial={{ scale: 0 }} animate={{ scale: 1 }} transition={pop}
      />
      <motion.span
        className="absolute bottom-[0.55em] left-[0.5em] block size-[0.62em] rounded-full border border-border bg-background shadow-xs"
        initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ ...pop, delay: 0.06 }}
      />
      <motion.span
        className="agent-cloud absolute bottom-[1.15em] left-[0.85em] flex h-[1.7em] w-[2.9em] items-center justify-center gap-[0.26em] border border-border bg-background shadow-sm"
        initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ ...pop, delay: 0.12 }}
      >
        {[0, 1, 2].map((dot) => (
          <motion.span
            key={dot}
            className="block size-[0.3em] rounded-full bg-foreground"
            animate={still ? { opacity: 0.6 } : { y: ["0em", "-0.22em", "0em"], opacity: [0.35, 1, 0.35] }}
            transition={still ? undefined : { duration: 1, repeat: Infinity, delay: dot * 0.16, ease: "easeInOut" }}
          />
        ))}
      </motion.span>
    </motion.span>
  );
}

/**
 * Agent, SupoClip's helper: a small pebble with a face. It breathes, blinks, follows the
 * pointer when `follow` is on, ponders in a thought bubble while it works and hops when it's done.
 */
export function AgentMark({
  busy,
  mood,
  follow = false,
  size = "sm",
  className,
}: {
  /** Shorthand for `mood="thinking"`. */
  busy?: boolean;
  mood?: AgentMood;
  /** Eyes follow the pointer (the big Agent on the home screen). */
  follow?: boolean;
  size?: keyof typeof SIZES;
  className?: string;
}) {
  const current: AgentMood = mood ?? (busy ? "thinking" : "idle");
  const reduced = Boolean(useReducedMotion());
  const ref = useRef<HTMLSpanElement | null>(null);
  const [body, animateBody] = useAnimate();
  const [blink, setBlink] = useState(false);
  const [look, setLook] = useState({ x: 0, y: 0 });
  const [pose, setPose] = useState(0);
  const previous = useRef<AgentMood>(current);

  // Blinks at a human pace: every 2.5-6 s, now and then twice.
  useEffect(() => {
    if (reduced || current === "happy" || current === "sad") return;
    let timer: ReturnType<typeof setTimeout>;
    const schedule = () => {
      timer = setTimeout(() => {
        setBlink(true);
        setTimeout(() => setBlink(false), 130);
        if (Math.random() < 0.2) setTimeout(() => { setBlink(true); setTimeout(() => setBlink(false), 120); }, 270);
        schedule();
      }, randomBetween(2500, 6000));
    };
    schedule();
    return () => clearTimeout(timer);
  }, [reduced, current]);

  // Pondering: moves through the thinking poses while it works.
  useEffect(() => {
    if (current !== "thinking") return;
    setPose(0);
    if (reduced) return;
    const timer = setInterval(() => setPose((p) => (p + 1) % THINKING_POSES.length), POSE_MS);
    return () => clearInterval(timer);
  }, [current, reduced]);

  // Glances around when it isn't following the pointer.
  useEffect(() => {
    if (reduced || follow || current !== "idle") return;
    let timer: ReturnType<typeof setTimeout>;
    const glance = () => {
      timer = setTimeout(() => {
        setLook(Math.random() < 0.4 ? { x: 0, y: 0 } : { x: randomBetween(-2.2, 2.2), y: randomBetween(-1.2, 1.2) });
        glance();
      }, randomBetween(1800, 4200));
    };
    glance();
    return () => clearTimeout(timer);
  }, [reduced, follow, current]);

  // Following the pointer; the spring on the eyes eases it.
  useEffect(() => {
    if (!follow || reduced) return;
    let frame = 0;
    const onMove = (event: PointerEvent) => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const box = ref.current?.getBoundingClientRect();
        if (!box) return;
        const dx = event.clientX - (box.left + box.width / 2);
        const dy = event.clientY - (box.top + box.height / 2);
        const distance = Math.hypot(dx, dy) || 1;
        const reach = Math.min(1, distance / 260) * MAX_LOOK;
        setLook({ x: (dx / distance) * reach, y: (dy / distance) * reach * 0.8 });
      });
    };
    window.addEventListener("pointermove", onMove, { passive: true });
    return () => { window.removeEventListener("pointermove", onMove); cancelAnimationFrame(frame); };
  }, [follow, reduced]);

  const thinking = THINKING_POSES[pose];

  // Every change of mood: a little squash and stretch, then the new pose (a hop for happy).
  useEffect(() => {
    const element = body.current;
    if (!element) return;
    const rest = current === "listening"
      ? { rotate: -6, y: "0.1em", scale: 1 }
      : current === "sad"
        ? { rotate: 0, y: "0.12em", scale: 0.92 }
        : current === "thinking"
          ? { rotate: thinking.tilt, y: "-0.04em", scale: 1 }
          : { rotate: 0, y: "0em", scale: 1 };
    const changed = previous.current !== current;
    previous.current = current;
    if (reduced) {
      void animateBody(element, { ...rest, scaleX: 1, scaleY: 1 }, { duration: 0 });
      return;
    }
    if (changed && current === "happy") {
      void animateBody(element, {
        y: ["0em", "-0.55em", "0em", "-0.28em", "0em"],
        scaleX: [1, 0.95, 1.07, 0.98, 1],
        scaleY: [1, 1.06, 0.93, 1.02, 1],
        rotate: [0, -4, 0, 3, 0],
      }, { duration: 0.95, ease: "easeOut" });
      return;
    }
    if (changed) {
      void animateBody(element, { scaleX: [1, 1.07, 0.97, 1], scaleY: [1, 0.92, 1.04, 1] }, { duration: 0.42, ease: "easeOut" });
    }
    void animateBody(element, rest, { type: "spring", stiffness: 220, damping: 16 });
  }, [current, thinking.tilt, reduced, animateBody, body]);

  const gaze = current === "thinking" ? thinking.gaze
    : current === "listening" ? { x: look.x * 0.4, y: 2.4 }
      : current === "idle" ? look : { x: 0, y: 0 };
  const spring = { type: "spring" as const, stiffness: 240, damping: 20, mass: 0.6 };
  const mouth = current === "thinking" ? thinking.mouth : MOUTHS[current];
  // Eyes squint shut into a new shape and open again.
  const squint = {
    initial: { opacity: 0, scaleY: 0.15 },
    animate: { opacity: 1, scaleY: 1 },
    exit: { opacity: 0, scaleY: 0.15 },
    transition: { duration: 0.16, ease: "easeOut" as const },
  };

  return (
    <span
      ref={ref}
      role="img"
      aria-label="Agent"
      className={cn("relative inline-flex shrink-0", SIZES[size], className)}
    >
      <span
        ref={body}
        className={cn(
          "agent-pebble relative flex h-[3.55em] w-[3.8em] items-center justify-center bg-foreground text-background",
          current === "thinking" && "agent-pebble-busy",
        )}
      >
        <svg viewBox="0 0 60 40" className="w-[2.95em] overflow-visible" aria-hidden>
          <AnimatePresence initial={false}>
            {current === "thinking" && (
              <motion.g
                key="brows"
                fill="none"
                stroke="currentColor"
                strokeWidth={2.4}
                strokeLinecap="round"
                initial={{ opacity: 0, y: -3 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -2 }}
                transition={{ type: "spring", stiffness: 300, damping: 18 }}
              >
                {thinking.brows.map((brow, index) => (
                  <motion.path
                    key={index}
                    initial={{ d: brow, pathLength: 0 }}
                    animate={{ d: brow, pathLength: 1 }}
                    transition={{ d: { duration: 0.4, ease: "easeInOut" }, pathLength: { duration: 0.3, delay: index * 0.06 } }}
                  />
                ))}
              </motion.g>
            )}
          </AnimatePresence>
          <AnimatePresence initial={false} mode="wait">
            {current === "happy" ? (
              <motion.g key="happy" {...squint} style={{ transformBox: "fill-box", transformOrigin: "center" }} fill="none" stroke="currentColor" strokeWidth={3} strokeLinecap="round">
                <path d="M15 18 Q20 12 25 18" />
                <path d="M33 18 Q38 12 43 18" />
              </motion.g>
            ) : current === "sad" ? (
              <motion.g key="sad" {...squint} style={{ transformBox: "fill-box", transformOrigin: "center" }} fill="none" stroke="currentColor" strokeWidth={3} strokeLinecap="round">
                <path d="M16 18 H24" />
                <path d="M34 18 H42" />
              </motion.g>
            ) : (
              <motion.g key="eyes" {...squint} style={{ transformBox: "fill-box", transformOrigin: "center" }} fill="currentColor">
                <motion.g animate={{ x: gaze.x, y: gaze.y }} transition={spring}>
                  {[20, 38].map((cx) => (
                    <motion.circle
                      key={cx}
                      cx={cx}
                      cy={16}
                      r={4.6}
                      style={{ transformBox: "fill-box", transformOrigin: "center" }}
                      animate={{ scaleY: blink ? 0.12 : current === "listening" ? 1.1 : 1 }}
                      transition={{ duration: blink ? 0.07 : 0.12, ease: "easeOut" }}
                    />
                  ))}
                </motion.g>
              </motion.g>
            )}
          </AnimatePresence>
          <motion.path
            initial={false}
            animate={{ d: mouth }}
            transition={{ duration: 0.32, ease: "easeInOut" }}
            fill="none"
            stroke="currentColor"
            strokeWidth={2.6}
            strokeLinecap="round"
          />
        </svg>
      </span>
      <AnimatePresence>{current === "thinking" && <ThoughtBubble still={reduced} />}</AnimatePresence>
    </span>
  );
}

/** Agent's face as a small, still icon for links and buttons that mention Agent (where a sparkle used to be). */
export function PebbleIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 22" className={cn("size-4 shrink-0", className)} aria-hidden>
      <path d="M12.3 1.4c5.7.1 10.4 3.7 10.3 9.7-.1 5.9-4.8 9.5-10.7 9.5C6 20.6 1.4 17.3 1.4 11.2 1.4 5.1 6.4 1.3 12.3 1.4Z" className="fill-current" />
      <circle cx="8.7" cy="9.9" r="1.65" className="fill-background" />
      <circle cx="15.3" cy="9.5" r="1.65" className="fill-background" />
      <path d="M10.1 14c1.2.95 2.8.95 4 0" fill="none" stroke="var(--background)" strokeWidth="1.35" strokeLinecap="round" />
    </svg>
  );
}
