"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "motion/react";
import { ArrowRight, ChevronDown, Clapperboard, LayoutPanelTop, Loader2, X } from "lucide-react";
import { toast } from "sonner";

import { AgentMark } from "@/components/agent/agent-mark";
import { Spotlight, bringIntoView } from "@/components/feature-tour";
import { PebbleChoices, PebbleMessages, usePebbleScript } from "@/components/onboarding/pebble-chat";
import { PlanCard } from "@/components/onboarding/plan-card";
import { usePebbleFlow, type PebbleStage } from "@/components/onboarding/pebble-flow";
import { useAgentStatus } from "@/hooks/use-agent-status";
import { useBillingSummary } from "@/hooks/use-billing-summary";
import { agentKeys, saveHomeMode, trialLeft, type AgentStatus, type HomeMode } from "@/lib/agent";
import { trackEvent } from "@/lib/analytics";
import { trackTillyGoal } from "@/lib/datafast";
import { getPublicBillingPlans } from "@/lib/billing-plans";
import {
  completeOnboarding,
  estimateClips,
  formatRuntime,
  lookAtLink,
  onboardingKeys,
  previewOf,
  suggestedPlan,
  type LinkLook,
  type OnboardingStatus,
  type PreviewState,
} from "@/lib/onboarding";
import { onPebble } from "@/lib/pebble-events";
import { hasPaidAccess } from "@/lib/subscription-status";

type TaskRow = { id: string; status: string; progress?: number; progress_message?: string; preview?: unknown; source_title?: string };
type ClipRow = { id: string; hook_title?: string | null; virality_score?: number | null };

const ACTIVE: PebbleStage[] = ["link", "making", "ready", "paid", "choose", "done"];

async function fetchTask(taskId: string): Promise<{ task: TaskRow; clips: ClipRow[] }> {
  const [taskResponse, clipsResponse] = await Promise.all([
    fetch(`/api/tasks/${taskId}`, { cache: "no-store" }),
    fetch(`/api/tasks/${taskId}/clips`, { cache: "no-store" }),
  ]);
  if (!taskResponse.ok) throw Object.assign(new Error(String(taskResponse.status)), { status: taskResponse.status });
  const task = (await taskResponse.json()) as TaskRow;
  const clips = clipsResponse.ok ? (((await clipsResponse.json()) as { clips?: ClipRow[] }).clips ?? []) : [];
  return { task, clips };
}

/** The element a step points at, once it is on the page. */
function useTarget(name: string | null) {
  const [element, setElement] = useState<HTMLElement | null>(null);
  useEffect(() => {
    setElement(null);
    if (!name) return;
    let tries = 0;
    const find = () => {
      const found = Array.from(document.querySelectorAll<HTMLElement>(`[data-tour="${name}"]`)).find((node) => node.getClientRects().length > 0);
      if (found) {
        setElement(found);
        bringIntoView(found);
        return;
      }
      if (++tries < 40) timer = window.setTimeout(find, 250);
    };
    let timer = window.setTimeout(find, 200);
    return () => window.clearTimeout(timer);
  }, [name]);
  return element;
}

/** Progress in Agent's words (the worker's own messages are English and technical). */
function phaseOf(progress: number) {
  if (progress < 15) return "fetch";
  if (progress < 45) return "listen";
  if (progress < 70) return "find";
  if (progress < 97) return "cut";
  return "finish";
}

function MakingCard({ task }: { task: TaskRow | undefined }) {
  const t = useTranslations("onboarding.dock.making");
  const progress = Math.max(4, Math.min(100, task?.progress ?? 0));
  const phase = phaseOf(progress);
  return (
    <div className="rounded-2xl border bg-card p-3.5 shadow-[0_14px_40px_-28px_rgb(28_25_23/0.5)]">
      <div className="flex items-center gap-2 text-[13px] font-medium">
        <Loader2 className="size-3.5 animate-spin text-muted-foreground" />
        <AnimatePresence mode="wait">
          <motion.span key={phase} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} className="shimmer-text">
            {t(`phase.${phase}`)}
          </motion.span>
        </AnimatePresence>
        <span className="ml-auto tabular-nums text-muted-foreground">{Math.round(progress)}%</span>
      </div>
      <div className="mt-2.5 h-1.5 overflow-hidden rounded-full bg-muted">
        <motion.div className="h-full rounded-full bg-brand" animate={{ width: `${progress}%` }} transition={{ type: "spring", stiffness: 60, damping: 20 }} />
      </div>
    </div>
  );
}

