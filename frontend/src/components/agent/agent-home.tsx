"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { ArrowRight, BarChart3, CalendarDays, Flame, ListOrdered, Loader2, MessageSquareText, PenLine, Sparkles } from "lucide-react";
import { motion } from "motion/react";
import { toast } from "sonner";

import { AgentComposer } from "@/components/agent/agent-composer";
import { AgentMark } from "@/components/agent/agent-mark";
import { PostRow } from "@/components/agent/agent-cards";
import { AppShell } from "@/components/app/app-shell";
import { ClipCover } from "@/components/app/clip-cover";
import { Skeleton } from "@/components/ui/skeleton";
import { useSocialAccess } from "@/hooks/use-social-access";
import { useSession } from "@/lib/auth-client";
import { TrialNote, useAgentGate } from "@/components/agent/agent-upgrade";
import { agentKeys, fetchConversations, startConversation, type Attachment, type CardPost } from "@/lib/agent";
import { coverClipUrl, timeAgo } from "@/lib/generations";
import { useGenerations } from "@/lib/queries/generations";
import { fetchScheduledPosts, socialPostKeys } from "@/lib/queries/social";
import { cn } from "@/lib/utils";

function greetingKey() {
  const hour = new Date().getHours();
  return hour < 5 ? "upLate" : hour < 12 ? "morning" : hour < 18 ? "afternoon" : "evening";
}

type Suggestion = {
  id: string;
  title: string;
  hint: string;
  prompt: string;
  /** Put in the composer to finish (a link to paste) instead of sending at once. */
  seed?: boolean;
  icon?: typeof Sparkles;
  cover?: string | null;
};

const MAX_TITLE = 34;

function shorten(value: string) {
  return value.length > MAX_TITLE ? `${value.slice(0, MAX_TITLE - 1).trimEnd()}…` : value;
}

/**
 * Three things worth doing next, from the person's own account: their latest project, what's
 * scheduled, how posts are doing. New accounts get starters instead.
 */
function useSuggestions(socialAccess: boolean | null, scheduledCount: number | null): Suggestion[] {
  const t = useTranslations("agent.suggest");
  const locale = useLocale();
  const { data: session } = useSession();
  const { data: generations } = useGenerations(session?.user?.id);
  return useMemo(() => {
    const list: Suggestion[] = [];
    const latest = generations?.find((g) => g.status === "completed" && g.clips_count > 0);
    const processing = generations?.find((g) => g.status === "processing" || g.status === "queued");
    if (processing) {
      const title = shorten(processing.source_title);
      list.push({ id: "processing", title: t("processing.title"), hint: title, prompt: t("processing.prompt", { title: processing.source_title }), cover: coverClipUrl(processing), icon: Loader2 });
    }
    if (latest && socialAccess) {
      list.push({
        id: "post", title: t("post.title"), hint: t("post.hint", { count: latest.clips_count, ago: timeAgo(latest.created_at, locale) }),
        prompt: t("post.prompt", { title: latest.source_title }), cover: coverClipUrl(latest),
      });
    }
    if (latest) {
      list.push({ id: "series", title: t("series.title"), hint: t("series.hint"), prompt: t("series.prompt", { title: latest.source_title }), icon: ListOrdered });
    }
    if (socialAccess && scheduledCount) {
      list.push({ id: "week", title: t("week.title"), hint: t("week.hint", { count: scheduledCount }), prompt: t("week.prompt"), icon: CalendarDays });
    } else if (socialAccess && latest) {
      list.push({ id: "stats", title: t("stats.title"), hint: t("stats.hint"), prompt: t("stats.prompt"), icon: BarChart3 });
    }
    list.push({ id: "clip", title: t("clip.title"), hint: t("clip.hint"), prompt: t("clip.prompt"), seed: true, icon: Sparkles });
    if (!latest) {
      list.push({ id: "viral", title: t("viral.title"), hint: t("viral.hint"), prompt: t("viral.prompt"), icon: Flame });
      list.push({ id: "rhythm", title: t("rhythm.title"), hint: t("rhythm.hint"), prompt: t("rhythm.prompt"), icon: CalendarDays });
    }
    return list.slice(0, 3);
  }, [generations, socialAccess, scheduledCount, t, locale]);
}

