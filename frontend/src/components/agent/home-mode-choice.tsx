"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { AnimatePresence, motion } from "motion/react";
import { ArrowRight, Check } from "lucide-react";
import { toast } from "sonner";

import { AgentMark } from "@/components/agent/agent-mark";
import { AgentFilm } from "@/components/agent/onboarding/agent-film";
import { AgentWelcome } from "@/components/agent/onboarding/agent-welcome";
import { ManualFilm } from "@/components/agent/onboarding/manual-film";
import { ManualWelcome } from "@/components/agent/onboarding/manual-welcome";
import { AppShell } from "@/components/app/app-shell";
import { useSession } from "@/lib/auth-client";
import { saveHomeMode, trialLeft, type AgentStatus, type HomeMode } from "@/lib/agent";
import { useAgentUpgrade } from "@/components/agent/agent-upgrade";
import { cn } from "@/lib/utils";

const EASE = [0.16, 1, 0.3, 1] as const;

type Gate = { kind: "full" } | { kind: "trial"; left: number } | { kind: "locked"; trialUsed: boolean };

function gateFor(status: AgentStatus): Gate {
  if (status.available && status.access === "trial") return { kind: "trial", left: trialLeft(status) ?? 0 };
  if (status.available) return { kind: "full" };
  return { kind: "locked", trialUsed: status.reason === "trial_used" };
}

function ChoiceCard({
  mode,
  primary,
  tag,
  cta,
  onChoose,
  index,
}: {
  mode: HomeMode;
  /** The suggested one: stronger border and a filled button. */
  primary?: boolean;
  /** A small label above the card: "Recommended", or the plan it comes with. */
  tag?: { label: string; plan?: boolean };
  cta: string;
  onChoose: () => void;
  index: number;
}) {
  const t = useTranslations("agent");
  const [hovered, setHovered] = useState(false);
  return (
    <motion.button
      type="button"
      onClick={onChoose}
      onPointerEnter={() => setHovered(true)}
      onPointerLeave={() => setHovered(false)}
      initial={{ opacity: 0, y: 24 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.6, delay: 0.15 + index * 0.1, ease: EASE }}
      whileHover={{ y: -6 }}
      whileTap={{ scale: 0.985 }}
      className={cn(
        "group relative flex flex-col rounded-[28px] border bg-card p-2 text-left shadow-[0_30px_80px_-40px_rgb(28_25_23/0.45)] transition-[border-color,box-shadow] duration-300 hover:shadow-[0_40px_100px_-40px_rgb(28_25_23/0.55)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        primary ? "border-foreground/15 hover:border-foreground/35" : "hover:border-foreground/25",
      )}
    >
      {tag && (
        <span
          className={cn(
            "absolute -top-3 left-6 z-10 flex items-center gap-1.5 rounded-full border bg-background px-2.5 py-1 text-[11px] font-medium shadow-xs",
            tag.plan ? "text-muted-foreground" : "border-brand/25 text-brand",
          )}
        >
          {!tag.plan && <span className="size-1.5 rounded-full bg-brand" />}
          {tag.label}
        </span>
      )}
      <div className="relative overflow-hidden rounded-[22px] border bg-canvas">
        {mode === "agent" ? <AgentFilm active /> : <ManualFilm active />}
        <div className="pointer-events-none absolute inset-0 rounded-[22px] ring-1 ring-inset ring-black/5 dark:ring-white/5" />
      </div>
      <div className="flex flex-1 flex-col px-4 pb-4 pt-5 sm:px-5">
        <span className="font-display text-xl font-bold tracking-tight sm:text-2xl">{t(`choice.${mode}.title`)}</span>
        <span className="mt-1.5 text-sm leading-relaxed text-muted-foreground">{t(`choice.${mode}.body`)}</span>
        <span className="mt-4 space-y-2">
          {[0, 1, 2].map((perk) => (
            <span key={perk} className="flex items-start gap-2.5 text-sm">
              <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full bg-muted"><Check className="size-2.5" /></span>
              {t(`choice.${mode}.perks.${perk}`)}
            </span>
          ))}
        </span>
        <span
          className={cn(
            "mt-6 flex h-11 items-center justify-center gap-2 rounded-full text-sm font-medium transition-all",
            primary ? "bg-foreground text-background" : "border bg-background",
          )}
        >
          {cta}
          <motion.span animate={{ x: hovered ? 3 : 0 }}><ArrowRight className="size-4" /></motion.span>
        </span>
      </div>
    </motion.button>
  );
}

/**
 * Asked once, on the first visit: does home open on the agent or the create form? Each choice
 * plays a short film of what it's like, and picking one plays a welcome before home opens.
 */