function VideoCard({ look }: { look: LinkLook }) {
  const t = useTranslations("onboarding.dock.link");
  return (
    <div className="flex items-center gap-3 rounded-2xl border bg-card p-2 pr-3 shadow-[0_14px_40px_-28px_rgb(28_25_23/0.5)]">
      <div className="relative aspect-video w-24 shrink-0 overflow-hidden rounded-xl bg-muted">
        {look.thumbnail_url && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={look.thumbnail_url} alt="" className="size-full object-cover" />
        )}
        <span className="absolute bottom-1 right-1 rounded bg-black/75 px-1 text-[10px] font-medium tabular-nums text-white">{formatRuntime(look.duration_seconds)}</span>
      </div>
      <div className="min-w-0">
        <p className="line-clamp-2 text-[13px] font-medium leading-snug">{look.title ?? t("untitled")}</p>
        {look.estimated_clips ? <p className="mt-0.5 text-[11.5px] text-muted-foreground">{t("estimate", { count: look.estimated_clips })}</p> : null}
      </div>
    </div>
  );
}

function HomeChoice({ onPick }: { onPick: (mode: HomeMode) => void }) {
  const t = useTranslations("onboarding.dock.choose");
  return (
    <div className="grid grid-cols-2 gap-2">
      {(["agent", "manual"] as const).map((mode) => (
        <button
          key={mode}
          type="button"
          onClick={() => onPick(mode)}
          className="flex flex-col items-start gap-2 rounded-2xl border bg-card p-3 text-left transition-colors hover:border-foreground/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {mode === "agent" ? <AgentMark size="sm" /> : <span className="flex size-7 items-center justify-center rounded-full bg-muted"><LayoutPanelTop className="size-3.5" /></span>}
          <span className="text-[13px] font-semibold">{t(`${mode}.title`)}</span>
          <span className="text-[11.5px] leading-snug text-muted-foreground">{t(`${mode}.body`)}</span>
        </button>
      ))}
    </div>
  );
}

/**
 * Agent, docked in the corner while it walks a new account through its first clip: paste a link,
 * the free preview, the plans, and the rest of the clips after checkout. It follows across pages
 * and points at what it talks about.
 */
