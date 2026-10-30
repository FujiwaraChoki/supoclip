"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";
import {
  AlertCircle,
  Check,
  Clock,
  ExternalLink,
  Loader2,
  MoreHorizontal,
  RefreshCw,
  RotateCcw,
  Send,
  Trash2,
  XCircle,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { SocialProviderIcon } from "@/components/social-provider-icon";
import { timeAgo } from "@/lib/generations";
import {
  ACTIVE_POST_STATUSES,
  PROVIDER_LABELS,
  connectionLabel,
  formatDateTime,
  formatMetric,
  parseHashtagInput,
  type SocialConnection,
  type SocialPost,
  type SocialPostStatus,
  type SocialPrivacyLevel,
  type SocialProviderStatus,
} from "@/lib/social";

interface PublishableClip {
  id: string;
  clip_order: number;
  hook_title: string | null;
  text: string | null;
}

interface ClipPublishButtonProps {
  taskId: string;
  clip: PublishableClip;
  connections: SocialConnection[];
  providers: SocialProviderStatus[];
  onChanged: () => void | Promise<void>;
}

interface ClipPostListProps {
  posts: SocialPost[];
  onChanged: () => void | Promise<void>;
}

const ALL_PRIVACY_LEVELS: SocialPrivacyLevel[] = ["public", "unlisted", "private"];

function privacyOptionsFor(provider: SocialProviderStatus | undefined): SocialPrivacyLevel[] {
  return provider?.supported_privacy_levels ?? ALL_PRIVACY_LEVELS;
}

// Keep each platform’s default when selecting several destinations.
function defaultPrivacyFor(provider: SocialProviderStatus | undefined): SocialPrivacyLevel {
  const options = privacyOptionsFor(provider);
  const preferred = provider?.default_privacy_level;
  return preferred && options.includes(preferred) ? preferred : (options[0] ?? "private");
}

function toLocalDateTimeInput(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`;
}

async function readError(response: Response, fallback: string): Promise<string> {
  const data = await response.json().catch(() => ({}));
  if (typeof data?.detail === "string") return data.detail;
  if (Array.isArray(data?.detail) && data.detail[0]?.msg) return data.detail[0].msg;
  return fallback;
}

function postAccountName(post: SocialPost): string {
  return (
    post.account.display_name ||
    (post.account.username ? `@${post.account.username.replace(/^@/, "")}` : null) ||
    PROVIDER_LABELS[post.provider]
  );
}

function metricsSummary(post: SocialPost, label: (value: number | null | undefined, one: "view" | "like" | "comment") => string): string | null {
  if (!post.last_metrics_at || !post.metrics) return null;
  const { views, likes, comments } = post.metrics;
  return [label(views, "view"), label(likes, "like"), label(comments, "comment")].join(" · ");
}

function PostStatus({ post }: { post: SocialPost }) {
  const t = useTranslations("social");
  const locale = useLocale();
  switch (post.status) {
    case "queued":
    case "publishing":
      return (
        <span className="inline-flex items-center gap-1.5 text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin" />
          {post.status === "queued" ? t("postStatus.starting") : t("postStatus.uploading")}
        </span>
      );
    case "scheduled":
      return (
        <span className="inline-flex items-center gap-1.5 text-muted-foreground">
          <Clock className="size-3.5" />
          {post.scheduled_for ? formatDateTime(post.scheduled_for) : t("postStatus.scheduled")}
        </span>
      );
    case "published":
      return (
        <span className="text-muted-foreground">
          {post.published_at ? t("postStatus.publishedAgo", { time: timeAgo(post.published_at, locale) }) : t("postStatus.published")}
        </span>
      );
    case "failed":
      return <span className="text-destructive">{t("postStatus.failed")}</span>;
    case "cancelled":
      return <span className="text-muted-foreground">{t("postStatus.cancelled")}</span>;
    default:
      return <span className="text-muted-foreground">{post.status}</span>;
  }
}

/**
 * Toasts when a post the user is watching changes state, so publishing never
 * "just happens" silently. Statuses seen on first load are only recorded.
 */
export function useSocialPostToasts(posts: SocialPost[]) {
  const t = useTranslations("social");
  const seen = useRef<Map<string, SocialPostStatus> | null>(null);
  useEffect(() => {
    const previous = seen.current;
    const next = new Map(posts.map((post) => [post.id, post.status] as const));
    seen.current = next;
    if (!previous) return;
    for (const post of posts) {
      const before = previous.get(post.id);
      if (!before || before === post.status) continue;
      const id = postToastId(post.id);
      const platform = PROVIDER_LABELS[post.provider];
      if (post.status === "published") {
        toast.success(t("toasts.published", { platform }), {
          id,
          description: post.privacy_level === "public" ? undefined : t("toasts.visibility", { privacy: t(`privacy.${post.privacy_level}`) }),
          action: post.external_url
            ? { label: t("toasts.open"), onClick: () => window.open(post.external_url!, "_blank", "noopener") }
            : undefined,
        });
      } else if (post.status === "failed") {
        toast.error(t("toasts.failed", { platform }), { id, description: post.error_message ?? undefined });
      } else if (ACTIVE_POST_STATUSES.includes(post.status)) {
        toast.loading(t("toasts.publishing", { platform }), { id });
      }
    }
  }, [posts, t]);
}

function postToastId(postId: string) {
  return `social-post-${postId}`;
}

export function ClipPostList({ posts, onChanged }: ClipPostListProps) {
  const t = useTranslations("social");
  const [busyPostId, setBusyPostId] = useState<string | null>(null);
  const metricLabel = (value: number | null | undefined, one: "view" | "like" | "comment") =>
    `${formatMetric(value)} ${value === 1 ? t(`metrics.${one}`) : t(`metrics.${one}s`)}`;

  const runPostAction = async (post: SocialPost, action: "cancel" | "retry" | "refresh-metrics" | "delete") => {
    setBusyPostId(post.id);
    try {
      const response = await fetch(
        action === "delete" ? `/api/social/posts/${post.id}` : `/api/social/posts/${post.id}/${action}`,
        { method: action === "delete" ? "DELETE" : "POST" },
      );
      if (!response.ok) {
        toast.error(await readError(response, t("toasts.actionFailed")));
      } else if (action === "retry") {
        toast.loading(t("toasts.publishing", { platform: PROVIDER_LABELS[post.provider] }), { id: postToastId(post.id) });
      } else if (action === "cancel") {
        toast.success(t("toasts.cancelled"));
      } else if (action === "refresh-metrics") {
        toast.success(t("toasts.metrics"));
      }
      await onChanged();
    } finally {
      setBusyPostId(null);
    }
  };

  if (posts.length === 0) return null;

  return (
    <div className="mt-3 space-y-2">
      {posts.map((post) => {
        const busy = busyPostId === post.id;
        const metrics = metricsSummary(post, metricLabel);
        const canCancel = post.status === "scheduled" || post.status === "queued";
        const canRetry = post.status === "failed" || post.status === "cancelled";
        const canRefresh = post.status === "published" && Boolean(post.external_post_id);
        const canRemove = post.status !== "queued" && post.status !== "publishing";
        const hasMenu = canCancel || canRetry || canRefresh || canRemove;
        return (
          <div key={post.id} className="flex items-center gap-3 rounded-lg border bg-card px-3 py-2.5 text-sm">
            <SocialProviderIcon provider={post.provider} className="text-muted-foreground" />
            <div className="min-w-0 flex-1">
              <div className="flex min-w-0 items-center gap-2">
                <span className="truncate font-medium">{postAccountName(post)}</span>
                <span className="text-muted-foreground/50">·</span>
                <span className="shrink-0"><PostStatus post={post} /></span>
              </div>
              {post.status === "failed" && post.error_message ? (
                <p className="mt-0.5 line-clamp-2 text-xs text-destructive" title={post.error_message}>
                  {post.error_message}
                </p>
              ) : metrics ? (
                <p className="mt-0.5 text-xs text-muted-foreground">{metrics}</p>
              ) : null}
            </div>
            {post.status === "published" && post.external_url && (
              <Button asChild size="icon" variant="ghost" className="size-8 text-muted-foreground">
                <a href={post.external_url} target="_blank" rel="noreferrer" aria-label={t("openPost")}>
                  <ExternalLink className="size-4" />
                </a>
              </Button>
            )}
            {hasMenu && (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button size="icon" variant="ghost" className="size-8 text-muted-foreground" disabled={busy} aria-label={t("postActions")}>
                    {busy ? <Loader2 className="size-4 animate-spin" /> : <MoreHorizontal className="size-4" />}
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  {canRefresh && (
                    <DropdownMenuItem onSelect={() => runPostAction(post, "refresh-metrics")}>
                      <RefreshCw /> {t("refreshMetrics")}
                    </DropdownMenuItem>
                  )}
                  {canRetry && (
                    <DropdownMenuItem onSelect={() => runPostAction(post, "retry")}>
                      <RotateCcw /> {t("tryAgain")}
                    </DropdownMenuItem>
                  )}
                  {canCancel && (
                    <DropdownMenuItem onSelect={() => runPostAction(post, "cancel")}>
                      <XCircle /> {t("cancelPost")}
                    </DropdownMenuItem>
                  )}
                  {canRemove && (canRefresh || canRetry || canCancel) && <DropdownMenuSeparator />}
                  {canRemove && (
                    <DropdownMenuItem variant="destructive" onSelect={() => runPostAction(post, "delete")}>
                      <Trash2 /> {t("remove")}
                    </DropdownMenuItem>
                  )}
                </DropdownMenuContent>
              </DropdownMenu>
            )}
          </div>
        );
      })}
    </div>
  );
}

export function ClipPublishButton({
  taskId,
  clip,
  connections,
  providers,
  onChanged,
}: ClipPublishButtonProps) {
  const t = useTranslations("social");
  const [open, setOpen] = useState(false);
  const [accountIds, setAccountIds] = useState<string[]>([]);
  const [submissions, setSubmissions] = useState<Record<string, { status: "accepted" | "failed" | "unknown"; message?: string; scheduled?: boolean }>>({});
  const submitting = useRef(false);
  const [title, setTitle] = useState("");
  const [caption, setCaption] = useState("");
  const [hashtags, setHashtags] = useState("");
  const [privacyByAccount, setPrivacyByAccount] = useState<Record<string, SocialPrivacyLevel | "">>({});
  const [accountPrivacy, setAccountPrivacy] = useState<Record<string, { levels: SocialPrivacyLevel[]; loading?: boolean; error?: string }>>({});
  const optionsEpoch = useRef(0);
  const optionRequests = useRef<Record<string, number>>({});
  const [schedule, setSchedule] = useState(false);
  const [scheduledFor, setScheduledFor] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isGeneratingCaption, setIsGeneratingCaption] = useState(false);
  const captionRevision = useRef(0);
  const captionRequest = useRef(0);
  const [error, setError] = useState<string | null>(null);

  const activeConnections = useMemo(
    () => connections.filter(
      (connection) => connection.status !== "revoked" && providers.some(
        (provider) => provider.provider === connection.provider && provider.configured,
      ),
    ),
    [connections, providers],
  );
  const providerForAccount = (id: string) =>
    providers.find(
      (provider) =>
        provider.provider === activeConnections.find((connection) => connection.id === id)?.provider,
    );
  const selectedConnections = activeConnections.filter(
    (connection) => accountIds.includes(connection.id) && connection.status !== "reauth_required",
  );
  const pendingConnections = selectedConnections.filter(
    (connection) => !submissions[connection.id] || submissions[connection.id].status === "failed",
  );
  const hasAccepted = Object.values(submissions).some((result) => result.status === "accepted");
  const hasTikTok = selectedConnections.some((connection) => connection.provider === "tiktok");
  const titleMaxLength = selectedConnections.some((connection) => connection.provider === "youtube") ? 100 : 255;
  const privacyFor = (id: string) => {
    if (activeConnections.find((c) => c.id === id)?.provider === "tiktok") return privacyByAccount[id] ?? "";
    const provider = providerForAccount(id);
    const chosen = privacyByAccount[id];
    return chosen && privacyOptionsFor(provider).includes(chosen) ? chosen : defaultPrivacyFor(provider);
  };
  const anyProviderConfigured = providers.some((provider) => provider.configured);
  const privacyOptionsForAccount = (id: string) => activeConnections.find((c) => c.id === id)?.provider === "tiktok"
    ? accountPrivacy[id]?.levels ?? [] : privacyOptionsFor(providerForAccount(id));
  const privacyReady = pendingConnections.every((connection) => {
    const level = privacyFor(connection.id);
    return level !== "" && privacyOptionsForAccount(connection.id).includes(level);
  });

  const loadPrivacyOptions = async (id: string, epoch: number) => {
    const request = (optionRequests.current[id] ?? 0) + 1;
    optionRequests.current[id] = request;
    setAccountPrivacy((previous) => ({ ...previous, [id]: { levels: [], loading: true } }));
    setPrivacyByAccount((previous) => ({ ...previous, [id]: "" }));
    try {
      const response = await fetch(`/api/social/connections/${encodeURIComponent(id)}/publishing-options`, { cache: "no-store", signal: AbortSignal.timeout(15_000) });
      if (!response.ok) throw new Error(await readError(response, t("privacyLoadFailed")));
      const data = await response.json();
      const levels = ALL_PRIVACY_LEVELS.filter((level) => Array.isArray(data?.privacy_levels) && data.privacy_levels.includes(level));
      if (levels.length === 0) throw new Error(t("privacyLoadFailed"));
      if (epoch === optionsEpoch.current && request === optionRequests.current[id]) {
        setAccountPrivacy((previous) => ({ ...previous, [id]: { levels } }));
      }
    } catch (error) {
      if (epoch === optionsEpoch.current && request === optionRequests.current[id]) {
        setAccountPrivacy((previous) => ({ ...previous, [id]: { levels: [], error: error instanceof Error ? error.message : t("privacyLoadFailed") } }));
      }
    }
  };

  const generateCaption = async (id: string) => {
    const connection = activeConnections.find((item) => item.id === id);
    if (!connection) return;
    const request = ++captionRequest.current;
    const revision = captionRevision.current;
    setIsGeneratingCaption(true);
    setError(null);
    try {
      const response = await fetch("/api/social/captions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_id: taskId, clip_id: clip.id, provider: connection.provider }),
      });
      if (!response.ok) throw new Error(await readError(response, t("captionFailed")));
      const data = await response.json();
      if (typeof data.caption !== "string" || !data.caption.trim()) throw new Error(t("captionFailed"));
      // A late response must never replace edits or a newer form's draft.
      if (request === captionRequest.current && revision === captionRevision.current) {
        setCaption(data.caption);
      }
    } catch (err) {
      if (request === captionRequest.current) {
        setError(err instanceof Error ? err.message : t("captionFailed"));
      }
    } finally {
      if (request === captionRequest.current) setIsGeneratingCaption(false);
    }
  };

  const changeOpen = (value: boolean) => {
    if (submitting.current) return;
    if (!value) {
      optionsEpoch.current++;
      captionRequest.current++;
      setIsGeneratingCaption(false);
    }
    setOpen(value);
  };

  // The form resets only when the sheet opens, so connections refreshing in
  // the background never wipe what the user is typing.
  const openSheet = () => {
    const available = activeConnections.filter((connection) => connection.status !== "reauth_required");
    const retained = accountIds.filter((id) => available.some((connection) => connection.id === id));
    const nextIds = retained.length ? retained : available.slice(0, 1).map((connection) => connection.id);
    setError(null);
    setSubmissions({});
    setTitle(clip.hook_title || t("clipFallback", { order: clip.clip_order }));
    setCaption(clip.text || "");
    setHashtags("");
    setSchedule(false);
    setScheduledFor(toLocalDateTimeInput(new Date(Date.now() + 60 * 60 * 1000)));
    setAccountIds(nextIds);
    const epoch = ++optionsEpoch.current;
    setAccountPrivacy({});
    setPrivacyByAccount(Object.fromEntries(activeConnections.map((connection) => [
      connection.id, connection.provider === "tiktok" ? "" : defaultPrivacyFor(providerForAccount(connection.id)),
    ])));
    for (const connection of available) {
      if (connection.provider === "tiktok") void loadPrivacyOptions(connection.id, epoch);
    }
    setOpen(true);
    if (nextIds[0]) void generateCaption(nextIds[0]);
  };

  const toggleAccount = (id: string) => {
    captionRequest.current++;
    setIsGeneratingCaption(false);
    setAccountIds((ids) => ids.includes(id) ? ids.filter((value) => value !== id) : [...ids, id]);
  };

  // Without a configured platform there is nothing to publish to.
  if (!anyProviderConfigured) return null;

  const handleSubmit = async () => {
    if (submitting.current || pendingConnections.length === 0 || !privacyReady) return;
    if (title.length > titleMaxLength) {
      setError(t("titleTooLong", { limit: titleMaxLength }));
      return;
    }
    let when: string | undefined;
    if (schedule) {
      const date = new Date(scheduledFor);
      if (!scheduledFor || Number.isNaN(date.getTime()) || date.getTime() <= Date.now()) {
        setError(t("invalidDate"));
        return;
      }
      when = date.toISOString();
    }
    submitting.current = true;
    setIsSubmitting(true);
    setError(null);
    try {
      const results = await Promise.all(pendingConnections.map(async (connection) => {
        const record = (result: typeof submissions[string]) => {
          setSubmissions((previous) => ({ ...previous, [connection.id]: result }));
          return result.status === "accepted";
        };
        let response: Response;
        try {
          response = await fetch("/api/social/posts", {
            signal: AbortSignal.timeout(30_000),
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              task_id: taskId,
              clip_id: clip.id,
              social_account_id: connection.id,
              title: title.trim(),
              caption,
              hashtags: parseHashtagInput(hashtags),
              privacy_level: privacyFor(connection.id),
              ...(when ? { scheduled_for: when } : {}),
            }),
          });
        } catch {
          // The server may have accepted the request. Never retry it automatically.
          return record({ status: "unknown", message: t("submissionUnknown") });
        }
        if (!response.ok) {
          return record(response.status >= 500 || response.status === 408
            ? { status: "unknown", message: t("submissionUnknown") }
            : { status: "failed", message: await readError(response, t("publishFailed")) });
        }
        const payload = await response.json().catch(() => null) as { post?: SocialPost } | null;
        if (!payload?.post?.id) return record({ status: "unknown", message: t("submissionUnknown") });
        const post = payload.post;
        const isScheduled = post.status === "scheduled";
        record({ status: "accepted", scheduled: isScheduled });
        const platform = PROVIDER_LABELS[connection.provider];
        if (isScheduled && post.scheduled_for) {
          toast.success(t("toasts.scheduled", { when: formatDateTime(post.scheduled_for) }), {
            description: t("toasts.scheduledDetail", { platform, account: connectionLabel(connection) }),
          });
        } else {
          toast.loading(t("toasts.publishing", { platform }), {
            id: postToastId(post.id), description: t("toasts.keepWorking"),
          });
        }
        return true;
      }));
      if (results.every(Boolean)) setOpen(false);
      // Always refresh: a failed/ambiguous response can still have created a post.
      try {
        await onChanged();
      } catch {
        toast.error(t("toasts.actionFailed"));
      }
    } finally {
      submitting.current = false;
      setIsSubmitting(false);
    }
  };

  return (
    <>
      <Button size="sm" variant="outline" onClick={openSheet}>
        <Send className="w-4 h-4" />
        {t("publish")}
      </Button>

      <Sheet open={open} onOpenChange={changeOpen}>
        <SheetContent className="w-full sm:max-w-lg overflow-y-auto" showCloseButton={!isSubmitting}>
          <SheetHeader>
            <SheetTitle>{t("publishTitle", { order: clip.clip_order })}</SheetTitle>
            <SheetDescription>
              {t("publishBody")}
            </SheetDescription>
          </SheetHeader>

          <div className="px-4 pb-4 space-y-5">
            {error && (
              <Alert variant="destructive">
                <AlertCircle className="h-4 w-4" />
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}

            {activeConnections.length === 0 ? (
              <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
                {t.rich("emptyAccounts", {
                  link: (chunks) => (
                    <Link href="/settings/social" className="font-medium text-foreground underline underline-offset-4">
                      {chunks}
                    </Link>
                  ),
                })}
              </div>
            ) : (
              <>
                <fieldset className="space-y-2" disabled={isSubmitting}>
                  <legend className="text-sm font-medium">{t("accounts")}</legend>
                  <p className="text-xs text-muted-foreground">{t("accountsHint")}</p>
                  <div className="grid gap-2">
                    {activeConnections.map((connection) => {
                      const selected = accountIds.includes(connection.id);
                      const result = submissions[connection.id];
                      const locked = result?.status === "accepted" || result?.status === "unknown";
                      const unavailable = connection.status === "reauth_required";
                      const inputId = `publish-account-${clip.id}-${connection.id}`;
                      return (
                        <div key={connection.id}>
                          <label htmlFor={inputId} className={`flex items-center gap-3 rounded-xl border p-3 transition-colors ${selected ? "border-foreground/40 bg-muted/50" : "hover:bg-muted/30"} ${unavailable || locked || isSubmitting ? "cursor-default opacity-70" : "cursor-pointer"}`}>
                            <SocialProviderIcon provider={connection.provider} className="size-5 shrink-0" />
                            <span className="min-w-0 flex-1">
                              <span className="block text-sm font-medium">{PROVIDER_LABELS[connection.provider]}</span>
                              <span className="block truncate text-xs text-muted-foreground">{connectionLabel(connection)}{unavailable ? t("reconnectRequired") : ""}</span>
                              {result?.status === "accepted" && <span className="mt-1 flex items-center gap-1 text-xs"><Check className="size-3" />{result.scheduled ? t("postStatus.scheduled") : t("postStatus.queued")}</span>}
                            </span>
                            <Checkbox id={inputId} checked={selected} onCheckedChange={() => toggleAccount(connection.id)} disabled={unavailable || locked || isSubmitting} aria-label={`${PROVIDER_LABELS[connection.provider]} · ${connectionLabel(connection)}`} />
                          </label>
                          {result?.message && <p role="alert" className="mt-1 px-1 text-xs text-destructive">{result.message}</p>}
                        </div>
                      );
                    })}
                  </div>
                  {hasAccepted && <p className="text-xs text-muted-foreground">{t("submittedHint")}</p>}
                </fieldset>

                <div className="space-y-2">
                  <Label htmlFor={`publish-title-${clip.id}`}>{t("title")}</Label>
                  <Input
                    id={`publish-title-${clip.id}`}
                    value={title}
                    maxLength={titleMaxLength}
                    disabled={isSubmitting}
                    onChange={(event) => setTitle(event.target.value)}
                  />
                  {hasTikTok && (
                    <p className="text-xs text-muted-foreground">{t("tiktokTitle")}</p>
                  )}
                </div>

                <div className="space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <Label htmlFor={`publish-caption-${clip.id}`}>{t("caption")}</Label>
                    <Button type="button" size="sm" variant="ghost" disabled={isGeneratingCaption || isSubmitting || pendingConnections.length === 0} onClick={() => void generateCaption(pendingConnections[0].id)}>
                      {isGeneratingCaption ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
                      {isGeneratingCaption ? t("generatingCaption") : t("generateCaption")}
                    </Button>
                  </div>
                  <Textarea
                    id={`publish-caption-${clip.id}`}
                    value={caption}
                    rows={5}
                    disabled={isSubmitting}
                    maxLength={2200}
                    onChange={(event) => {
                      captionRevision.current++;
                      setCaption(event.target.value);
                    }}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor={`publish-hashtags-${clip.id}`}>{t("hashtags")}</Label>
                  <Input
                    id={`publish-hashtags-${clip.id}`}
                    value={hashtags}
                    disabled={isSubmitting}
                    placeholder={t("hashtagsPlaceholder")}
                    onChange={(event) => setHashtags(event.target.value)}
                  />
                </div>

                {selectedConnections.map((connection) => {
                  const result = submissions[connection.id];
                  const locked = result?.status === "accepted" || result?.status === "unknown";
                  const privacyId = `publish-privacy-${clip.id}-${connection.id}`;
                  return (
                    <div key={connection.id} className="space-y-2">
                      <div className="flex items-center justify-between gap-2">
                        <Label htmlFor={privacyId}>{selectedConnections.length === 1 ? t("visibilityLabel") : t("visibilityForAccount", { account: `${PROVIDER_LABELS[connection.provider]} · ${connectionLabel(connection)}` })}</Label>
                        {connection.provider === "tiktok" && <Button type="button" variant="ghost" size="sm" disabled={isSubmitting || locked || accountPrivacy[connection.id]?.loading} onClick={() => void loadPrivacyOptions(connection.id, optionsEpoch.current)}>{t("refreshPrivacy")}</Button>}
                      </div>
                      <Select value={privacyFor(connection.id)} disabled={isSubmitting || locked || privacyOptionsForAccount(connection.id).length === 0} onValueChange={(value) => setPrivacyByAccount((previous) => ({ ...previous, [connection.id]: value as SocialPrivacyLevel }))}>
                        <SelectTrigger id={privacyId} className="w-full"><SelectValue placeholder={t("chooseVisibility")} /></SelectTrigger>
                        <SelectContent>
                          {privacyOptionsForAccount(connection.id).map((option) => (
                            <SelectItem key={option} value={option}>{t(`privacy.${option}`)}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      {connection.provider === "tiktok" && <p className="text-xs text-muted-foreground">{t("tiktokPrivate")}</p>}
                      {connection.provider === "tiktok" && accountPrivacy[connection.id]?.loading && <p className="text-xs text-muted-foreground">{t("loadingPrivacy")}</p>}
                      {accountPrivacy[connection.id]?.error && <p role="alert" className="text-xs text-destructive">{accountPrivacy[connection.id].error}</p>}
                      {connection.provider === "tiktok" && !accountPrivacy[connection.id]?.loading && !accountPrivacy[connection.id]?.error && accountPrivacy[connection.id]?.levels.length && !accountPrivacy[connection.id].levels.includes("public") ? <p className="text-xs text-muted-foreground">{t("tiktokPublicUnavailable")}</p> : null}
                    </div>
                  );
                })}

                <div className="flex items-center justify-between rounded-lg border p-3">
                  <div>
                    <p className="text-sm font-medium">{t("scheduleLater")}</p>
                    <p className="text-xs text-muted-foreground">{t("scheduleHint")}</p>
                  </div>
                  <Switch checked={schedule} onCheckedChange={setSchedule} disabled={isSubmitting} aria-label={t("scheduleLater")} />
                </div>
                {schedule && (
                  <div className="space-y-2">
                    <Label htmlFor={`publish-when-${clip.id}`}>{t("publishAt")}</Label>
                    <Input
                      id={`publish-when-${clip.id}`}
                      type="datetime-local"
                      disabled={isSubmitting}
                      value={scheduledFor}
                      min={toLocalDateTimeInput(new Date())}
                      onChange={(event) => setScheduledFor(event.target.value)}
                    />
                  </div>
                )}
              </>
            )}
          </div>

          <SheetFooter>
            <Button variant="outline" onClick={() => changeOpen(false)} disabled={isSubmitting}>
              {t("cancel")}
            </Button>
            {activeConnections.length > 0 && (
              <Button onClick={handleSubmit} disabled={isSubmitting || isGeneratingCaption || pendingConnections.length === 0 || !privacyReady}>
                {isSubmitting ? <Loader2 className="size-4 animate-spin" /> : schedule ? <Clock className="size-4" /> : <Send className="size-4" />}
                {isSubmitting ? (schedule ? t("scheduling") : t("publishing")) : schedule ? (pendingConnections.length > 1 ? t("scheduleToAccounts", { count: pendingConnections.length }) : t("schedule")) : (pendingConnections.length > 1 ? t("publishToAccounts", { count: pendingConnections.length }) : t("publishNow"))}
              </Button>
            )}
          </SheetFooter>
        </SheetContent>
      </Sheet>
    </>
  );
}
