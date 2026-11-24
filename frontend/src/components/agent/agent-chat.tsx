"use client";

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { AnimatePresence, motion } from "motion/react";
import {
  AlertCircle, BarChart3, CalendarDays, Captions, Check, ChevronDown, Clock, Eye, Film, FileText, Lightbulb, Loader2,
  Pencil, PenLine, Search, Send, Scissors, Settings2, Sparkles, Trash2, Undo2, Wand2, X, Brain,
  ListVideo, CircleSlash, type LucideIcon,
} from "lucide-react";
import { toast } from "sonner";

import { AgentComposer } from "@/components/agent/agent-composer";
import { AgentMark, PebbleIcon, type AgentMood } from "@/components/agent/agent-mark";
import { ApprovalCard, OptionsCard, ToolCard } from "@/components/agent/agent-cards";
import { AgentMarkdown } from "@/components/agent/markdown";
import { formatSize } from "@/lib/agent-attachments";
import { Skeleton } from "@/components/ui/skeleton";
import { TrialNote, useAgentGate } from "@/components/agent/agent-upgrade";
import {
  agentKeys, answerApproval, answeredReply, fetchConversation, sendMessage, stopConversation,
  type AgentCard, type AgentConversation, type AgentItem, type AgentJobCard, type ApprovalItem, type Attachment, type ToolStatus,
} from "@/lib/agent";
import { cn } from "@/lib/utils";

const STEP_ICONS: Record<string, LucideIcon> = {
  get_overview: Eye, list_projects: ListVideo, get_project: Film, find_clips: Search, get_clip_transcript: FileText,
  create_project: Sparkles, when_project_finishes: Clock, project_action: CircleSlash, delete_project: Trash2,
  list_caption_styles: Captions, edit_clip: Wand2, check_background_work: Loader2, trim_clip: Scissors,
  restyle_project: Captions, get_calendar: CalendarDays, suggest_posting_times: Clock, draft_caption: PenLine,
  schedule_post: CalendarDays, publish_now: Send, update_post: Pencil, post_action: Undo2, get_performance: BarChart3,
  update_create_defaults: Settings2, remember: Brain, forget: Brain, show_options: Lightbulb,
};

// ----------------------------------------------------------------------
// Live state: what arrives over SSE after the transcript was loaded
// ----------------------------------------------------------------------
type LiveSegment =
  | { type: "text"; text: string }
  | { type: "tool"; id: string; name: string; input: Record<string, string | number | boolean>; status: ToolStatus; card?: AgentCard | null; error?: string | null };

type Live = {
  status: "idle" | "running" | "awaiting_approval";
  segments: LiveSegment[];
  approval: ApprovalItem[] | null;
  error: string | null;
  jobs: Map<string, AgentJobCard>;
};

type LiveAction =
  | { type: "reset"; status: Live["status"]; approval: ApprovalItem[] | null; jobs: AgentJobCard[]; partial?: string }
  | { type: "event"; event: string; data: Record<string, unknown> }
  | { type: "running" };

function liveReducer(state: Live, action: LiveAction): Live {
  if (action.type === "reset") {
    const jobs = new Map(state.jobs);
    for (const job of action.jobs) jobs.set(job.id, job);
    return { status: action.status, segments: action.partial ? [{ type: "text", text: action.partial }] : [], approval: action.approval, error: null, jobs };
  }
  if (action.type === "running") return { ...state, status: "running", error: null, approval: null };
  const { event, data } = action;
  switch (event) {
    case "run":
      return { ...state, status: "running", error: null };
    case "text": {
      const text = String(data.text ?? "");
      const segments = [...state.segments];
      const last = segments[segments.length - 1];
      if (last?.type === "text") segments[segments.length - 1] = { ...last, text: last.text + text };
      else segments.push({ type: "text", text });
      return { ...state, status: "running", segments };
    }
    case "tool_start":
      return {
        ...state,
        status: "running",
        segments: [...state.segments, { type: "tool", id: String(data.id), name: String(data.name), input: (data.input ?? {}) as Record<string, string>, status: "running" }],
      };
    case "tool_end":
      return {
        ...state,
        segments: state.segments.map((segment) =>
          segment.type === "tool" && segment.id === data.id
            ? { ...segment, status: data.declined ? "declined" : data.ok ? "done" : "error", card: (data.card ?? null) as AgentCard | null, error: (data.error ?? null) as string | null }
            : segment),
      };
    case "approval":
      return { ...state, status: "awaiting_approval", approval: (data.items ?? []) as ApprovalItem[] };
    case "error":
      return { ...state, status: "idle", error: String(data.message ?? "") };
    case "job": {
      const job = data.job as AgentJobCard;
      const jobs = new Map(state.jobs);
      jobs.set(job.id, { ...(jobs.get(job.id) ?? {}), ...job });
      return { ...state, jobs };
    }
    default:
      return state;
  }
}