export function PebbleDock({ status, userId, pathname, navigate }: { status: OnboardingStatus; userId: string; pathname: string; navigate: (path: string) => void }) {
  const t = useTranslations("onboarding.dock");
  const queryClient = useQueryClient();
  const [flow, update] = usePebbleFlow(userId);
  const script = usePebbleScript();
  const { say, begin, push, clear } = script;
  const [billing] = useBillingSummary(true);
  const agentStatus = useAgentStatus();
  const [chips, setChips] = useState<{ id: string; label: string; primary?: boolean }[]>([]);
  const [look, setLook] = useState<LinkLook | null>(flow.link ?? null);
  const [target, setTarget] = useState<string | null>(null);
  const scroller = useRef<HTMLDivElement | null>(null);
  const plans = useMemo(() => getPublicBillingPlans(), []);
  const stage = flow.stage;
  const paid = hasPaidAccess(billing?.plan, billing?.subscription_status, billing?.subscription_provider);
  const monetized = status.monetized;
  const taskPath = flow.taskId ? `/tasks/${flow.taskId}` : null;
  const onTaskPage = Boolean(taskPath && pathname === taskPath);

  const watching = stage === "making" || stage === "ready" || stage === "paid";
  const taskQuery = useQuery({
    queryKey: ["pebble-task", flow.taskId],
    queryFn: () => fetchTask(flow.taskId!),
    enabled: Boolean(flow.taskId) && watching,
    refetchInterval: stage === "making" || stage === "paid" ? 4000 : false,
  });
  const task = taskQuery.data?.task;
  const preview: PreviewState | null = previewOf(task);
  const bestClip = useMemo(() => {
    const clips = taskQuery.data?.clips ?? [];
    return [...clips].sort((a, b) => (b.virality_score ?? 0) - (a.virality_score ?? 0))[0];
  }, [taskQuery.data]);
  const more = Math.max(0, (preview?.found ?? 0) - 1);
  const suggested = suggestedPlan(plans, flow.makes, look?.duration_seconds ?? null);

  const setStage = useCallback((next: PebbleStage, extra: Parameters<typeof update>[0] = {}) => {
    update({ ...extra, stage: next, minimized: false });
  }, [update]);

  const finish = useCallback((skipped: boolean) => {
    trackEvent("onboarding_completed", { skipped, stage });
    if (!skipped) trackTillyGoal("onboarding_completed");
    void completeOnboarding();
    queryClient.setQueryData<OnboardingStatus | null>(onboardingKeys.status, (current) => (current ? { ...current, completed: true } : current));
    update({ stage: "done", minimized: false });
  }, [queryClient, stage, update]);

  // Keep the newest line in view.
  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: script.reduced ? "auto" : "smooth" });
  }, [script.items, chips, script.reduced]);

  useEffect(() => { trackEvent("onboarding_step", { step: stage }); }, [stage]);

  // ---- link: paste a link on the create screen ------------------------------------------------
  const describe = useCallback(async (url: string) => {
    const token = begin();
    setChips([]);
    setTarget(null);
    script.setMood("thinking");
    let result: LinkLook;
    try {
      result = await lookAtLink(url);
    } catch (error) {
      if (!script.alive(token)) return;
      script.setMood("sad");
      await say((error as Error).message || t("link.lookFailed"), token, { think: false });
      setTarget("composer-source");
      return;
    }
    if (!script.alive(token)) return;
    setLook(result);
    update({ link: result });
    trackEvent("onboarding_link_checked", {
      eligible: result.preview.eligible,
      fits: result.preview.fits,
      duration_minutes: Math.round(result.duration_seconds / 60),
      estimated_clips: result.estimated_clips ?? 0,
    });
    push({ kind: "node", render: () => <VideoCard look={result} /> });
    const runtime = formatRuntime(result.duration_seconds);
    if (result.preview.eligible && result.preview.fits) {
      if (!(await say(t("link.free", { count: result.estimated_clips ?? 1, runtime }), token))) return;
      setTarget("composer-generate");
      script.setMood("happy");
      setTimeout(() => script.setMood("idle"), 1400);
      return;
    }
    if (!monetized || paid || result.preview.reason === "not_needed") {
      if (!(await say(t("link.paid", { count: result.estimated_clips ?? 1, runtime }), token))) return;
      setTarget("composer-generate");
      return;
    }
    const why = !result.preview.fits ? "tooLong" : result.preview.reason ?? "unavailable";
    if (!(await say(t(`link.refused.${why}`, { minutes: result.preview.max_minutes, runtime }), token))) return;
    trackEvent("onboarding_plans_shown", { suggested: suggestedPlan(plans, flow.makes, result.duration_seconds), where: "refused" });
    push({ kind: "node", render: () => <PlanCard compact suggested={suggestedPlan(plans, flow.makes, result.duration_seconds)} returnTo="/" /> });
  }, [begin, flow.makes, monetized, paid, plans, push, say, script, t, update]);

  useEffect(() => onPebble("pebble:source", ({ url }) => { if (stage === "link") void describe(url); }), [describe, stage]);
  // A file: the browser read its length, so nothing to look up.
  useEffect(() => onPebble("pebble:file", async ({ durationSeconds }) => {
    if (stage !== "link") return;
    const token = begin();
    setChips([]);
    setTarget(null);
    const count = estimateClips(durationSeconds);
    const runtime = formatRuntime(durationSeconds);
    const fits = durationSeconds <= status.preview.max_minutes * 60;
    if (status.preview.eligible && fits) {
      if (!(await say(t("link.free", { count, runtime }), token))) return;
      setTarget("composer-generate");
    } else if (!monetized || paid) {
      if (!(await say(t("link.paid", { count, runtime }), token))) return;
      setTarget("composer-generate");
    } else {
      const why = !fits ? "tooLong" : status.preview.reason ?? "unavailable";
      if (!(await say(t(`link.refused.${why}`, { minutes: status.preview.max_minutes, runtime }), token))) return;
      push({ kind: "node", render: () => <PlanCard compact suggested={suggestedPlan(plans, flow.makes, durationSeconds)} returnTo="/" /> });
    }
  }), [begin, flow.makes, monetized, paid, plans, push, say, stage, status.preview, t]);
  useEffect(() => onPebble("pebble:preview-started", ({ taskId }) => {
    trackEvent("preview_started", { duration_minutes: Math.round((look?.duration_seconds ?? 0) / 60) });
    setStage("making", { taskId });
  }), [look, setStage]);
  useEffect(() => onPebble("pebble:preview-error", async ({ code }) => {
    const token = begin();
    script.setMood("sad");
    const line = code === "VIDEO_TOO_LONG"
      ? t("link.refused.tooLong", { minutes: status.preview.max_minutes, runtime: look ? formatRuntime(look.duration_seconds) : "" })
      : t("link.refused.unavailable");
    if (!(await say(line, token, { think: false }))) return;
    push({ kind: "node", render: () => <PlanCard compact suggested={suggestedPlan(plans, flow.makes, look?.duration_seconds ?? null)} returnTo="/" /> });
  }), [begin, flow.makes, look, plans, push, say, script, status.preview.max_minutes, t]);
  useEffect(() => onPebble("pebble:paid", ({ taskId }) => { setStage("paid", { taskId }); }), [setStage]);

  // A plain (paid) project started from the dock's link step is followed the same way.
  useEffect(() => {
    if (stage !== "link" || !pathname.startsWith("/tasks/")) return;
    const id = pathname.split("/")[2];
    if (id && id !== flow.taskId) setStage("making", { taskId: id });
  }, [flow.taskId, pathname, setStage, stage]);

  // ---- each stage's lines --------------------------------------------------------------------
  useEffect(() => {
    if (!ACTIVE.includes(stage)) return;
    const token = begin();
    clear();
    setChips([]);
    setTarget(null);
    void (async () => {
      if (stage === "link") {
        if (pathname !== "/") {
          if (!(await say(t("link.elsewhere"), token))) return;
          setChips([{ id: "home", label: t("link.goHome"), primary: true }]);
          return;
        }
        if (!(await say(t(status.preview.eligible ? "link.pasteFree" : "link.paste"), token))) return;
        setTarget("composer-source");
        if (look) {
          push({ kind: "node", render: () => <VideoCard look={look} /> });
          setTarget("composer-generate");
        }
      } else if (stage === "making") {
        if (!(await say(t("making.started"), token))) return;
        push({ kind: "node", render: () => <MakingCardLive taskId={flow.taskId} /> });
        if (!(await say(t("making.wait"), token))) return;
      } else if (stage === "ready") {
        // Lines come from the effect below, once the clip is known.
      } else if (stage === "paid") {
        if (!(await say(t("paid.thanks"), token))) return;
        push({ kind: "node", render: () => <MakingCardLive taskId={flow.taskId} /> });
      } else if (stage === "choose") {
        const full = agentStatus?.available && agentStatus.access === "full";
        if (full) {
          if (!(await say(t("choose.ask"), token))) return;
          push({ kind: "node", render: () => <HomeChoice onPick={(mode) => void pickHome(mode)} /> });
        } else {
          if (!(await say(t("choose.allSet"), token))) return;
          const trial = agentStatus?.access === "trial" ? trialLeft(agentStatus) : null;
          setChips([
            ...(trial ? [{ id: "trial", label: t("choose.tryAgent", { count: trial }) }] : []),
            { id: "finish", label: t("choose.finish"), primary: true },
          ]);
        }
      } else if (stage === "done") {
        if (!(await say(t("done.bye"), token))) return;
        script.setMood("happy");
        await script.sleep(3200);
        if (script.alive(token)) update({ stage: "done", minimized: true });
      }
    })();
  // Re-run when the stage changes, or the link step's page does.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage, stage === "link" ? pathname : null]);

  // making → ready / failed
  useEffect(() => {
    if (stage !== "making" || !task) return;
    if (task.status === "completed") {
      trackEvent("preview_ready", { found: previewOf(task)?.found ?? 0 });
      setStage(previewOf(task) && !paid ? "ready" : "choose");
    } else if (task.status === "error" || task.status === "cancelled") {
      const token = begin();
      void (async () => {
        script.setMood("sad");
        await say(t("making.failed"), token, { think: false });
        setChips([{ id: "again", label: t("making.again"), primary: true }]);
      })();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage, task?.status]);

  // paid → choose, once the rest is made
  useEffect(() => {
    if (stage !== "paid" || !task) return;
    if (task.status === "completed" && preview?.unlocked_at) setStage("choose");
  }, [preview?.unlocked_at, setStage, stage, task]);

  // ready: the best clip, then the plans
  const readyPlayed = useRef(false);
  useEffect(() => {
    if (stage !== "ready") { readyPlayed.current = false; return; }
    if (!task || readyPlayed.current) return;
    readyPlayed.current = true;
    const token = begin();
    void (async () => {
      const hook = bestClip?.hook_title?.trim();
      if (!(await say(hook ? t("ready.best", { hook, score: bestClip?.virality_score ?? 0 }) : t("ready.bestNoHook"), token))) return;
      if (onTaskPage) setTarget("preview-clip");
      else setChips([{ id: "show", label: t("ready.show"), primary: true }]);
      if (!more) return;
      if (!(await say(t("ready.more", { count: more }), token))) return;
      trackEvent("onboarding_plans_shown", { suggested, where: "ready" });
      push({ kind: "node", render: () => <PlanCard compact suggested={suggested} more={more} previewTaskId={flow.taskId} returnTo={`/tasks/${flow.taskId}`} /> });
      setChips((current) => [...current.filter((chip) => chip.id !== "later"), { id: "later", label: t("ready.later") }]);
    })();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage, task, bestClip]);

  // Point at the preview clip once we arrive on its page.
  useEffect(() => {
    if (stage === "ready" && onTaskPage) {
      setTarget("preview-clip");
      setChips((current) => current.filter((chip) => chip.id !== "show"));
    }
  }, [onTaskPage, stage]);

  const pickHome = async (mode: HomeMode) => {
    push({ kind: "user", text: t(`choose.${mode}.title`) });
    try {
      await saveHomeMode(mode);
      queryClient.setQueryData<AgentStatus | null>(agentKeys.status, (current) => (current ? { ...current, home_mode: mode } : current));
    } catch {
      toast.error(t("choose.saveFailed"));
    }
    finish(false);
  };

  const onChip = (id: string) => {
    setChips([]);
    if (id === "home") navigate("/");
    else if (id === "show" && taskPath) navigate(taskPath);
    else if (id === "later") update({ minimized: true });
    else if (id === "again") { setLook(null); setStage("link", { taskId: undefined, link: null }); navigate("/"); }
    else if (id === "trial") { finish(false); navigate("/?mode=agent"); }
    else if (id === "finish") finish(false);
  };

  // The spotlight only while the dock is open and the page has the target.
  const spot = useTarget(flow.minimized ? null : target);
  const spotOn = Boolean(spot) && !(stage === "link" && pathname !== "/");

  if (!ACTIVE.includes(stage)) return null;
  if (stage === "done" && flow.minimized) return null;

  return (
    <>
      {spotOn && spot && <Spotlight target={spot} />}
      <AnimatePresence initial={false} mode="wait">
        {flow.minimized ? (
          <motion.button
            key="bubble"
            data-pebble-dock
            type="button"
            layoutId="pebble-guide"
            onClick={() => update({ minimized: false })}
            initial={{ opacity: 0, scale: 0.6 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.6 }}
            aria-label={t("open")}
            className="fixed bottom-[calc(5rem+env(safe-area-inset-bottom))] right-4 z-50 flex size-14 items-center justify-center rounded-full border bg-background shadow-[0_18px_40px_-16px_rgb(28_25_23/0.55)] md:bottom-6 md:right-6"
          >
            <AgentMark size="sm" mood={stage === "making" || stage === "paid" ? "thinking" : "idle"} />
            {(stage === "ready" || stage === "making") && <span className="absolute right-1 top-1 size-2.5 rounded-full bg-brand ring-2 ring-background" />}
          </motion.button>
        ) : (
          <motion.section
            key="dock"
            data-pebble-dock
            aria-label={t("label")}
            initial={{ opacity: 0, y: 24, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 24, scale: 0.97 }}
            transition={{ type: "spring", stiffness: 300, damping: 30 }}
            className="fixed inset-x-3 bottom-[calc(4.75rem+env(safe-area-inset-bottom))] z-50 flex max-h-[min(34rem,62dvh)] flex-col overflow-hidden rounded-3xl border bg-background/95 shadow-[0_30px_80px_-30px_rgb(28_25_23/0.6)] backdrop-blur-xl md:inset-x-auto md:bottom-6 md:right-6 md:w-[24rem] md:max-h-[min(40rem,78dvh)]"
          >
            <header className="flex items-center gap-2.5 border-b px-3.5 py-2.5">
              <motion.span layoutId="pebble-guide"><AgentMark size="sm" mood={script.mood} /></motion.span>
              <span className="text-sm font-semibold">Agent</span>
              <span className="text-xs text-muted-foreground">{t(`steps.${stage}`)}</span>
              <span className="ml-auto flex items-center gap-0.5">
                <button type="button" onClick={() => update({ minimized: true })} aria-label={t("minimize")} className="rounded-full p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground">
                  <ChevronDown className="size-4" />
                </button>
                {stage !== "done" && (
                  <button type="button" onClick={() => finish(true)} aria-label={t("skip")} title={t("skip")} className="rounded-full p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground">
                    <X className="size-4" />
                  </button>
                )}
              </span>
            </header>
            <div ref={scroller} className="flex-1 space-y-3 overflow-y-auto overscroll-contain px-3.5 py-3.5" aria-live="polite">
              <PebbleMessages compact items={script.items} mood={script.mood} thinking={script.mood === "thinking"} />
              {chips.length > 0 && <PebbleChoices compact options={chips} onPick={onChip} />}
            </div>
            {stage === "ready" && taskPath && !onTaskPage && (
              <button type="button" onClick={() => navigate(taskPath)} className="flex items-center justify-center gap-1.5 border-t py-2 text-xs font-medium text-muted-foreground hover:text-foreground">
                <Clapperboard className="size-3.5" />{t("ready.open")}<ArrowRight className="size-3" />
              </button>
            )}
          </motion.section>
        )}
      </AnimatePresence>
    </>
  );
}

/** The progress card, reading the shared task query so it moves while the dock talks. */
function MakingCardLive({ taskId }: { taskId?: string }) {
  const { data } = useQuery({ queryKey: ["pebble-task", taskId], queryFn: () => fetchTask(taskId!), enabled: Boolean(taskId), refetchInterval: 4000 });
  return <MakingCard task={data?.task} />;
}

export function pebbleDockActive(stage: PebbleStage) {
  return ACTIVE.includes(stage) && stage !== "intro";
}

