"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { AnimatePresence, motion } from "motion/react";
import { ArrowRight, BarChart3, CalendarDays, Film, ListOrdered, Rocket, Scissors, type LucideIcon } from "lucide-react";
import { toast } from "sonner";

import { AgentComposer } from "@/components/agent/agent-composer";
import { TrialNote, useAgentGate } from "@/components/agent/agent-upgrade";
import { ShowcaseExamples } from "@/components/onboarding/showcase-examples";
import { AgentMark, PebbleIcon, type AgentMood } from "@/components/agent/agent-mark";
import { usePrefersReducedMotion } from "@/hooks/use-prefers-reduced-motion";
import { startConversation, type Attachment } from "@/lib/agent";
import { cn } from "@/lib/utils";


type Choice = "examples" | "abilities" | "start";

type Item =
  | { id: number; kind: "agent"; text: string }
  | { id: number; kind: "user"; text: string }
  | { id: number; kind: "examples" }
  | { id: number; kind: "abilities" }
  | { id: number; kind: "choices"; options: Choice[] };

/** Omit for each member of a union (a plain Omit would merge them). */
type DistributiveOmit<T, K extends PropertyKey> = T extends unknown ? Omit<T, K> : never;
type NewItem = DistributiveOmit<Item, "id">;

const CHOICE_ICONS: Record<Choice, LucideIcon | typeof PebbleIcon> = { examples: Film, abilities: PebbleIcon, start: Rocket };
const ABILITIES: { key: string; icon: LucideIcon }[] = [
  { key: "series", icon: ListOrdered },
  { key: "schedule", icon: CalendarDays },
  { key: "edit", icon: Scissors },
  { key: "stats", icon: BarChart3 },
];

function AbilitiesCard({ onPick }: { onPick: (text: string) => void }) {
  const t = useTranslations("agent.onboarding.welcome");
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {ABILITIES.map(({ key, icon: Icon }, index) => (
        <motion.button
          key={key}
          type="button"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: index * 0.07, duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
          onClick={() => onPick(t(`abilities.${key}.prompt`))}
          className="group flex items-start gap-3 rounded-2xl border bg-background p-3.5 text-left transition-all hover:-translate-y-0.5 hover:border-foreground/25 hover:shadow-sm"
        >
          <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-muted"><Icon className="size-4" /></span>
          <span className="min-w-0">
            <span className="block text-sm font-medium">{t(`abilities.${key}.title`)}</span>
            <span className="block text-xs leading-snug text-muted-foreground">“{t(`abilities.${key}.prompt`)}”</span>
          </span>
          <ArrowRight className="ml-auto mt-1 size-3.5 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
        </motion.button>
      ))}
    </div>
  );
}

/**
 * The first conversation with Agent, played out after someone picks it in onboarding. Agent introduces
 * itself and offers choices; each one plays a short exchange (examples, what it can do, getting started).
 * The composer at the bottom is real: sending starts their first chat.
 */