/** Steps a person cares to see; lookups that only feed the agent fold into "Looked around". */
const QUIET_TOOLS = new Set(["get_overview", "list_caption_styles", "check_background_work", "get_clip_transcript"]);

function useStepLabel() {
  const t = useTranslations("agent");
  return (name: string, status: ToolStatus, input: Record<string, string | number | boolean>) => {
    if (status === "awaiting_approval") return t("steps.waiting");
    const key = `steps.${name}.${status === "running" ? "running" : "done"}`;
    const values = { platform: String(input.platform ?? ""), when: String(input.when ?? "").replace("T", " "), query: String(input.query ?? ""), action: String(input.action ?? "") };
    return t.has(key) ? t(key, values) : t("steps.generic");
  };
}

// ----------------------------------------------------------------------
// Rows
// ----------------------------------------------------------------------
function StepRow({ name, status, input, error }: { name: string; status: ToolStatus; input: Record<string, string | number | boolean>; error?: string | null }) {
  const t = useTranslations("agent");
  const label = useStepLabel();
  const Icon = STEP_ICONS[name] ?? Sparkles;
  return (
    <div className="flex items-center gap-2.5 py-1 text-sm">
      <span className={cn(
        "flex size-6 shrink-0 items-center justify-center rounded-full border bg-background",
        status === "running" && "border-foreground/20",
        status === "error" && "border-red-500/30 text-red-600",
      )}>
        {status === "running" ? <Loader2 className="size-3 animate-spin" /> : status === "error" ? <X className="size-3" /> : status === "declined" || status === "interrupted" ? <CircleSlash className="size-3 text-muted-foreground" /> : status === "awaiting_approval" ? <Clock className="size-3" /> : <Icon className="size-3 text-muted-foreground" />}
      </span>
      <span className={cn("min-w-0 truncate", status === "running" ? "text-foreground shimmer-text" : "text-muted-foreground")}>
        {label(name, status, input)}
        {status === "declined" && <span className="ml-1.5 text-xs">· {t("steps.declined")}</span>}
        {status === "error" && error && <span className="ml-1.5 text-xs" title={error}>· {t("steps.failed")}</span>}
      </span>
    </div>
  );
}

type Block =
  | { kind: "user"; item: Extract<AgentItem, { type: "user" }> }
  | { kind: "notice"; item: Extract<AgentItem, { type: "notice" }> }
  | { kind: "agent"; parts: AgentPart[]; live: boolean };

function blocks(items: AgentItem[], live: LiveSegment[]): Block[] {
  const out: Block[] = [];
  for (const item of items) {
    if (item.type === "user") out.push({ kind: "user", item });
    else if (item.type === "notice") out.push({ kind: "notice", item });
    else {
      const last = out[out.length - 1];
      if (last?.kind === "agent") last.parts.push(item);
      else out.push({ kind: "agent", parts: [item], live: false });
    }
  }
  if (live.length) {
    const last = out[out.length - 1];
    if (last?.kind === "agent") { last.parts.push(...live); last.live = true; }
    else out.push({ kind: "agent", parts: [...live], live: true });
  }
  return out;
}

