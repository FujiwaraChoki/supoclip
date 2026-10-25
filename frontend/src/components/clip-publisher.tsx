"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";
import {
  AlertCircle,
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

// Each platform starts at its own default (TikTok: private, since unaudited
// apps cannot post publicly).
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
  const [accountId, setAccountId] = useState<string>("");
  const [title, setTitle] = useState("");
  const [caption, setCaption] = useState("");
  const [hashtags, setHashtags] = useState("");
  const [privacy, setPrivacy] = useState<SocialPrivacyLevel>("private");
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
  const selectedConnection = activeConnections.find((connection) => connection.id === accountId);
  const privacyOptions = privacyOptionsFor(providerForAccount(accountId));
  const anyProviderConfigured = providers.some((provider) => provider.configured);

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
    if (!value) {
      captionRequest.current++;
      setIsGeneratingCaption(false);
    }
    setOpen(value);
  };

  // The form resets only when the sheet opens, so connections refreshing in
  // the background never wipe what the user is typing.
  const openSheet = () => {
    const nextAccountId = activeConnections.some((connection) => connection.id === accountId)
      ? accountId
      : (activeConnections[0]?.id ?? "");
    setError(null);
    setTitle(clip.hook_title || t("clipFallback", { order: clip.clip_order }));
    setCaption(clip.text || "");
    setHashtags("");
    setSchedule(false);
    setScheduledFor(toLocalDateTimeInput(new Date(Date.now() + 60 * 60 * 1000)));
    setAccountId(nextAccountId);
    setPrivacy(defaultPrivacyFor(providerForAccount(nextAccountId)));
    setOpen(true);
    void generateCaption(nextAccountId);
  };

  const selectAccount = (id: string) => {
    captionRequest.current++;
    setIsGeneratingCaption(false);
    setAccountId(id);
    setPrivacy(defaultPrivacyFor(providerForAccount(id)));
  };

  // Without a configured platform there is nothing to publish to.
  if (!anyProviderConfigured) return null;

  const handleSubmit = async () => {
    if (!accountId) {
      setError(t("chooseAccount"));
      return;
    }
    setIsSubmitting(true);
    setError(null);
    try {
      const body: Record<string, unknown> = {
        task_id: taskId,
        clip_id: clip.id,
        social_account_id: accountId,
        title: title.trim(),
        caption,
        hashtags: parseHashtagInput(hashtags),
        privacy_level: privacy,
      };
      if (schedule && scheduledFor) {
        const when = new Date(scheduledFor);
        if (Number.isNaN(when.getTime())) {
          throw new Error(t("invalidDate"));
        }
        body.scheduled_for = when.toISOString();
      }
      const response = await fetch("/api/social/posts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        throw new Error(await readError(response, t("publishFailed")));
      }
      const { post } = (await response.json().catch(() => ({}))) as { post?: SocialPost };
      const platform = PROVIDER_LABELS[selectedConnection?.provider ?? "youtube"];
      if (body.scheduled_for) {
        toast.success(t("toasts.scheduled", { when: formatDateTime(String(body.scheduled_for)) }), {
          description: t("toasts.scheduledDetail", { platform, account: selectedConnection ? connectionLabel(selectedConnection) : "" }),
        });
      } else if (post?.id) {
        toast.loading(t("toasts.publishing", { platform }), {
          id: postToastId(post.id),
          description: t("toasts.keepWorking"),
        });
      }
      setOpen(false);
      await onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("publishFailed"));
    } finally {
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
        <SheetContent className="w-full sm:max-w-lg overflow-y-auto">
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
                <div className="space-y-2">
                  <Label htmlFor={`publish-account-${clip.id}`}>{t("account")}</Label>
                  <Select value={accountId} onValueChange={selectAccount}>
                    <SelectTrigger id={`publish-account-${clip.id}`} className="w-full">
                      <SelectValue placeholder={t("chooseAccountPlaceholder")} />
                    </SelectTrigger>
                    <SelectContent>
                      {activeConnections.map((connection) => (
                        <SelectItem
                          key={connection.id}
                          value={connection.id}
                          disabled={connection.status === "reauth_required"}
                        >
                          <SocialProviderIcon provider={connection.provider} />
                          {PROVIDER_LABELS[connection.provider]} · {connectionLabel(connection)}
                          {connection.status === "reauth_required" ? t("reconnectRequired") : ""}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-2">
                  <Label htmlFor={`publish-title-${clip.id}`}>{t("title")}</Label>
                  <Input
                    id={`publish-title-${clip.id}`}
                    value={title}
                    maxLength={selectedConnection?.provider === "youtube" ? 100 : 255}
                    onChange={(event) => setTitle(event.target.value)}
                  />
                  {selectedConnection?.provider === "tiktok" && (
                    <p className="text-xs text-muted-foreground">{t("tiktokTitle")}</p>
                  )}
                </div>

                <div className="space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <Label htmlFor={`publish-caption-${clip.id}`}>{t("caption")}</Label>
                    <Button type="button" size="sm" variant="ghost" disabled={isGeneratingCaption || isSubmitting} onClick={() => void generateCaption(accountId)}>
                      {isGeneratingCaption ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
                      {isGeneratingCaption ? t("generatingCaption") : t("generateCaption")}
                    </Button>
                  </div>
                  <Textarea
                    id={`publish-caption-${clip.id}`}
                    value={caption}
                    rows={5}
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
                    placeholder={t("hashtagsPlaceholder")}
                    onChange={(event) => setHashtags(event.target.value)}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor={`publish-privacy-${clip.id}`}>{t("visibilityLabel")}</Label>
                  <Select value={privacy} onValueChange={(value) => setPrivacy(value as SocialPrivacyLevel)}>
                    <SelectTrigger id={`publish-privacy-${clip.id}`} className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {privacyOptions.map((option) => (
                        <SelectItem key={option} value={option}>
                          {t(`privacy.${option}`)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {selectedConnection?.provider === "tiktok" && (
                    <p className="text-xs text-muted-foreground">
                      {t("tiktokPrivate")}
                    </p>
                  )}
                </div>

                <div className="flex items-center justify-between rounded-lg border p-3">
                  <div>
                    <p className="text-sm font-medium">{t("scheduleLater")}</p>
                    <p className="text-xs text-muted-foreground">{t("scheduleHint")}</p>
                  </div>
                  <Switch checked={schedule} onCheckedChange={setSchedule} />
                </div>
                {schedule && (
                  <div className="space-y-2">
                    <Label htmlFor={`publish-when-${clip.id}`}>{t("publishAt")}</Label>
                    <Input
                      id={`publish-when-${clip.id}`}
                      type="datetime-local"
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
              <Button onClick={handleSubmit} disabled={isSubmitting || isGeneratingCaption || !accountId}>
                {isSubmitting ? <Loader2 className="size-4 animate-spin" /> : schedule ? <Clock className="size-4" /> : <Send className="size-4" />}
                {isSubmitting ? (schedule ? t("scheduling") : t("publishing")) : schedule ? t("schedule") : t("publishNow")}
              </Button>
            )}
          </SheetFooter>
        </SheetContent>
      </Sheet>
    </>
  );
}
