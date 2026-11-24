"use client";

import { PebbleIcon } from "@/components/agent/agent-mark";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { motion } from "motion/react";
import { ChevronLeft, ChevronRight, ExternalLink, Film, Loader2, RotateCcw, XCircle } from "lucide-react";
import { toast } from "sonner";

import { AgentMark } from "@/components/agent/agent-mark";
import { ClipCover } from "@/components/app/clip-cover";
import { SocialProviderIcon } from "@/components/social-provider-icon";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { useAgentStatus } from "@/hooks/use-agent-status";
import { intlLocale } from "@/i18n/config";
import { clipFileUrl, startConversation } from "@/lib/agent";
import { socialPostKeys } from "@/lib/queries/social";
import { PROVIDER_LABELS, type SocialPost, type SocialProviderId } from "@/lib/social";
import { cn } from "@/lib/utils";

type View = "month" | "week";
const PLATFORMS: SocialProviderId[] = ["tiktok", "youtube", "instagram"];

/** Status as a short colored bar inside a chip; cancelled posts fade. */
const STATUS_BAR: Record<string, string> = {
  scheduled: "bg-foreground/50",
  queued: "bg-amber-500",
  publishing: "bg-amber-500",
  published: "bg-emerald-500",
  failed: "bg-red-500",
  cancelled: "bg-muted-foreground/30",
};
const STATUS_DOT: Record<string, string> = {
  scheduled: "bg-foreground/60",
  queued: "bg-amber-500",
  publishing: "bg-amber-500 animate-pulse",
  published: "bg-emerald-500",
  failed: "bg-red-500",
  cancelled: "bg-muted-foreground/40",
};

function startOfDay(date: Date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}
function addDays(date: Date, days: number) {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
}
function sameDay(a: Date, b: Date) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}
/** Monday-first weeks. */
function startOfWeek(date: Date) {
  const day = (date.getDay() + 6) % 7;
  return addDays(startOfDay(date), -day);
}
function dayKey(date: Date) {
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}
function postTime(post: SocialPost) {
  return new Date(post.scheduled_for || post.published_at || post.created_at || Date.now());
}
function localInputValue(date: Date) {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

async function fetchWindow(start: Date, end: Date): Promise<SocialPost[]> {
  const query = new URLSearchParams({ starts_at: start.toISOString(), ends_at: end.toISOString(), limit: "500" });
  const response = await fetch(`/api/social/posts?${query}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`Calendar request failed (${response.status})`);
  return ((await response.json()) as { posts?: SocialPost[] }).posts ?? [];
}

async function patchPost(id: string, body: Record<string, unknown>): Promise<SocialPost> {
  const response = await fetch(`/api/social/posts/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data?.detail === "string" ? data.detail : "Couldn't update the post.");
  return data.post as SocialPost;
}

/** A post as something to click or drag: a div, because browsers won't drag a <button>. */
function dragProps(post: SocialPost, onOpen: () => void, draggable: boolean) {
  return {
    role: "button",
    tabIndex: 0,
    draggable,
    onKeyDown: (event: React.KeyboardEvent) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        onOpen();
      }
    },
  } as const;
}

function PostChip({ post, onOpen, timeFormat, draggable }: { post: SocialPost; onOpen: () => void; timeFormat: Intl.DateTimeFormat; draggable: boolean }) {
  return (
    <div
      {...dragProps(post, onOpen, draggable)}
      onDragStart={(event) => {
        event.dataTransfer.setData("text/post-id", post.id);
        event.dataTransfer.effectAllowed = "move";
      }}
      onClick={onOpen}
      className={cn(
        "group flex w-full cursor-pointer items-center gap-1.5 rounded-md border bg-background py-1 pl-1 pr-1.5 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring text-[11px] leading-tight shadow-xs transition-all hover:-translate-y-px hover:shadow-sm",
        post.status === "cancelled" && "opacity-60",
        draggable && "cursor-grab active:cursor-grabbing",
      )}
    >
      <span className={cn("h-3.5 w-[3px] shrink-0 rounded-full", STATUS_BAR[post.status])} aria-hidden />
      <SocialProviderIcon provider={post.provider} className="size-3 text-muted-foreground" />
      <span className="shrink-0 font-medium tabular-nums">{timeFormat.format(postTime(post))}</span>
      <span className="min-w-0 truncate text-muted-foreground">{post.title || post.hook_title}</span>
    </div>
  );
}