export function AgentWelcome({ firstName, onFinish }: { firstName?: string; onFinish: () => void }) {
  const t = useTranslations("agent.onboarding.welcome");
  const tAgent = useTranslations("agent");
  const locale = useLocale();
  const router = useRouter();
  const reduced = usePrefersReducedMotion();
  const [items, setItems] = useState<Item[]>([]);
  const [mood, setMood] = useState<AgentMood>("idle");
  const [seed, setSeed] = useState<{ text: string; n: number } | null>(null);
  const [highlightComposer, setHighlightComposer] = useState(false);
  const [starting, setStarting] = useState(false);
  const used = useRef(new Set<Choice>());
  const nextId = useRef(0);
  const run = useRef(0);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  const push = (item: NewItem) => {
    const id = nextId.current++;
    setItems((current) => [...current.filter((entry) => entry.kind !== "choices"), { ...item, id } as Item]);
    return id;
  };

  /** Plays one beat: Agent thinks, then types its words out. False when the person moved on. */
  const say = async (text: string, token: number) => {
    const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, reduced ? 0 : ms));
    setMood("thinking");
    await sleep(650 + Math.min(700, text.length * 6));
    if (run.current !== token) return false;
    setMood("idle");
    const id = push({ kind: "agent", text: "" });
    const step = reduced ? text.length : 2;
    for (let shown = step; shown < text.length + step; shown += step) {
      if (run.current !== token) return false;
      setItems((current) => current.map((entry) => (entry.id === id && entry.kind === "agent" ? { ...entry, text: text.slice(0, shown) } : entry)));
      await sleep(16);
    }
    return true;
  };

  const offer = (choices: Choice[]) => {
    const left = choices.filter((choice) => !used.current.has(choice));
    if (left.length) push({ kind: "choices", options: left });
  };

  // Hello.
  useEffect(() => {
    const token = ++run.current;
    void (async () => {
      if (!(await say(firstName ? t("hello", { name: firstName }) : t("helloNoName"), token))) return;
      if (!(await say(t("intro"), token))) return;
      setMood("happy");
      setTimeout(() => setMood("idle"), 1400);
      offer(["examples", "abilities", "start"]);
    })();
    const runner = run;
    return () => { runner.current += 1; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end", behavior: reduced ? "auto" : "smooth" });
  }, [items, reduced]);

  const choose = async (choice: Choice) => {
    used.current.add(choice);
    const token = ++run.current;
    push({ kind: "user", text: t(`choices.${choice}`) });
    if (choice === "examples") {
      if (!(await say(t("examplesIntro"), token))) return;
      push({ kind: "examples" });
      await new Promise((resolve) => setTimeout(resolve, reduced ? 0 : 900));
      if (!(await say(t("examplesOutro"), token))) return;
      offer(["abilities", "start"]);
    } else if (choice === "abilities") {
      if (!(await say(t("abilitiesIntro"), token))) return;
      push({ kind: "abilities" });
      await new Promise((resolve) => setTimeout(resolve, reduced ? 0 : 600));
      if (!(await say(t("abilitiesOutro"), token))) return;
      offer(["examples", "start"]);
    } else {
      if (!(await say(t("startLine"), token))) return;
      setMood("listening");
      setHighlightComposer(true);
      setSeed({ text: "", n: Date.now() });
      offer(["examples", "abilities"]);
    }
  };

  const tryPrompt = (text: string) => {
    setSeed({ text, n: Date.now() });
    setHighlightComposer(true);
    setMood("listening");
  };

  const gate = useAgentGate("agent_welcome");

  const send = async (message: string, attachments: Attachment[]) => {
    setStarting(true);
    setMood("thinking");
    try {
      const { conversation } = await startConversation(message, attachments, locale);
      onFinish();
      router.push(`/agent/${conversation.id}?new=1`);
      return true;
    } catch (error) {
      if (!gate.handleError(error)) toast.error(error instanceof Error ? error.message : t("sendFailed"));
      setStarting(false);
      setMood("sad");
      setTimeout(() => setMood("idle"), 1600);
      return false;
    }
  };

  return (
    <div data-agent-chat className="relative mx-auto flex min-h-[calc(100dvh-3.5rem)] w-full max-w-2xl flex-col px-4 pb-6 pt-10 sm:px-6 md:min-h-dvh md:pt-[8vh]">
      <button type="button" onClick={onFinish} className="absolute right-4 top-4 text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline sm:right-6">
        {t("skip")}
      </button>

      <motion.div layoutId="agent-hero" className="mx-auto">
        <AgentMark size="lg" follow mood={mood} />
      </motion.div>

      <div className="mt-10 flex-1 space-y-5">
        <AnimatePresence initial={false}>
          {items.map((item) => (
            <motion.div
              key={item.id}
              layout="position"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4, transition: { duration: 0.15 } }}
              transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
            >
              {item.kind === "agent" ? (
                <p className="text-[1.0625rem] leading-relaxed">{item.text}</p>
              ) : item.kind === "user" ? (
                <div className="flex justify-end">
                  <span className="rounded-[1.25rem] rounded-br-md bg-muted px-4 py-2 text-[0.9375rem]">{item.text}</span>
                </div>
              ) : item.kind === "examples" ? (
                <ShowcaseExamples inset={false} />
              ) : item.kind === "abilities" ? (
                <AbilitiesCard onPick={tryPrompt} />
              ) : (
                <div className="flex flex-wrap gap-2">
                  {item.options.map((choice) => {
                    const Icon = CHOICE_ICONS[choice];
                    return (
                      <button
                        key={choice}
                        type="button"
                        onClick={() => void choose(choice)}
                        className={cn(
                          "inline-flex h-10 items-center gap-2 rounded-full border bg-background px-4 text-sm font-medium shadow-xs transition-all hover:-translate-y-px hover:border-foreground/30 hover:shadow-sm",
                          choice === "start" && "border-foreground bg-foreground text-background hover:bg-foreground/90",
                        )}
                      >
                        <Icon className="size-4" />{t(`choices.${choice}`)}
                      </button>
                    );
                  })}
                </div>
              )}
            </motion.div>
          ))}
        </AnimatePresence>
        {/* While Agent thinks, its small self shows where the reply will come (the big one may be scrolled away). */}
        <AnimatePresence>
          {mood === "thinking" && items.length > 0 && (
            <motion.div
              key="thinking"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, transition: { duration: 0.12 } }}
              className="flex items-center gap-3 pt-3"
            >
              <AgentMark busy />
              <span className="shimmer-text text-sm text-muted-foreground">{tAgent("thinking")}</span>
            </motion.div>
          )}
        </AnimatePresence>
        <div ref={bottomRef} className="scroll-mb-44" />
      </div>

      <div className="sticky bottom-[calc(4.5rem+env(safe-area-inset-bottom))] mt-8 before:pointer-events-none before:absolute before:inset-x-0 before:-top-10 before:h-10 before:bg-gradient-to-t before:from-canvas before:to-transparent md:bottom-4">
        <motion.div
          animate={highlightComposer ? { boxShadow: ["0 0 0 0 rgb(0 0 0 / 0)", "0 0 0 6px color-mix(in oklch, var(--brand) 22%, transparent)", "0 0 0 0 rgb(0 0 0 / 0)"] } : {}}
          transition={{ duration: 1.6, repeat: highlightComposer ? 2 : 0 }}
          className="rounded-[1.35rem]"
          onFocusCapture={() => setMood((current) => (current === "idle" ? "listening" : current))}
          onBlurCapture={() => setMood((current) => (current === "listening" ? "idle" : current))}
        >
          <AgentComposer onSend={send} disabled={starting} size="large" seed={seed} placeholder={t("composerPlaceholder")} />
        </motion.div>
        <TrialNote status={gate.status} onUpgrade={gate.show} className="mt-2.5" />
        {gate.dialog}
      </div>
    </div>
  );
}
