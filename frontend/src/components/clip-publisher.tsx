"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
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
  PRIVACY_LABELS,
  PROVIDER_LABELS,
  POST_STATUS_LABELS,
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

function metricsSummary(post: SocialPost): string | null {
  if (!post.last_metrics_at || !post.metrics) return null;
  const { views, likes, comments } = post.metrics;
  const count = (value: number | null | undefined, noun: string) =>
    `${formatMetric(value)} ${noun}${value === 1 ? "" : "s"}`;
  return [count(views, "view"), count(likes, "like"), count(comments, "comment")].join(" · ");
}

function PostStatus({ post }: { post: SocialPost }) {
  switch (post.status) {
    case "queued":
    case "publishing":
      return (
        <span className="inline-flex items-center gap-1.5 text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin" />
          {post.status === "queued" ? "Starting upload…" : "Uploading…"}
        </span>
      );
    case "scheduled":
      return (
        <span className="inline-flex items-center gap-1.5 text-muted-foreground">
          <Clock className="size-3.5" />
          {post.scheduled_for ? formatDateTime(post.scheduled_for) : "Scheduled"}
        </span>
      );
    case "published":
      return (
        <span className="text-muted-foreground">
          {post.published_at ? `Published ${timeAgo(post.published_at)}` : "Published"}
        </span>
      );
    case "failed":
      return <span className="text-destructive">Failed</span>;
    default:
      return <span className="text-muted-foreground">{POST_STATUS_LABELS[post.status]}</span>;
  }
}

/**
 * Toasts when a post the user is watching changes state, so publishing never
 * "just happens" silently. Statuses seen on first load are only recorded.
 */
export function useSocialPostToasts(posts: SocialPost[]) {
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
        toast.success(`Published to ${platform}`, {
          id,
          description: post.privacy_level === "public" ? undefined : `Visibility: ${PRIVACY_LABELS[post.privacy_level]}`,
          action: post.external_url
            ? { label: "Open", onClick: () => window.open(post.external_url!, "_blank", "noopener") }
            : undefined,
        });
      } else if (post.status === "failed") {
        toast.error(`Couldn't publish to ${platform}`, { id, description: post.error_message ?? undefined });
      } else if (ACTIVE_POST_STATUSES.includes(post.status)) {
        toast.loading(`Publishing to ${platform}…`, { id });
      }
    }
  }, [posts]);
}

function postToastId(postId: string) {
  return `social-post-${postId}`;
}