function PostCardLarge({ post, onOpen, timeFormat, draggable }: { post: SocialPost; onOpen: () => void; timeFormat: Intl.DateTimeFormat; draggable: boolean }) {
  return (
    <div
      {...dragProps(post, onOpen, draggable)}
      onDragStart={(event) => { event.dataTransfer.setData("text/post-id", post.id); event.dataTransfer.effectAllowed = "move"; }}
      onClick={onOpen}
      className={cn(
        "group w-full cursor-pointer overflow-hidden rounded-xl border bg-background text-left shadow-xs outline-none focus-visible:ring-2 focus-visible:ring-ring transition-all hover:-translate-y-0.5 hover:shadow-md",
        post.status === "cancelled" && "opacity-60",
        draggable && "cursor-grab active:cursor-grabbing",
      )}
    >
      {post.task_id && post.clip_id ? (
        <ClipCover src={clipFileUrl(post.task_id, post.clip_id)} className="aspect-[4/5] w-full" />
      ) : <div className="flex aspect-[4/5] w-full items-center justify-center bg-muted"><Film className="size-5 text-muted-foreground" /></div>}
      <div className="space-y-0.5 p-2">
        <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <SocialProviderIcon provider={post.provider} className="size-3" />
          <span className="font-medium tabular-nums text-foreground">{timeFormat.format(postTime(post))}</span>
          <span className={cn("ml-auto size-1.5 rounded-full", STATUS_DOT[post.status])} />
        </p>
        <p className="line-clamp-2 text-xs font-medium leading-snug">{post.title || post.hook_title}</p>
      </div>
    </div>
  );
}

