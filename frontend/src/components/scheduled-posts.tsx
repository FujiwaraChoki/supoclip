"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { ExternalLink, Loader2, MoreHorizontal, XCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { SocialProviderIcon } from "@/components/social-provider-icon";
import { timeAgo } from "@/lib/generations";
import { PROVIDER_LABELS, type SocialPost } from "@/lib/social";

const dayFormat = new Intl.DateTimeFormat(undefined, { weekday: "long", month: "short", day: "numeric" });
const timeFormat = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });

function dayLabel(date: Date): string {
  const today = new Date();
  const startOf = (value: Date) => new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();
  const days = Math.round((startOf(date) - startOf(today)) / 86_400_000);
  if (days === 0) return "Today";
  if (days === 1) return "Tomorrow";
  return dayFormat.format(date);
}

function accountName(post: SocialPost): string {
  return (
    post.account.display_name ||
    (post.account.username ? `@${post.account.username.replace(/^@/, "")}` : null) ||
    PROVIDER_LABELS[post.provider]
  );
}

/** Every upcoming scheduled post across the user's tasks, soonest first. */
export function ScheduledPosts({ sectionHeadingClassName }: { sectionHeadingClassName: string }) {
  const [posts, setPosts] = useState<SocialPost[] | null>(null);
  const [busyPostId, setBusyPostId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const response = await fetch("/api/social/posts?status=scheduled&limit=200", { cache: "no-store" });
      if (!response.ok) return;
      const data = (await response.json()) as { posts?: SocialPost[] };
      setPosts(data.posts ?? []);
    } catch {
      // Keep whatever was last loaded; the next poll retries.
    }
  }, []);

  // Posts leave this list as the worker picks them up, so refresh periodically.
  useEffect(() => {
    void load();
    const interval = window.setInterval(() => void load(), 30_000);
    return () => window.clearInterval(interval);
  }, [load]);

  const groups = useMemo(() => {
    const sorted = [...(posts ?? [])]
      .filter((post) => post.scheduled_for)
      .sort((a, b) => new Date(a.scheduled_for!).getTime() - new Date(b.scheduled_for!).getTime());
    const byDay = new Map<string, SocialPost[]>();
    for (const post of sorted) {
      const label = dayLabel(new Date(post.scheduled_for!));
      byDay.set(label, [...(byDay.get(label) ?? []), post]);
    }
    return [...byDay.entries()];
  }, [posts]);

  const cancel = async (post: SocialPost) => {
    setBusyPostId(post.id);
    try {
      const response = await fetch(`/api/social/posts/${post.id}/cancel`, { method: "POST" });
      if (response.ok) {
        toast.success("Scheduled post cancelled");
      } else {
        const data = await response.json().catch(() => ({}));
        toast.error(typeof data?.detail === "string" ? data.detail : "Could not cancel the post");
      }
      await load();
    } finally {
      setBusyPostId(null);
    }
  };

  return (
    <section className="mb-10">
      <h2 className={sectionHeadingClassName}>Scheduled</h2>
      {posts === null ? (
        <Skeleton className="h-16 w-full" />
      ) : groups.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          Nothing scheduled. Open a clip, choose Publish and turn on Schedule for later.
        </p>
      ) : (
        <div className="space-y-5">
          {groups.map(([label, dayPosts]) => (
            <div key={label}>
              <p className="mb-2 text-xs font-medium text-muted-foreground">{label}</p>
              <div className="divide-y rounded-xl border bg-background">
                {dayPosts.map((post) => {
                  const when = new Date(post.scheduled_for!);
                  const title = post.hook_title || post.title || "Untitled clip";
                  return (
                    <div key={post.id} className="flex items-center gap-3 px-4 py-3 text-sm">
                      <span className="w-16 shrink-0 tabular-nums text-muted-foreground">{timeFormat.format(when)}</span>
                      <SocialProviderIcon provider={post.provider} className="text-muted-foreground" />
                      <div className="min-w-0 flex-1">
                        {post.task_id ? (
                          <Link href={`/tasks/${post.task_id}`} className="block truncate font-medium hover:underline underline-offset-4">
                            {title}
                          </Link>
                        ) : (
                          <span className="block truncate font-medium">{title}</span>
                        )}
                        <p className="truncate text-xs text-muted-foreground">
                          {accountName(post)} · {timeAgo(post.scheduled_for!)}
                        </p>
                      </div>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button
                            size="icon"
                            variant="ghost"
                            className="size-8 text-muted-foreground"
                            disabled={busyPostId === post.id}
                            aria-label="Scheduled post actions"
                          >
                            {busyPostId === post.id ? <Loader2 className="size-4 animate-spin" /> : <MoreHorizontal className="size-4" />}
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          {post.task_id && (
                            <DropdownMenuItem asChild>
                              <Link href={`/tasks/${post.task_id}`}>
                                <ExternalLink /> Open clip
                              </Link>
                            </DropdownMenuItem>
                          )}
                          <DropdownMenuItem variant="destructive" onSelect={() => cancel(post)}>
                            <XCircle /> Cancel post
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
