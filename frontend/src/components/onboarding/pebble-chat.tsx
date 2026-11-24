"use client";

import { Fragment, useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";

import { AgentMark, type AgentMood } from "@/components/agent/agent-mark";
import { usePrefersReducedMotion } from "@/hooks/use-prefers-reduced-motion";
import { cn } from "@/lib/utils";

/*
 * Agent's scripted voice in onboarding: lines stream in like a reply, Agent's face thinks before
 * each one, and cards (examples, a video, the plans) sit between the lines. Nothing here calls a
 * model; every line is translated copy, so it is instant, free and the same for everyone.
 */

export type ChatItem =
  | { id: number; kind: "agent"; text: string; done: boolean }
  | { id: number; kind: "user"; text: string }
  | { id: number; kind: "node"; render: () => ReactNode };

type NewItem =
  | { kind: "agent"; text: string; done: boolean }
  | { kind: "user"; text: string }
  | { kind: "node"; render: () => ReactNode };

export function usePebbleScript() {
  const reduced = usePrefersReducedMotion();
  const [items, setItems] = useState<ChatItem[]>([]);
  const [mood, setMood] = useState<AgentMood>("idle");
  const run = useRef(0);
  const nextId = useRef(0);

  useEffect(() => () => { run.current += 1; }, []);

  const push = useCallback((item: NewItem) => {
    const id = nextId.current++;
    setItems((current) => [...current, { ...item, id } as ChatItem]);
    return id;
  }, []);

  /** Starts a new beat; earlier beats still playing stop. */
  const begin = useCallback(() => ++run.current, []);
  const alive = useCallback((token: number) => run.current === token, []);

  const sleep = useCallback((ms: number) => new Promise((resolve) => setTimeout(resolve, reduced ? 0 : ms)), [reduced]);

  /** Agent thinks for a moment, then types `text`. False if a newer beat took over. */
  const say = useCallback(async (text: string, token: number, { think = true }: { think?: boolean } = {}) => {
    if (think) {
      setMood("thinking");
      await sleep(520 + Math.min(650, text.length * 5));
      if (run.current !== token) return false;
    }
    setMood("idle");
    const id = push({ kind: "agent", text: "", done: false });
    const step = reduced ? text.length : 3;
    for (let shown = step; shown < text.length + step; shown += step) {
      if (run.current !== token) {
        setItems((current) => current.map((entry) => (entry.id === id && entry.kind === "agent" ? { ...entry, text, done: true } : entry)));
        return false;
      }
      setItems((current) => current.map((entry) => (entry.id === id && entry.kind === "agent" ? { ...entry, text: text.slice(0, shown) } : entry)));
      await sleep(14);
    }
    setItems((current) => current.map((entry) => (entry.id === id && entry.kind === "agent" ? { ...entry, text, done: true } : entry)));
    return run.current === token;
  }, [push, reduced, sleep]);

  const clear = useCallback(() => setItems([]), []);
  const remove = useCallback((id: number) => setItems((current) => current.filter((item) => item.id !== id)), []);

  return { items, mood, setMood, push, say, begin, alive, sleep, clear, remove, reduced: Boolean(reduced) };
}

/** `**bold**` in translated lines. */
function richLine(text: string) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, index) =>
    part.startsWith("**") && part.endsWith("**") ? <strong key={index} className="font-semibold text-foreground">{part.slice(2, -2)}</strong> : <Fragment key={index}>{part}</Fragment>,
  );
}

/**
 * The conversation. `compact` is the docked guide (smaller type, no faces beside lines); the intro
 * shows Agent's face at the start of each run of lines.
 */
export function PebbleMessages({ items, mood, compact = false, thinking }: { items: ChatItem[]; mood: AgentMood; compact?: boolean; thinking?: boolean }) {
  return (
    <div className={cn("flex flex-col", compact ? "gap-2.5" : "gap-3.5")}>
      <AnimatePresence initial={false}>
        {items.map((item, index) => {
          const previous = items[index - 1];
          const firstOfRun = item.kind === "agent" && previous?.kind !== "agent";
          if (item.kind === "user") {
            return (
              <motion.div key={item.id} layout initial={{ opacity: 0, y: 8, scale: 0.96 }} animate={{ opacity: 1, y: 0, scale: 1 }} className="flex justify-end">
                <span className={cn("max-w-[85%] rounded-2xl rounded-br-md bg-foreground text-background", compact ? "px-3 py-1.5 text-[13px]" : "px-4 py-2 text-sm")}>{item.text}</span>
              </motion.div>
            );
          }
          if (item.kind === "node") {
            return (
              <motion.div key={item.id} layout initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ type: "spring", stiffness: 260, damping: 26 }}>
                {item.render()}
              </motion.div>
            );
          }
          return (
            <motion.div key={item.id} layout initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className={cn("flex items-start", compact ? "gap-0" : "gap-3")}>
              {!compact && (
                <span className="w-8 shrink-0 pt-0.5">{firstOfRun && <AgentMark size="sm" mood={index === items.length - 1 ? mood : "idle"} />}</span>
              )}
              <p className={cn("min-w-0 leading-relaxed text-foreground/90", compact ? "text-[13.5px]" : "text-[15px]")}>
                {richLine(item.text)}
                {!item.done && <span className="ml-0.5 inline-block h-[1em] w-[2px] translate-y-[2px] animate-pulse bg-foreground/60" />}
              </p>
            </motion.div>
          );
        })}
      </AnimatePresence>
      {thinking && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className={cn("flex items-center", compact ? "gap-2" : "gap-3")}>
          {!compact && <span className="w-8 shrink-0"><AgentMark size="sm" mood="thinking" /></span>}
          <span className="shimmer-text text-sm text-muted-foreground">…</span>
        </motion.div>
      )}
    </div>
  );
}

/** Tappable answers under the last line. */
export function PebbleChoices({ options, onPick, compact = false }: { options: { id: string; label: string; icon?: ReactNode; primary?: boolean }[]; onPick: (id: string) => void; compact?: boolean }) {
  return (
    <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className={cn("flex flex-wrap gap-2", compact ? "" : "pl-11")}>
      {options.map((option, index) => (
        <motion.button
          key={option.id}
          type="button"
          initial={{ opacity: 0, y: 6, scale: 0.95 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          transition={{ delay: index * 0.05, type: "spring", stiffness: 320, damping: 24 }}
          whileTap={{ scale: 0.96 }}
          onClick={() => onPick(option.id)}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full border font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            compact ? "h-8 px-3 text-[13px]" : "h-9 px-3.5 text-sm",
            option.primary ? "border-foreground bg-foreground text-background hover:bg-foreground/90" : "bg-background hover:border-foreground/30 hover:bg-accent",
          )}
        >
          {option.icon}
          {option.label}
        </motion.button>
      ))}
    </motion.div>
  );
}