/** Every post on one calendar: scheduled, going out, published and failed, in the person's local time. */
export function PostingCalendar() {
  const t = useTranslations("calendar");
  const locale = useLocale();
  const router = useRouter();
  const queryClient = useQueryClient();
  const agent = useAgentStatus();
  const [view, setView] = useState<View>("month");
  const [anchor, setAnchor] = useState(() => startOfDay(new Date()));
  const [platforms, setPlatforms] = useState<Set<SocialProviderId>>(new Set());
  const [openId, setOpenId] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState<string | null>(null);
  const [planning, setPlanning] = useState(false);

  const formats = useMemo(() => ({
    month: new Intl.DateTimeFormat(intlLocale(locale), { month: "long", year: "numeric" }),
    weekday: new Intl.DateTimeFormat(intlLocale(locale), { weekday: "short" }),
    dayLong: new Intl.DateTimeFormat(intlLocale(locale), { weekday: "long", month: "long", day: "numeric" }),
    short: new Intl.DateTimeFormat(intlLocale(locale), { month: "short", day: "numeric" }),
    time: new Intl.DateTimeFormat(intlLocale(locale), { hour: "numeric", minute: "2-digit" }),
  }), [locale]);

  const { days, start, end } = useMemo(() => {
    if (view === "week") {
      const first = startOfWeek(anchor);
      const list = Array.from({ length: 7 }, (_, i) => addDays(first, i));
      return { days: list, start: first, end: addDays(first, 7) };
    }
    const firstOfMonth = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
    const first = startOfWeek(firstOfMonth);
    const lastOfMonth = new Date(anchor.getFullYear(), anchor.getMonth() + 1, 0);
    const weeks = Math.ceil((((lastOfMonth.getTime() - first.getTime()) / 86_400_000) + 1) / 7);
    const list = Array.from({ length: weeks * 7 }, (_, i) => addDays(first, i));
    return { days: list, start: first, end: addDays(first, weeks * 7) };
  }, [view, anchor]);

  const windowKey = [...socialPostKeys.all, "calendar", start.toISOString(), end.toISOString()] as const;
  const { data: posts, isLoading } = useQuery({
    queryKey: windowKey,
    queryFn: () => fetchWindow(start, end),
    refetchInterval: 30_000,
    placeholderData: (previous) => previous,
  });

  const visible = useMemo(
    () => (posts ?? []).filter((post) => post.status !== "cancelled" && (platforms.size === 0 || platforms.has(post.provider))),
    [posts, platforms],
  );
  const byDay = useMemo(() => {
    const map = new Map<string, SocialPost[]>();
    for (const post of [...visible].sort((a, b) => postTime(a).getTime() - postTime(b).getTime())) {
      const key = dayKey(postTime(post));
      map.set(key, [...(map.get(key) ?? []), post]);
    }
    return map;
  }, [visible]);
  const open = (posts ?? []).find((post) => post.id === openId) ?? null;
  const today = startOfDay(new Date());

  const move = (step: number) => {
    setAnchor((current) => view === "week" ? addDays(current, step * 7) : new Date(current.getFullYear(), current.getMonth() + step, 1));
  };

  const updateCache = (post: SocialPost) => {
    queryClient.setQueryData<SocialPost[]>(windowKey, (current) => current?.map((p) => (p.id === post.id ? post : p)));
    void queryClient.invalidateQueries({ queryKey: socialPostKeys.all });
  };

  const reschedule = async (postId: string, day: Date) => {
    const post = posts?.find((p) => p.id === postId);
    if (!post || post.status !== "scheduled") return;
    const current = postTime(post);
    const target = new Date(day.getFullYear(), day.getMonth(), day.getDate(), current.getHours(), current.getMinutes());
    if (sameDay(target, current)) return;
    if (target.getTime() < Date.now() + 60_000) {
      toast.error(t("toasts.past"));
      return;
    }
    const optimistic = { ...post, scheduled_for: target.toISOString() };
    queryClient.setQueryData<SocialPost[]>(windowKey, (list) => list?.map((p) => (p.id === postId ? optimistic : p)));
    try {
      updateCache(await patchPost(postId, { scheduled_for: target.toISOString() }));
      toast.success(t("toasts.moved", { day: formats.short.format(target) }));
    } catch (error) {
      queryClient.setQueryData<SocialPost[]>(windowKey, (list) => list?.map((p) => (p.id === postId ? post : p)));
      toast.error(error instanceof Error ? error.message : t("toasts.failed"));
    }
  };

  const planWithAgent = async () => {
    setPlanning(true);
    try {
      const { conversation } = await startConversation(t("planPrompt"), [], locale);
      router.push(`/agent/${conversation.id}?new=1`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("toasts.failed"));
      setPlanning(false);
    }
  };

  const dropProps = (day: Date) => {
    const key = dayKey(day);
    const past = day < today;
    return {
      onDragOver: (event: React.DragEvent) => {
        if (past || !event.dataTransfer.types.includes("text/post-id")) return;
        event.preventDefault();
        if (dragOver !== key) setDragOver(key);
      },
      onDragLeave: () => setDragOver((current) => (current === key ? null : current)),
      onDrop: (event: React.DragEvent) => {
        event.preventDefault();
        setDragOver(null);
        const id = event.dataTransfer.getData("text/post-id");
        if (id) void reschedule(id, day);
      },
    };
  };

  const title = view === "week"
    ? `${formats.short.format(days[0])} – ${formats.short.format(days[6])}`
    : formats.month.format(anchor);
  const counts = {
    scheduled: visible.filter((p) => p.status === "scheduled").length,
    published: visible.filter((p) => p.status === "published").length,
  };

  return (
    <main className="mx-auto w-full max-w-6xl px-4 pb-16 pt-8 sm:px-6 md:pt-10">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div data-tour="calendar-grid">
          <h1 className="font-display text-2xl font-bold tracking-tight sm:text-3xl">{t("title")}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{t("summary", counts)}</p>
        </div>
        {agent?.available && (
          <Button onClick={() => void planWithAgent()} disabled={planning} variant="outline" className="rounded-full" data-tour="calendar-agent">
            {planning ? <Loader2 className="size-4 animate-spin" /> : <PebbleIcon className="size-4" />}{t("planWithAgent")}
          </Button>
        )}
      </div>

      <div className="mt-6 flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1">
          <Button variant="outline" size="icon" className="size-8 rounded-full" onClick={() => move(-1)} aria-label={t("previous")}><ChevronLeft className="size-4" /></Button>
          <Button variant="outline" size="icon" className="size-8 rounded-full" onClick={() => move(1)} aria-label={t("next")}><ChevronRight className="size-4" /></Button>
        </div>
        <Button variant="ghost" size="sm" className="h-8 rounded-full" onClick={() => setAnchor(startOfDay(new Date()))}>{t("today")}</Button>
        <h2 className="ml-1 min-w-0 font-display text-lg font-bold capitalize tracking-tight">{title}</h2>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1" role="group" aria-label={t("filter")}>
            {PLATFORMS.map((platform) => {
              const on = platforms.has(platform);
              return (
                <button
                  key={platform}
                  type="button"
                  aria-pressed={on}
                  title={PROVIDER_LABELS[platform]}
                  onClick={() => setPlatforms((current) => {
                    const next = new Set(current);
                    if (next.has(platform)) next.delete(platform); else next.add(platform);
                    return next;
                  })}
                  className={cn("flex size-8 items-center justify-center rounded-full border transition-colors", on ? "border-foreground bg-foreground text-background" : "bg-background text-muted-foreground hover:text-foreground")}
                >
                  <SocialProviderIcon provider={platform} className="size-3.5" />
                </button>
              );
            })}
          </div>
          <div className="flex rounded-full border bg-muted/50 p-0.5" role="tablist">
            {(["month", "week"] as const).map((option) => (
              <button
                key={option}
                type="button"
                role="tab"
                aria-selected={view === option}
                onClick={() => setView(option)}
                className={cn("h-7 rounded-full px-3 text-xs font-medium transition-colors", view === option ? "bg-background text-foreground shadow-xs" : "text-muted-foreground hover:text-foreground")}
              >
                {t(`views.${option}`)}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Desktop grid */}
      <motion.div
        key={`${view}-${start.toISOString()}`}
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
        className="mt-5 hidden overflow-hidden rounded-2xl border bg-background md:block"
      >
        <div className="grid grid-cols-7 border-b bg-muted/40">
          {days.slice(0, 7).map((day) => (
            <div key={day.toISOString()} className="px-3 py-2 text-xs font-medium capitalize text-muted-foreground">{formats.weekday.format(day)}</div>
          ))}
        </div>
        <div className="grid grid-cols-7">
          {days.map((day, index) => {
            const key = dayKey(day);
            const items = byDay.get(key) ?? [];
            const outside = view === "month" && day.getMonth() !== anchor.getMonth();
            const isToday = sameDay(day, today);
            const past = day < today;
            const limit = view === "month" ? 3 : 99;
            return (
              <div
                key={key}
                {...dropProps(day)}
                className={cn(
                  "group/day relative border-b border-r p-1.5 transition-colors",
                  view === "month" ? "min-h-[7.5rem]" : "min-h-[28rem]",
                  (index + 1) % 7 === 0 && "border-r-0",
                  index >= days.length - 7 && "border-b-0",
                  outside && "bg-muted/25",
                  past && !isToday && "bg-muted/15",
                  dragOver === key && "bg-brand-soft/60 ring-2 ring-inset ring-brand/40",
                )}
              >
                <div className="mb-1 flex items-center justify-between px-1">
                  <span className={cn(
                    "flex size-6 items-center justify-center rounded-full text-xs tabular-nums",
                    isToday ? "bg-foreground font-semibold text-background" : outside ? "text-muted-foreground/60" : "text-muted-foreground",
                  )}>{day.getDate()}</span>
                  {view === "week" && <span className="text-[11px] text-muted-foreground">{items.length || ""}</span>}
                </div>
                <div className={cn("space-y-1", view === "week" && "space-y-2")}>
                  {items.slice(0, limit).map((post) => view === "week" ? (
                    <PostCardLarge key={post.id} post={post} timeFormat={formats.time} draggable={post.status === "scheduled"} onOpen={() => setOpenId(post.id)} />
                  ) : (
                    <PostChip key={post.id} post={post} timeFormat={formats.time} draggable={post.status === "scheduled"} onOpen={() => setOpenId(post.id)} />
                  ))}
                  {items.length > limit && (
                    <button type="button" onClick={() => { setView("week"); setAnchor(day); }} className="px-1 text-[11px] font-medium text-muted-foreground hover:text-foreground">
                      {t("more", { count: items.length - limit })}
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </motion.div>

      {/* Mobile agenda */}
      <div className="mt-5 space-y-5 md:hidden">
        {isLoading ? (
          [0, 1, 2].map((key) => <Skeleton key={key} className="h-20 rounded-xl" />)
        ) : days.filter((day) => (byDay.get(dayKey(day)) ?? []).length > 0).length === 0 ? null : (
          days.filter((day) => (byDay.get(dayKey(day)) ?? []).length > 0).map((day) => (
            <section key={dayKey(day)}>
              <h3 className={cn("mb-2 text-xs font-medium capitalize", sameDay(day, today) ? "text-foreground" : "text-muted-foreground")}>{formats.dayLong.format(day)}</h3>
              <div className="space-y-1.5">
                {(byDay.get(dayKey(day)) ?? []).map((post) => (
                  <button key={post.id} type="button" onClick={() => setOpenId(post.id)} className={cn("flex w-full items-center gap-3 rounded-xl border bg-background p-2 text-left", post.status === "cancelled" && "opacity-60")}>
                    {post.task_id && post.clip_id && <ClipCover src={clipFileUrl(post.task_id, post.clip_id)} className="h-12 w-[1.8rem] shrink-0 rounded-md" />}
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-1.5 text-xs text-muted-foreground"><SocialProviderIcon provider={post.provider} className="size-3" />{formats.time.format(postTime(post))}</span>
                      <span className="block truncate text-sm font-medium">{post.title || post.hook_title}</span>
                    </span>
                    <span className={cn("size-2 shrink-0 rounded-full", STATUS_DOT[post.status])} />
                  </button>
                ))}
              </div>
            </section>
          ))
        )}
      </div>

      {!isLoading && visible.length === 0 && (
        <div className="mx-auto mt-8 flex max-w-md flex-col items-center rounded-2xl border border-dashed px-6 py-10 text-center">
          <AgentMark />
          <p className="mt-3 font-medium">{t("empty.title")}</p>
          <p className="mt-1 text-sm text-muted-foreground">{agent?.available ? t("empty.bodyAgent") : t("empty.body")}</p>
          {agent?.available ? (
            <Button onClick={() => void planWithAgent()} disabled={planning} className="mt-4 rounded-full">
              {planning ? <Loader2 className="size-4 animate-spin" /> : <PebbleIcon className="size-4" />}{t("planWithAgent")}
            </Button>
          ) : (
            <Button asChild variant="outline" className="mt-4 rounded-full"><Link href="/list">{t("empty.library")}</Link></Button>
          )}
        </div>
      )}

      <PostSheet post={open} onClose={() => setOpenId(null)} onChanged={updateCache} formats={formats} />
    </main>
  );
}

function PostSheet({ post, onClose, onChanged, formats }: {
  post: SocialPost | null;
  onClose: () => void;
  onChanged: (post: SocialPost) => void;
  formats: { dayLong: Intl.DateTimeFormat; time: Intl.DateTimeFormat };
}) {
  const t = useTranslations("calendar");
  const tAgent = useTranslations("agent");
  const [when, setWhen] = useState("");
  const [caption, setCaption] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    if (!post) return;
    setWhen(localInputValue(postTime(post)));
    setCaption(post.caption ?? "");
  }, [post]);

  if (!post) return <Sheet open={false} onOpenChange={onClose}><SheetContent /></Sheet>;
  const editable = post.status === "scheduled";
  const date = postTime(post);
  const dirty = editable && (when !== localInputValue(date) || caption !== (post.caption ?? ""));

  const act = async (name: string, run: () => Promise<SocialPost | null>) => {
    setBusy(name);
    try {
      const updated = await run();
      if (updated) onChanged(updated);
      toast.success(t(`toasts.${name}`));
      if (name === "cancelled") onClose();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("toasts.failed"));
    } finally {
      setBusy(null);
    }
  };

  const save = () => act("saved", () => patchPost(post.id, {
    ...(when !== localInputValue(date) ? { scheduled_for: new Date(when).toISOString() } : {}),
    ...(caption !== (post.caption ?? "") ? { caption } : {}),
  }));
  const post_ = async (path: string) => {
    const response = await fetch(`/api/social/posts/${post.id}/${path}`, { method: "POST" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data?.detail === "string" ? data.detail : t("toasts.failed"));
    return data.post as SocialPost;
  };

  return (
    <Sheet open onOpenChange={(value) => { if (!value) onClose(); }}>
      <SheetContent className="w-full gap-0 overflow-y-auto sm:max-w-md">
        <SheetHeader className="border-b">
          <SheetTitle className="flex items-center gap-2 pr-6">
            <SocialProviderIcon provider={post.provider} className="size-4" />
            <span className="truncate">{post.title || post.hook_title || t("untitled")}</span>
          </SheetTitle>
          <SheetDescription className="flex items-center gap-2">
            <span className={cn("size-1.5 rounded-full", STATUS_DOT[post.status])} />
            {tAgent(`postStatus.${post.status}`)} · {post.account.display_name || post.account.username || PROVIDER_LABELS[post.provider]}
          </SheetDescription>
        </SheetHeader>
        <div className="space-y-5 p-4">
          {post.task_id && post.clip_id && (
            <div className="mx-auto w-44 overflow-hidden rounded-2xl bg-black shadow-md">
              <video src={clipFileUrl(post.task_id, post.clip_id)} controls playsInline preload="metadata" className="aspect-[9/16] w-full object-cover" />
            </div>
          )}
          {post.error_message && post.status === "failed" && (
            <p className="rounded-xl border border-red-500/20 bg-red-500/5 px-3 py-2 text-sm text-red-700 dark:text-red-300">{post.error_message}</p>
          )}
          <div className="space-y-1.5">
            <label htmlFor="post-when" className="text-xs font-medium text-muted-foreground">{editable ? t("sheet.when") : t("sheet.wentOut")}</label>
            {editable ? (
              <input id="post-when" type="datetime-local" value={when} onChange={(event) => setWhen(event.target.value)} className="h-9 w-full rounded-lg border bg-background px-3 text-sm" />
            ) : (
              <p className="text-sm">{formats.dayLong.format(date)}, {formats.time.format(date)}</p>
            )}
          </div>
          <div className="space-y-1.5">
            <label htmlFor="post-caption" className="text-xs font-medium text-muted-foreground">{t("sheet.caption")}</label>
            {editable ? (
              <Textarea id="post-caption" value={caption} onChange={(event) => setCaption(event.target.value)} rows={5} className="text-sm" />
            ) : (
              <p className="whitespace-pre-wrap text-sm text-muted-foreground">{post.caption || "—"}</p>
            )}
            {post.hashtags.length > 0 && <p className="text-xs text-muted-foreground">{post.hashtags.map((tag) => `#${tag}`).join(" ")}</p>}
          </div>
          {post.metrics?.views != null && (
            <div className="grid grid-cols-3 gap-2 rounded-xl bg-muted/50 p-3 text-center">
              {(["views", "likes", "comments"] as const).map((metric) => (
                <div key={metric}>
                  <p className="font-display text-lg font-bold tabular-nums">{new Intl.NumberFormat(undefined, { notation: "compact" }).format(post.metrics?.[metric] ?? 0)}</p>
                  <p className="text-[11px] text-muted-foreground">{t(`sheet.${metric}`)}</p>
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="mt-auto flex flex-wrap gap-2 border-t p-4">
          {editable && (
            <Button onClick={() => void save()} disabled={!dirty || busy !== null} className="rounded-full">
              {busy === "saved" && <Loader2 className="size-4 animate-spin" />}{t("sheet.save")}
            </Button>
          )}
          {(post.status === "scheduled" || post.status === "queued") && (
            <Button variant="outline" onClick={() => void act("cancelled", () => post_("cancel"))} disabled={busy !== null} className="rounded-full">
              <XCircle className="size-4" />{t("sheet.cancel")}
            </Button>
          )}
          {post.status === "failed" && (
            <Button variant="outline" onClick={() => void act("retried", () => post_("retry"))} disabled={busy !== null} className="rounded-full">
              <RotateCcw className="size-4" />{t("sheet.retry")}
            </Button>
          )}
          {post.external_url && (
            <Button asChild variant="outline" className="rounded-full"><a href={post.external_url} target="_blank" rel="noreferrer"><ExternalLink className="size-4" />{t("sheet.view")}</a></Button>
          )}
          {post.task_id && (
            <Button asChild variant="ghost" className="rounded-full"><Link href={`/tasks/${post.task_id}`}>{t("sheet.project")}</Link></Button>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
