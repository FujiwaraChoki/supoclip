"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  AlertCircle,
  CalendarClock,
  ExternalLink,
  Eye,
  Heart,
  MessageCircle,
  RefreshCw,
  RotateCcw,
  Send,
  Share,
  Trash2,
  XCircle,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
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

const STATUS_STYLES: Record<SocialPostStatus, string> = {
  scheduled: "bg-blue-50 text-blue-700 border-blue-200",
  queued: "bg-gray-100 text-gray-700 border-gray-200",
  publishing: "bg-amber-50 text-amber-700 border-amber-200",
  published: "bg-green-50 text-green-700 border-green-200",
  failed: "bg-red-50 text-red-700 border-red-200",
  cancelled: "bg-gray-100 text-gray-500 border-gray-200",
};

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
        alert(await readError(response, "Action failed"));
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
        const metrics = post.metrics;
        const busy = busyPostId === post.id;
        return (
          <div
            key={post.id}
            className="flex flex-col gap-2 rounded-lg border bg-white px-3 py-2 text-sm"
          >
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="outline" className="font-medium">
                {PROVIDER_LABELS[post.provider] ?? post.provider}
              </Badge>
              <span className="text-gray-700 truncate">
                {connectionLabel({
                  display_name: post.account.display_name,
                  username: post.account.username,
                  provider: post.provider,
                })}
              </span>
              <Badge variant="outline" className={STATUS_STYLES[post.status]}>
                {POST_STATUS_LABELS[post.status]}
              </Badge>
              {post.status === "scheduled" && post.scheduled_for && (
                <span className="inline-flex items-center gap-1 text-xs text-gray-500">
                  <CalendarClock className="w-3.5 h-3.5" />
                  {formatDateTime(post.scheduled_for)}
                </span>
              )}
              {post.status === "published" && post.external_url && (
                <a
                  href={post.external_url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 text-xs text-blue-600 hover:underline"
                >
                  Open post
                  <ExternalLink className="w-3.5 h-3.5" />
                </a>
              )}
              <div className="ml-auto flex items-center gap-1">
                {(post.status === "scheduled" || post.status === "queued") && (
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    onClick={() => runPostAction(post, "cancel")}
                    aria-label="Cancel post"
                  >
                    <XCircle className="w-4 h-4" />
                  </Button>
                )}
                {(post.status === "failed" || post.status === "cancelled") && (
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    onClick={() => runPostAction(post, "retry")}
                    aria-label="Retry post"
                  >
                    <RotateCcw className="w-4 h-4" />
                  </Button>
                )}
                {post.status === "published" && post.external_post_id && (
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    onClick={() => runPostAction(post, "refresh-metrics")}
                    aria-label="Refresh metrics"
                  >
                    <RefreshCw className={`w-4 h-4 ${busy ? "animate-spin" : ""}`} />
                  </Button>
                )}
                {(post.status === "published" ||
                  post.status === "failed" ||
                  post.status === "cancelled" ||
                  post.status === "scheduled") && (
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    className="text-red-600 hover:text-red-700 hover:bg-red-50"
                    onClick={() => runPostAction(post, "delete")}
                    aria-label="Remove post record"
                  >
                    <Trash2 className="w-4 h-4" />
                  </Button>
                )}
              </div>
            </div>
            {post.status === "published" && (
              <div className="flex flex-wrap items-center gap-4 text-xs text-gray-600">
                <span className="inline-flex items-center gap-1">
                  <Eye className="w-3.5 h-3.5" /> {formatMetric(metrics?.views)}
                </span>
                <span className="inline-flex items-center gap-1">
                  <Heart className="w-3.5 h-3.5" /> {formatMetric(metrics?.likes)}
                </span>
                <span className="inline-flex items-center gap-1">
                  <MessageCircle className="w-3.5 h-3.5" /> {formatMetric(metrics?.comments)}
                </span>
                <span className="inline-flex items-center gap-1">
                  <Share className="w-3.5 h-3.5" /> {formatMetric(metrics?.shares)}
                </span>
                <span className="text-gray-400">
                  {post.last_metrics_at
                    ? `Updated ${formatDateTime(post.last_metrics_at)}`
                    : "Metrics arrive within an hour"}
                </span>
              </div>
            )}
            {post.error_message && post.status !== "published" && (
              <p className="text-xs text-red-600">{post.error_message}</p>
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
  const [privacy, setPrivacy] = useState<SocialPrivacyLevel>("public");
  const [schedule, setSchedule] = useState(false);
  const [scheduledFor, setScheduledFor] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const activeConnections = useMemo(
    () => connections.filter((connection) => connection.status !== "revoked"),
    [connections],
  );
  const selectedConnection = activeConnections.find((connection) => connection.id === accountId);
  const selectedProvider = providers.find(
    (provider) => provider.provider === selectedConnection?.provider,
  );
  const privacyOptions = useMemo<SocialPrivacyLevel[]>(
    () => selectedProvider?.supported_privacy_levels ?? ["public", "unlisted", "private"],
    [selectedProvider],
  );
  const anyProviderConfigured = providers.some((provider) => provider.configured);

  useEffect(() => {
    if (!open) return;
    setError(null);
    setTitle(clip.hook_title || `Clip ${clip.clip_order}`);
    setCaption(clip.text || "");
    setHashtags("");
    setSchedule(false);
    setScheduledFor(toLocalDateTimeInput(new Date(Date.now() + 60 * 60 * 1000)));
    if (!accountId && activeConnections[0]) {
      setAccountId(activeConnections[0].id);
    }
  }, [open, clip.hook_title, clip.clip_order, clip.text, accountId, activeConnections]);

  useEffect(() => {
    if (!privacyOptions.includes(privacy)) {
      setPrivacy(privacyOptions[0] ?? "public");
    }
  }, [privacy, privacyOptions]);

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
      <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
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
              <div className="rounded-lg border border-dashed p-4 text-sm text-gray-600">
                {anyProviderConfigured ? (
                  <>
                    No social accounts connected yet.{" "}
                    <Link href="/settings/social" className="text-blue-600 hover:underline">
                      Connect YouTube, TikTok or Instagram
                    </Link>{" "}
                    to publish from here.
                  </>
                ) : (
                  <>
                    Social publishing is not configured on this server. See the{" "}
                    <Link href="/settings/social" className="text-blue-600 hover:underline">
                      Social Accounts
                    </Link>{" "}
                    page for setup instructions.
                  </>
                )}
              </div>
            ) : (
              <>
                <div className="space-y-2">
                  <Label htmlFor={`publish-account-${clip.id}`}>Account</Label>
                  <Select value={accountId} onValueChange={setAccountId}>
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
                    <p className="text-xs text-gray-500">TikTok shows the caption, not the title.</p>
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
                    <p className="text-xs text-gray-500">
                      Until TikTok audits the app, posts can only be private.
                    </p>
                  )}
                </div>

                <div className="flex items-center justify-between rounded-lg border p-3">
                  <div>
                    <p className="text-sm font-medium">Schedule for later</p>
                    <p className="text-xs text-gray-500">Publish at a specific time.</p>
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
                <Send className="w-4 h-4" />
                {isSubmitting ? "Sending…" : schedule ? "Schedule" : "Publish now"}
              </Button>
            )}
          </SheetFooter>
        </SheetContent>
      </Sheet>
    </>
  );
}