export function ClipPostList({ posts, onChanged }: ClipPostListProps) {
  const [busyPostId, setBusyPostId] = useState<string | null>(null);

  const runPostAction = async (post: SocialPost, action: "cancel" | "retry" | "refresh-metrics" | "delete") => {
    setBusyPostId(post.id);
    try {
      const response = await fetch(
        action === "delete" ? `/api/social/posts/${post.id}` : `/api/social/posts/${post.id}/${action}`,
        { method: action === "delete" ? "DELETE" : "POST" },
      );
      if (!response.ok) {
        toast.error(await readError(response, "Action failed"));
      } else if (action === "retry") {
        toast.loading(`Publishing to ${PROVIDER_LABELS[post.provider]}…`, { id: postToastId(post.id) });
      } else if (action === "cancel") {
        toast.success("Post cancelled");
      } else if (action === "refresh-metrics") {
        toast.success("Metrics updated");
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
        const metrics = metricsSummary(post);
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
                <a href={post.external_url} target="_blank" rel="noreferrer" aria-label="Open post">
                  <ExternalLink className="size-4" />
                </a>
              </Button>
            )}
            {hasMenu && (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button size="icon" variant="ghost" className="size-8 text-muted-foreground" disabled={busy} aria-label="Post actions">
                    {busy ? <Loader2 className="size-4 animate-spin" /> : <MoreHorizontal className="size-4" />}
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  {canRefresh && (
                    <DropdownMenuItem onSelect={() => runPostAction(post, "refresh-metrics")}>
                      <RefreshCw /> Refresh metrics
                    </DropdownMenuItem>
                  )}
                  {canRetry && (
                    <DropdownMenuItem onSelect={() => runPostAction(post, "retry")}>
                      <RotateCcw /> Try again
                    </DropdownMenuItem>
                  )}
                  {canCancel && (
                    <DropdownMenuItem onSelect={() => runPostAction(post, "cancel")}>
                      <XCircle /> Cancel post
                    </DropdownMenuItem>
                  )}
                  {canRemove && (canRefresh || canRetry || canCancel) && <DropdownMenuSeparator />}
                  {canRemove && (
                    <DropdownMenuItem variant="destructive" onSelect={() => runPostAction(post, "delete")}>
                      <Trash2 /> Remove from list
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
  const [open, setOpen] = useState(false);
  const [accountId, setAccountId] = useState<string>("");
  const [title, setTitle] = useState("");
  const [caption, setCaption] = useState("");
  const [hashtags, setHashtags] = useState("");
  const [privacy, setPrivacy] = useState<SocialPrivacyLevel>("private");
  const [schedule, setSchedule] = useState(false);
  const [scheduledFor, setScheduledFor] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const activeConnections = useMemo(
    () => connections.filter((connection) => connection.status !== "revoked"),
    [connections],
  );
  const providerForAccount = (id: string) =>
    providers.find(
      (provider) =>
        provider.provider === activeConnections.find((connection) => connection.id === id)?.provider,
    );
  const selectedConnection = activeConnections.find((connection) => connection.id === accountId);
  const privacyOptions = privacyOptionsFor(providerForAccount(accountId));
  const anyProviderConfigured = providers.some((provider) => provider.configured);

  // The form resets only when the sheet opens, so connections refreshing in
  // the background never wipe what the user is typing.
  const openSheet = () => {
    const nextAccountId = activeConnections.some((connection) => connection.id === accountId)
      ? accountId
      : (activeConnections[0]?.id ?? "");
    setError(null);
    setTitle(clip.hook_title || `Clip ${clip.clip_order}`);
    setCaption(clip.text || "");
    setHashtags("");
    setSchedule(false);
    setScheduledFor(toLocalDateTimeInput(new Date(Date.now() + 60 * 60 * 1000)));
    setAccountId(nextAccountId);
    setPrivacy(defaultPrivacyFor(providerForAccount(nextAccountId)));
    setOpen(true);
  };

  const selectAccount = (id: string) => {
    setAccountId(id);
    setPrivacy(defaultPrivacyFor(providerForAccount(id)));
  };

  // Without a configured platform there is nothing to publish to.
  if (!anyProviderConfigured) return null;

  const handleSubmit = async () => {
    if (!accountId) {
      setError("Choose an account to publish to.");
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
          throw new Error("Pick a valid date and time.");
        }
        body.scheduled_for = when.toISOString();
      }
      const response = await fetch("/api/social/posts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        throw new Error(await readError(response, "Failed to publish clip"));
      }
      const { post } = (await response.json().catch(() => ({}))) as { post?: SocialPost };
      const platform = PROVIDER_LABELS[selectedConnection?.provider ?? "youtube"];
      if (body.scheduled_for) {
        toast.success(`Scheduled for ${formatDateTime(String(body.scheduled_for))}`, {
          description: `${platform} · ${selectedConnection ? connectionLabel(selectedConnection) : ""}`,
        });
      } else if (post?.id) {
        toast.loading(`Publishing to ${platform}…`, {
          id: postToastId(post.id),
          description: "You can keep working. We'll tell you when it's live.",
        });
      }
      setOpen(false);
      await onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to publish clip");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <>
      <Button size="sm" variant="outline" onClick={openSheet}>
        <Send className="w-4 h-4" />
        Publish
      </Button>

      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent className="w-full sm:max-w-lg overflow-y-auto">
          <SheetHeader>
            <SheetTitle>Publish clip {clip.clip_order}</SheetTitle>
            <SheetDescription>
              Post this clip straight to a connected account, or schedule it for later.
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
                No social accounts connected yet.{" "}
                <Link href="/settings/social" className="font-medium text-foreground underline underline-offset-4">
                  Connect YouTube, TikTok or Instagram
                </Link>{" "}
                to publish from here.
              </div>
            ) : (
              <>
                <div className="space-y-2">
                  <Label htmlFor={`publish-account-${clip.id}`}>Account</Label>
                  <Select value={accountId} onValueChange={selectAccount}>
                    <SelectTrigger id={`publish-account-${clip.id}`} className="w-full">
                      <SelectValue placeholder="Choose an account" />
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
                          {connection.status === "reauth_required" ? " (reconnect required)" : ""}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-2">
                  <Label htmlFor={`publish-title-${clip.id}`}>Title</Label>
                  <Input
                    id={`publish-title-${clip.id}`}
                    value={title}
                    maxLength={selectedConnection?.provider === "youtube" ? 100 : 255}
                    onChange={(event) => setTitle(event.target.value)}
                  />
                  {selectedConnection?.provider === "tiktok" && (
                    <p className="text-xs text-muted-foreground">TikTok shows the caption, not the title.</p>
                  )}
                </div>

                <div className="space-y-2">
                  <Label htmlFor={`publish-caption-${clip.id}`}>Caption</Label>
                  <Textarea
                    id={`publish-caption-${clip.id}`}
                    value={caption}
                    rows={5}
                    maxLength={2200}
                    onChange={(event) => setCaption(event.target.value)}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor={`publish-hashtags-${clip.id}`}>Hashtags</Label>
                  <Input
                    id={`publish-hashtags-${clip.id}`}
                    value={hashtags}
                    placeholder="podcast, clips, shorts"
                    onChange={(event) => setHashtags(event.target.value)}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor={`publish-privacy-${clip.id}`}>Visibility</Label>
                  <Select value={privacy} onValueChange={(value) => setPrivacy(value as SocialPrivacyLevel)}>
                    <SelectTrigger id={`publish-privacy-${clip.id}`} className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {privacyOptions.map((option) => (
                        <SelectItem key={option} value={option}>
                          {PRIVACY_LABELS[option]}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {selectedConnection?.provider === "tiktok" && (
                    <p className="text-xs text-muted-foreground">
                      Until TikTok audits the app, posts can only be private.
                    </p>
                  )}
                </div>

                <div className="flex items-center justify-between rounded-lg border p-3">
                  <div>
                    <p className="text-sm font-medium">Schedule for later</p>
                    <p className="text-xs text-muted-foreground">Publish at a specific time.</p>
                  </div>
                  <Switch checked={schedule} onCheckedChange={setSchedule} />
                </div>
                {schedule && (
                  <div className="space-y-2">
                    <Label htmlFor={`publish-when-${clip.id}`}>Publish at</Label>
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
            <Button variant="outline" onClick={() => setOpen(false)} disabled={isSubmitting}>
              Cancel
            </Button>
            {activeConnections.length > 0 && (
              <Button onClick={handleSubmit} disabled={isSubmitting || !accountId}>
                {isSubmitting ? <Loader2 className="size-4 animate-spin" /> : schedule ? <Clock className="size-4" /> : <Send className="size-4" />}
                {isSubmitting ? (schedule ? "Scheduling…" : "Publishing…") : schedule ? "Schedule" : "Publish now"}
              </Button>
            )}
          </SheetFooter>
        </SheetContent>
      </Sheet>
    </>
  );
}
