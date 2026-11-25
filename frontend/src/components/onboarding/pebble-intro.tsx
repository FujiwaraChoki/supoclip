"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { motion } from "motion/react";
import { Gamepad2, Mic, MonitorPlay, Presentation, Sparkles, Wand2, type LucideIcon } from "lucide-react";

import { AgentMark } from "@/components/agent/agent-mark";
import { AppShell } from "@/components/app/app-shell";
import { PebbleChoices, PebbleMessages, usePebbleScript } from "@/components/onboarding/pebble-chat";
import { ShowcaseExamples } from "@/components/onboarding/showcase-examples";
import { trackEvent } from "@/lib/analytics";
import { useSession } from "@/lib/auth-client";
import { MAKES, saveMakes, type Makes, type OnboardingStatus } from "@/lib/onboarding";

const MAKES_ICONS: Record<Makes, LucideIcon> = { podcasts: Mic, streams: Gamepad2, youtube: MonitorPlay, talks: Presentation, other: Sparkles };
type Beat = "makes" | "after" | "none";

/**
 * The first thing a new account sees: Agent says hello, asks what they make,
 * shows clips like theirs, and offers to make their first one free. Then it hands over to the
 * docked guide on the real create screen (`onStart`).
 */
export function PebbleIntro({ status, onStart, onSkip }: { status: OnboardingStatus; onStart: (makes: Makes | undefined) => void; onSkip: () => void }) {
  const t = useTranslations("onboarding.intro");
  const { data: session } = useSession();
  const firstName = session?.user?.name?.trim().split(/\s+/)[0];
  const script = usePebbleScript();
  const { say, begin, push } = script;
  const [beat, setBeat] = useState<Beat>("none");
  const [makes, setMakes] = useState<Makes | undefined>(status.answers.makes);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const free = status.preview.eligible;

  useEffect(() => {
    trackEvent("onboarding_step", { step: "intro" });
    const token = begin();
    void (async () => {
      if (!(await say(firstName ? t("hello", { name: firstName }) : t("helloNoName"), token))) return;
      if (!(await say(t(free ? "pitchFree" : "pitch"), token))) return;
      if (!(await say(t("askMakes"), token))) return;
      setBeat("makes");
    })();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end", behavior: script.reduced ? "auto" : "smooth" });
  }, [script.items, beat, script.reduced]);

  const pickMakes = async (value: Makes) => {
    setBeat("none");
    setMakes(value);
    void saveMakes(value).catch(() => {});
    trackEvent("onboarding_makes_chosen", { makes: value });
    const token = begin();
    push({ kind: "user", text: t(`makes.${value}`) });
    if (!(await say(t(`reply.${value}`), token))) return;
    if (!(await say(t("showcase"), token))) return;
    push({ kind: "node", render: () => <ShowcaseExamples /> });
    trackEvent("onboarding_examples_opened", { makes: value });
    await script.sleep(900);
    if (!(await say(t(free ? "offerFree" : "offer"), token))) return;
    script.setMood("happy");
    setTimeout(() => script.setMood("idle"), 1500);
    setBeat("after");
  };

  const after = async () => {
    setBeat("none");
    push({ kind: "user", text: t(free ? "startFree" : "start") });
    script.setMood("happy");
    await script.sleep(450);
    onStart(makes);
  };

  return (
    <AppShell>
      <div className="relative isolate overflow-x-clip">
        <div aria-hidden className="pointer-events-none absolute inset-0 -z-10">
          <div className="absolute left-1/2 top-[-12%] h-[30rem] w-[56rem] -translate-x-1/2 rounded-full bg-[radial-gradient(closest-side,color-mix(in_oklch,var(--brand)_9%,transparent),transparent)]" />
          <div className="absolute inset-0 bg-[radial-gradient(var(--border)_1px,transparent_1px)] [background-size:22px_22px] [mask-image:radial-gradient(ellipse_70%_55%_at_50%_25%,black,transparent)] opacity-50" />
        </div>
        <main data-agent-chat className="relative mx-auto flex min-h-[calc(100dvh-3.5rem)] w-full max-w-2xl flex-col px-4 pb-16 pt-10 sm:px-6 md:min-h-dvh md:pt-[9vh]">
          <button type="button" onClick={onSkip} className="absolute right-4 top-4 text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline sm:right-6">
            {t("skip")}
          </button>
          <motion.div layoutId="pebble-guide" className="mx-auto" initial={{ opacity: 0, scale: 0.6 }} animate={{ opacity: 1, scale: 1 }} transition={{ type: "spring", stiffness: 260, damping: 18 }}>
            <AgentMark size="lg" follow mood={script.mood} />
          </motion.div>
          <div className="mt-10 space-y-5" aria-live="polite">
            <PebbleMessages items={script.items} mood={script.mood} />
            {beat === "makes" && (
              <PebbleChoices
                options={MAKES.map((value) => {
                  const Icon = MAKES_ICONS[value];
                  return { id: value, label: t(`makes.${value}`), icon: <Icon className="size-4" /> };
                })}
                onPick={(id) => void pickMakes(id as Makes)}
              />
            )}
            {beat === "after" && (
              <PebbleChoices
                options={[{ id: "start", label: t(free ? "startFree" : "start"), icon: <Wand2 className="size-4" />, primary: true }]}
                onPick={() => void after()}
              />
            )}
          </div>
          <div ref={bottomRef} className="h-px scroll-mb-24" />
        </main>
      </div>
    </AppShell>
  );
}