type AgentPart = Extract<AgentItem, { type: "text" | "tool" }> | LiveSegment;

function AgentBlock({ parts, jobs, mood, items, onReply, replyDisabled }: {
  parts: AgentPart[];
  jobs: Map<string, AgentJobCard>;
  mood: AgentMood;
  items: AgentItem[];
  onReply: (reply: string, label: string) => void;
  replyDisabled: boolean;
}) {
  const t = useTranslations("agent");
  const [showQuiet, setShowQuiet] = useState(false);
  // Group consecutive tool steps; options cards render on their own, after their text.
  const groups: ({ type: "text"; text: string; key: string } | { type: "steps"; tools: Extract<LiveSegment, { type: "tool" }>[]; key: string } | { type: "options"; card: Extract<AgentCard, { type: "options" }>; key: string; itemId: string })[] = [];
  parts.forEach((part, index) => {
    if (part.type === "text") {
      groups.push({ type: "text", text: part.text, key: `t${index}` });
      return;
    }
    const tool = part as Extract<LiveSegment, { type: "tool" }>;
    if (tool.name === "show_options" && tool.card?.type === "options") {
      groups.push({ type: "options", card: tool.card, key: tool.id, itemId: tool.id });
      return;
    }
    const last = groups[groups.length - 1];
    if (last?.type === "steps") last.tools.push(tool);
    else groups.push({ type: "steps", tools: [tool], key: tool.id });
  });

  // Options are the agent's question: they sit under its reply, whatever order it wrote them in.
  const ordered = [...groups.filter((group) => group.type !== "options"), ...groups.filter((group) => group.type === "options")];

  return (
    <div className="flex gap-3">
      <AgentMark className="mt-0.5" mood={mood} />
      <div className="min-w-0 flex-1 space-y-3">
        {ordered.map((group) => {
          if (group.type === "text") return <AgentMarkdown key={group.key} text={group.text} />;
          if (group.type === "options") {
            const index = items.findIndex((item) => item.type === "tool" && item.id === group.itemId);
            return (
              <OptionsCard
                key={group.key}
                card={group.card}
                answered={index >= 0 ? answeredReply(items, index) : null}
                disabled={replyDisabled}
                onReply={onReply}
              />
            );
          }
          const quiet = group.tools.filter((tool) => QUIET_TOOLS.has(tool.name) && tool.status === "done" && !tool.card);
          const shown = group.tools.filter((tool) => !quiet.includes(tool));
          return (
            <div key={group.key} className="space-y-2">
              <div className="rounded-xl">
                {quiet.length > 0 && (
                  <button type="button" onClick={() => setShowQuiet((open) => !open)} className="flex items-center gap-2.5 py-1 text-sm text-muted-foreground hover:text-foreground">
                    <span className="flex size-6 items-center justify-center rounded-full border bg-background"><Check className="size-3" /></span>
                    {t("steps.lookedAround", { count: quiet.length })}
                    <ChevronDown className={cn("size-3.5 transition-transform", showQuiet && "rotate-180")} />
                  </button>
                )}
                {showQuiet && quiet.map((tool) => <StepRow key={tool.id} name={tool.name} status={tool.status} input={tool.input} />)}
                {shown.map((tool) => (
                  <div key={tool.id}>
                    <StepRow name={tool.name} status={tool.status} input={tool.input} error={tool.error} />
                    {tool.card && (
                      <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }} className="mb-2 mt-1.5">
                        <ToolCard card={tool.card} jobs={jobs} />
                      </motion.div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function NoticeRow({ item }: { item: Extract<AgentItem, { type: "notice" }> }) {
  const t = useTranslations("agent");
  const { notice } = item;
  const text = notice.kind === "project_finished"
    ? notice.status === "completed" ? t("notice.projectDone", { title: notice.title ?? "" }) : t("notice.projectEnded", { title: notice.title ?? "" })
    : notice.status === "completed" ? t("notice.editDone", { title: notice.title ?? "" }) : t("notice.editEnded", { title: notice.title ?? "" });
  return (
    <div className="flex items-center gap-3 text-xs text-muted-foreground">
      <span className="h-px flex-1 bg-border" />
      <span className="flex items-center gap-1.5"><PebbleIcon className="size-3.5" />{text}</span>
      <span className="h-px flex-1 bg-border" />
    </div>
  );
}

function UserBubble({ item }: { item: Extract<AgentItem, { type: "user" }> }) {
  const t = useTranslations("agent");
  const images = item.attachments.filter((a) => a.kind === "image" && a.preview);
  const files = item.attachments.filter((a) => !(a.kind === "image" && a.preview));
  return (
    <div className="flex justify-end">
      <div className="flex max-w-[85%] flex-col items-end gap-1.5">
        {images.length > 0 && (
          <div className={cn("grid justify-end gap-1.5", images.length === 1 ? "grid-cols-1" : images.length === 2 ? "grid-cols-2" : "grid-cols-3")}>
            {images.map((image, index) => (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                key={index}
                src={image.preview ?? undefined}
                alt={image.name ?? ""}
                className={cn("rounded-2xl border object-cover", images.length === 1 ? "max-h-64 w-auto max-w-full" : "size-28")}
              />
            ))}
          </div>
        )}
        {files.length > 0 && (
          <div className="flex flex-wrap justify-end gap-1.5">
            {files.map((file, index) => (
              <span key={index} className="inline-flex h-10 max-w-60 items-center gap-2 rounded-xl border bg-background pl-1.5 pr-3 text-xs">
                <span className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-muted">
                  {file.kind === "video" ? <Film className="size-3.5" /> : <FileText className="size-3.5" />}
                </span>
                <span className="min-w-0">
                  <span className="block truncate font-medium">{file.name}</span>
                  <span className="block text-[10px] uppercase tracking-wide text-muted-foreground">
                    {t(`composer.kinds.${file.kind}`)}{file.size ? ` · ${formatSize(file.size)}` : ""}
                  </span>
                </span>
              </span>
            ))}
          </div>
        )}
        {item.text && (
          <div className="whitespace-pre-wrap break-words rounded-[1.25rem] rounded-br-md bg-muted px-4 py-2.5 text-[0.9375rem] leading-relaxed">{item.text}</div>
        )}
      </div>
    </div>
  );
}

/** "Thinking", with the face only when the agent hasn't started its reply (otherwise its face is already working). */
function Thinking({ withFace }: { withFace: boolean }) {
  const t = useTranslations("agent");
  return (
    <div className={cn("flex items-center gap-3", withFace ? "pt-4" : "-mt-4 pl-11")}>
      {withFace && <AgentMark busy />}
      <span className="shimmer-text text-sm text-muted-foreground">{t("thinking")}</span>
    </div>
  );
}

// ----------------------------------------------------------------------
// The conversation
// ----------------------------------------------------------------------
export function AgentChat({ conversationId, freshlyStarted }: { conversationId: string; freshlyStarted?: boolean }) {
  const t = useTranslations("agent");
  const locale = useLocale();
  const queryClient = useQueryClient();
  const { data, isError, refetch } = useQuery({
    queryKey: agentKeys.conversation(conversationId),
    queryFn: () => fetchConversation(conversationId),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
  const [live, dispatch] = useReducer(liveReducer, { status: freshlyStarted ? "running" : "idle", segments: [], approval: null, error: null, jobs: new Map() });
  const [answering, setAnswering] = useState(false);
  // A short smile when a turn finishes.
  const [celebrating, setCelebrating] = useState(false);
  const cursor = useRef<string | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const scrollerPinned = useRef(true);

  const reload = useCallback(async () => {
    const fresh = await fetchConversation(conversationId);
    queryClient.setQueryData(agentKeys.conversation(conversationId), fresh);
    dispatch({
      type: "reset",
      status: fresh.run?.status === "awaiting_approval" ? "awaiting_approval" : fresh.run ? "running" : "idle",
      approval: fresh.run?.status === "awaiting_approval" ? fresh.run.pending?.items ?? null : null,
      jobs: fresh.jobs,
    });
    void queryClient.invalidateQueries({ queryKey: agentKeys.conversations });
    return fresh;
  }, [conversationId, queryClient]);

  // First load: the live state follows the run the transcript says is open.
  useEffect(() => {
    if (!data || cursor.current !== null) return;
    // Opened mid-reply (the tab was closed while Agent worked): show what it has written so far
    // and follow on from exactly there.
    const partial = !freshlyStarted && data.partial?.event_id ? data.partial : null;
    cursor.current = freshlyStarted ? "0-0" : partial ? partial.event_id : data.last_event_id;
    dispatch({
      type: "reset",
      status: data.run?.status === "awaiting_approval" ? "awaiting_approval" : data.run || freshlyStarted ? "running" : "idle",
      approval: data.run?.status === "awaiting_approval" ? data.run.pending?.items ?? null : null,
      jobs: data.jobs,
      ...(partial ? { partial: partial.text } : {}),
    });
  }, [data, freshlyStarted]);

  // One event stream per open conversation; the browser resumes it with Last-Event-ID.
  const streamReady = data !== undefined;
  useEffect(() => {
    if (!streamReady) return;
    const after = cursor.current ?? "$";
    const source = new EventSource(`/api/agent/conversations/${conversationId}/events?after=${encodeURIComponent(after)}`);
    const handle = (event: MessageEvent) => {
      let payload: Record<string, unknown> = {};
      try { payload = JSON.parse(event.data); } catch { /* ignore */ }
      if (event.lastEventId) cursor.current = event.lastEventId;
      dispatch({ type: "event", event: event.type, data: payload });
      if (event.type === "done" && payload.status !== "cancelled") {
        setCelebrating(true);
        window.setTimeout(() => setCelebrating(false), 1800);
      }
      if (event.type === "done" || event.type === "error" || event.type === "approval" || event.type === "notice") {
        void reload().catch(() => {});
      }
    };
    for (const name of ["run", "text", "tool_start", "tool_end", "approval", "done", "error", "job", "notice"]) {
      source.addEventListener(name, handle as EventListener);
    }
    return () => source.close();
  }, [conversationId, streamReady, reload]);

  const items = useMemo(() => data?.items ?? [], [data]);
  const shown = useMemo(() => blocks(items, live.segments), [items, live.segments]);
  const running = live.status === "running";
  const pendingApproval = live.approval?.length ? live.approval : null;
  const lastText = live.segments[live.segments.length - 1];
  const lastMood: AgentMood = running ? "thinking" : live.error ? "sad" : celebrating ? "happy" : "idle";
  const showThinking = running && (!lastText || lastText.type !== "text" || !lastText.text) && !(lastText?.type === "tool" && lastText.status === "running");

  // Follow the conversation as it grows, unless the person scrolled up to read.
  useEffect(() => {
    const onScroll = () => {
      scrollerPinned.current = window.innerHeight + window.scrollY >= document.body.scrollHeight - 160;
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  useEffect(() => {
    if (scrollerPinned.current) bottomRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [shown.length, live.segments, pendingApproval, showThinking]);

  const gate = useAgentGate("agent_chat");
  const { handleError: handleGateError, refresh: refreshGate } = gate;
  const onTrial = gate.status?.access === "trial";

  const send = useCallback(async (message: string, attachments: Attachment[]) => {
    try {
      // Shown at once; the transcript replaces it when the turn is saved.
      queryClient.setQueryData<AgentConversation>(agentKeys.conversation(conversationId), (current) => current && ({
        ...current,
        items: [...current.items, { type: "user", id: `pending-${Date.now()}`, text: message, attachments: attachments.map((a) => ({ name: a.name, kind: a.kind, preview: a.kind === "image" ? a.preview : null, size: a.kind === "video" ? null : a.size })), created_at: new Date().toISOString() }],
      }));
      dispatch({ type: "running" });
      scrollerPinned.current = true;
      await sendMessage(conversationId, message, attachments, locale);
      if (onTrial) void refreshGate();
      return true;
    } catch (error) {
      void reload().catch(() => {});
      if (!handleGateError(error)) toast.error(error instanceof Error ? error.message : t("errors.send"));
      return false;
    }
  }, [conversationId, handleGateError, locale, onTrial, queryClient, refreshGate, reload, t]);

  const answer = async (approved: boolean) => {
    setAnswering(true);
    try {
      await answerApproval(conversationId, approved, data?.run?.id);
      dispatch({ type: "running" });
    } catch (error) {
      if (!gate.handleError(error)) toast.error(error instanceof Error ? error.message : t("errors.send"));
      void reload();
    } finally {
      setAnswering(false);
    }
  };

  if (isError) {
    return (
      <div className="mx-auto max-w-md py-24 text-center">
        <AlertCircle className="mx-auto size-6 text-muted-foreground" />
        <p className="mt-3 text-sm text-muted-foreground">{t("errors.load")}</p>
        <button type="button" onClick={() => void refetch()} className="mt-3 text-sm font-medium underline">{t("errors.retry")}</button>
      </div>
    );
  }

  return (
    <div data-agent-chat className="flex min-h-[calc(100dvh-3.5rem)] flex-col md:min-h-dvh">
      <div className="mx-auto w-full max-w-3xl flex-1 space-y-7 px-4 pb-8 pt-8 sm:px-6">
        {!data ? (
          <div className="space-y-6">
            <Skeleton className="ml-auto h-10 w-2/3 rounded-2xl" />
            <Skeleton className="h-24 w-full rounded-2xl" />
          </div>
        ) : (
          <AnimatePresence initial={false}>
            {shown.map((block, index) => (
              <motion.div
                key={block.kind === "agent" ? `a${index}` : block.item.id}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
              >
                {block.kind === "user" ? <UserBubble item={block.item} />
                  : block.kind === "notice" ? <NoticeRow item={block.item} />
                  : (
                    <AgentBlock
                      parts={block.parts}
                      jobs={live.jobs}
                      mood={index === shown.length - 1 ? lastMood : "idle"}
                      items={items}
                      replyDisabled={running}
                      onReply={(reply) => void send(reply, [])}
                    />
                  )}
              </motion.div>
            ))}
          </AnimatePresence>
        )}
        {showThinking && <Thinking withFace={shown[shown.length - 1]?.kind !== "agent"} />}
        {pendingApproval && (
          <div className="pl-11">
            <ApprovalCard items={pendingApproval} busy={answering} onAnswer={(approved) => void answer(approved)} />
          </div>
        )}
        {live.error && (
          <div className="ml-11 flex items-start gap-2 rounded-xl border border-red-500/20 bg-red-500/5 px-3 py-2.5 text-sm text-red-700 dark:text-red-300">
            <AlertCircle className="mt-0.5 size-4 shrink-0" />{live.error}
          </div>
        )}
        <div ref={bottomRef} />
      </div>
      <div className="sticky bottom-[calc(4rem+env(safe-area-inset-bottom))] z-20 bg-gradient-to-t from-canvas from-70% to-transparent px-4 pb-3 pt-8 sm:px-6 md:bottom-0">
        <div className="mx-auto max-w-3xl">
          <AgentComposer
            onSend={send}
            onStop={() => { void stopConversation(conversationId).then(() => reload()).catch(() => {}); }}
            running={running}
            // Without Creator the chat stays readable; only new messages need the plan.
            disabled={gate.locked && !running}
            placeholder={gate.locked ? t("composer.placeholderLocked") : pendingApproval ? t("composer.placeholderApproval") : t("composer.placeholderReply")}
          />
          {gate.status?.access === "trial" || gate.locked ? (
            <TrialNote status={gate.status} onUpgrade={gate.show} className="mt-2" />
          ) : (
            <p className="mt-2 hidden text-center text-[11px] text-muted-foreground sm:block">{t("composer.disclaimer")}</p>
          )}
          {gate.dialog}
        </div>
      </div>
    </div>
  );
}