export function HomeModeChoice({ status, onChosen }: { status: AgentStatus; onChosen: (mode: HomeMode) => void }) {
  const t = useTranslations("agent");
  const gate = gateFor(status);
  const upgrade = useAgentUpgrade("home_choice");
  const { data: session } = useSession();
  const firstName = session?.user?.name?.trim().split(/\s+/)[0];
  const [picked, setPicked] = useState<HomeMode | null>(null);

  const choose = (mode: HomeMode) => {
    setPicked(mode);
    // Saved now, so a reload doesn't ask again; home switches when the welcome ends.
    saveHomeMode(mode).catch(() => toast.error(t("choice.saveFailed")));
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  return (
    <AppShell>
      <div className="relative isolate overflow-x-clip">
        {/* Soft light and a faint dot grid behind everything. */}
        <div aria-hidden className="pointer-events-none absolute inset-0 -z-10">
          <div className="absolute left-1/2 top-[-10%] h-[34rem] w-[60rem] -translate-x-1/2 rounded-full bg-[radial-gradient(closest-side,color-mix(in_oklch,var(--brand)_10%,transparent),transparent)]" />
          <div className="absolute inset-0 bg-[radial-gradient(var(--border)_1px,transparent_1px)] [background-size:22px_22px] [mask-image:radial-gradient(ellipse_70%_60%_at_50%_30%,black,transparent)] opacity-60" />
        </div>

        <AnimatePresence mode="wait">
          {picked === null ? (
            <motion.main
              key="choose"
              exit={{ opacity: 0, y: -16, filter: "blur(6px)" }}
              transition={{ duration: 0.4, ease: EASE }}
              className="mx-auto flex w-full max-w-6xl flex-col items-center px-4 pb-24 pt-10 sm:px-8 md:pt-[9vh]"
            >
              <motion.div layoutId="agent-hero" initial={{ opacity: 0, scale: 0.6 }} animate={{ opacity: 1, scale: 1 }} transition={{ type: "spring", stiffness: 260, damping: 18 }}>
                <AgentMark size="lg" follow />
              </motion.div>
              <motion.h1
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.55, delay: 0.05, ease: EASE }}
                className="mt-6 text-center font-display text-4xl font-bold tracking-tight sm:text-[3.4rem] sm:leading-[1.05]"
              >
                {t("choice.title")}
              </motion.h1>
              <motion.p
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ duration: 0.5, delay: 0.12 }}
                className="mt-4 max-w-xl text-center text-base text-muted-foreground sm:text-lg"
              >
                {gate.kind === "full" ? t("choice.subtitle") : t("choice.subtitleGated")}
              </motion.p>

              <div className="mt-12 grid w-full gap-6 lg:grid-cols-2">
                {gate.kind === "full" ? (
                  <>
                    <ChoiceCard mode="agent" primary tag={{ label: t("choice.recommended") }} cta={t("choice.agent.cta")} index={0} onChoose={() => choose("agent")} />
                    <ChoiceCard mode="manual" cta={t("choice.manual.cta")} index={1} onChoose={() => choose("manual")} />
                  </>
                ) : (
                  // Without Creator the form is the way in, so it leads; the agent is the step up.
                  <>
                    <ChoiceCard
                      mode="manual"
                      primary
                      tag={{ label: status.plan === "pro" ? t("choice.includedInPro") : t("choice.proTag"), plan: true }}
                      cta={t("choice.manual.cta")}
                      index={0}
                      onChoose={() => choose("manual")}
                    />
                    <ChoiceCard
                      mode="agent"
                      tag={{ label: t("choice.creatorTag"), plan: true }}
                      cta={gate.kind === "trial" ? t("choice.trial.cta", { count: gate.left }) : t("choice.locked.cta")}
                      index={1}
                      // Picking the agent without Creator shows the plans; nothing is saved until it's theirs.
                      onChoose={() => (gate.kind === "locked" ? upgrade.show(gate.trialUsed) : choose("agent"))}
                    />
                  </>
                )}
              </div>
              <motion.p initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.5 }} className="mt-8 text-center text-xs text-muted-foreground">
                {t("choice.changeLater")}
              </motion.p>
            </motion.main>
          ) : (
            <motion.div key={picked} initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5, ease: EASE }}>
              {picked === "agent" ? (
                <AgentWelcome firstName={firstName} onFinish={() => onChosen("agent")} />
              ) : (
                <ManualWelcome onFinish={() => onChosen("manual")} />
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
      {upgrade.dialog}
    </AppShell>
  );
}