function SuggestionCard({ suggestion, onPick, disabled }: { suggestion: Suggestion; onPick: () => void; disabled: boolean }) {
  const Icon = suggestion.icon ?? Sparkles;
  return (
    <button
      type="button"
      onClick={onPick}
      disabled={disabled}
      className="group flex min-w-[15rem] snap-start items-center gap-3 rounded-2xl border bg-background/70 p-3 text-left backdrop-blur transition-all hover:-translate-y-0.5 hover:border-foreground/20 hover:bg-background hover:shadow-sm disabled:opacity-50 sm:min-w-0"
    >
      {suggestion.cover ? (
        <ClipCover src={suggestion.cover} className="h-11 w-8 shrink-0 rounded-lg" />
      ) : (
        <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-muted text-foreground/80 transition-colors group-hover:bg-foreground group-hover:text-background">
          <Icon className={cn("size-4", suggestion.id === "processing" && "animate-spin")} />
        </span>
      )}
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[13.5px] font-medium">{suggestion.title}</span>
        <span className="block truncate text-xs text-muted-foreground">{suggestion.hint}</span>
      </span>
    </button>
  );
}

/** The home screen for people who chose the agent: say what you want, it does the rest. */
export function AgentHome({ manualHref = "/?mode=manual" }: { manualHref?: string }) {
  const t = useTranslations("agent");
  const locale = useLocale();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { data: session } = useSession();
  const socialAccess = useSocialAccess(Boolean(session?.user));
  const [seed, setSeed] = useState<{ text: string; n: number } | null>(null);
  const [starting, setStarting] = useState(false);
  const [listening, setListening] = useState(false);
  const firstName = session?.user?.name?.trim().split(/\s+/)[0];
  const greeting = useMemo(() => t(`greeting.${greetingKey()}`, { name: firstName ?? "" }), [t, firstName]);

  const { data: conversations } = useQuery({ queryKey: agentKeys.conversations, queryFn: fetchConversations });
  const { data: scheduled } = useQuery({
    queryKey: socialPostKeys.scheduled(),
    queryFn: fetchScheduledPosts,
    enabled: socialAccess === true,
  });
  const upcoming = useMemo(
    () => [...(scheduled ?? [])].filter((post) => post.scheduled_for).sort((a, b) => a.scheduled_for!.localeCompare(b.scheduled_for!)).slice(0, 4),
    [scheduled],
  );

  const gate = useAgentGate("agent_home");

  const start = async (message: string, attachments: Attachment[]) => {
    setStarting(true);
    try {
      const { conversation } = await startConversation(message, attachments, locale);
      void queryClient.invalidateQueries({ queryKey: agentKeys.conversations });
      if (gate.status?.access === "trial") void gate.refresh();
      router.push(`/agent/${conversation.id}?new=1`);
      return true;
    } catch (error) {
      if (!gate.handleError(error)) toast.error(error instanceof Error ? error.message : t("errors.send"));
      setStarting(false);
      return false;
    }
  };

  const suggestions = useSuggestions(socialAccess, scheduled ? scheduled.filter((post) => post.scheduled_for).length : null);
  const pick = (suggestion: Suggestion) => {
    if (suggestion.seed) setSeed({ text: suggestion.prompt, n: Date.now() });
    else void start(suggestion.prompt, []);
  };

  return (
    <AppShell tour={{ busy: starting }}>
      <main className="relative mx-auto w-full max-w-5xl px-4 pb-24 pt-10 sm:px-8 md:pt-[13vh]">
        {/* A faint warm light behind the composer; the accent stays a whisper. */}
        <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 -z-0 mx-auto h-[28rem] max-w-3xl bg-[radial-gradient(60%_55%_at_50%_35%,color-mix(in_oklch,var(--brand)_9%,transparent),transparent_70%)]" />
        <div className="relative mx-auto max-w-2xl">
          <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }} className="text-center">
            <AgentMark size="lg" follow mood={starting ? "thinking" : listening ? "listening" : "idle"} className="mx-auto" />
            <h1 className="mt-5 font-display text-3xl font-bold tracking-tight sm:text-[2.5rem] sm:leading-tight">{greeting}</h1>
            <p className="mt-2 text-muted-foreground">{t("subtitle")}</p>
          </motion.div>

          <motion.div data-tour="agent-composer" onFocusCapture={() => setListening(true)} onBlurCapture={() => setListening(false)} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.45, delay: 0.05, ease: [0.16, 1, 0.3, 1] }} className="mt-8">
            <AgentComposer onSend={start} disabled={starting} autoFocus size="large" seed={seed} placeholder={t("composer.placeholderHome")} />
            <TrialNote status={gate.status} onUpgrade={gate.show} className="mt-3" />
          </motion.div>

          <motion.div
            data-tour="agent-suggestions"
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.45, delay: 0.12, ease: [0.16, 1, 0.3, 1] }}
            className="-mx-4 mt-4 flex snap-x gap-2.5 overflow-x-auto px-4 pb-1 sm:mx-0 sm:grid sm:grid-cols-3 sm:overflow-visible sm:px-0 sm:pb-0"
          >
            {suggestions.map((suggestion) => (
              <SuggestionCard key={suggestion.id} suggestion={suggestion} disabled={starting} onPick={() => pick(suggestion)} />
            ))}
          </motion.div>

          <p className="mt-6 text-center text-xs text-muted-foreground">
            <Link href={manualHref} data-tour="agent-manual-link" className="underline-offset-2 hover:text-foreground hover:underline">
              {t("manualLink")}
            </Link>
          </p>
          {gate.dialog}
        </div>

        <div className={cn("relative mx-auto mt-16 grid max-w-4xl grid-cols-1 gap-8 md:mt-24", socialAccess === true && "md:grid-cols-2")}>
          <section aria-labelledby="agent-chats">
            <h2 id="agent-chats" className="mb-3 font-display text-base font-bold tracking-tight">{t("home.recentChats")}</h2>
            {conversations === undefined ? (
              <div className="space-y-2">{[0, 1, 2].map((key) => <Skeleton key={key} className="h-11 rounded-xl" />)}</div>
            ) : conversations.length === 0 ? (
              <p className="rounded-2xl border border-dashed px-4 py-6 text-center text-sm text-muted-foreground">{t("home.noChats")}</p>
            ) : (
              <ul className="overflow-hidden rounded-2xl border bg-background">
                {conversations.slice(0, 6).map((conversation) => (
                  <li key={conversation.id} className="border-b last:border-b-0">
                    <Link href={`/agent/${conversation.id}`} className="group flex items-center gap-3 px-3.5 py-2.5 transition-colors hover:bg-accent">
                      <MessageSquareText className="size-4 shrink-0 text-muted-foreground" />
                      <span className="min-w-0 flex-1 truncate text-sm">{conversation.title || t("home.untitled")}</span>
                      {conversation.status === "awaiting_approval" && <span className="size-1.5 rounded-full bg-brand" aria-label={t("home.needsYou")} />}
                      <span className="shrink-0 text-xs text-muted-foreground">{conversation.updated_at ? timeAgo(conversation.updated_at, locale) : ""}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {socialAccess === true && (
            <section aria-labelledby="agent-upcoming">
              <div className="mb-3 flex items-end justify-between">
                <h2 id="agent-upcoming" className="font-display text-base font-bold tracking-tight">{t("home.comingUp")}</h2>
                <Link href="/calendar" data-tour="agent-calendar-link" className="group flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
                  {t("home.calendar")}<ArrowRight className="size-3 transition-transform group-hover:translate-x-0.5" />
                </Link>
              </div>
              {scheduled === undefined ? (
                <div className="space-y-2">{[0, 1].map((key) => <Skeleton key={key} className="h-14 rounded-xl" />)}</div>
              ) : upcoming.length === 0 ? (
                <button
                  type="button"
                  onClick={() => void start(t("suggest.fill.prompt"), [])}
                  className="flex w-full items-center gap-3 rounded-2xl border border-dashed px-4 py-5 text-left text-sm text-muted-foreground transition-colors hover:border-foreground/25 hover:text-foreground"
                >
                  <PenLine className="size-4 shrink-0" />{t("home.nothingScheduled")}
                </button>
              ) : (
                <div className="divide-y overflow-hidden rounded-2xl border bg-background">
                  {upcoming.map((post) => <PostRow key={post.id} post={post as unknown as CardPost} />)}
                </div>
              )}
            </section>
          )}
        </div>
      </main>
    </AppShell>
  );
}
