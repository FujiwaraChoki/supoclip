import type { SocialPost } from "@/lib/social";

/**
 * Social post lists. Everything under `all` is a view of the same posts, so a write (cancel, retry,
 * publish) invalidates `socialPostKeys.all` and every list on screen refreshes.
 */
export const socialPostKeys = {
  all: ["social-posts"] as const,
  scheduled: () => [...socialPostKeys.all, "scheduled"] as const,
  forTask: (taskId: string) => [...socialPostKeys.all, "task", taskId] as const,
};

/** Every upcoming scheduled post across the user's tasks. Throws on failure so the last list stays. */
export async function fetchScheduledPosts(): Promise<SocialPost[]> {
  const response = await fetch("/api/social/posts?status=scheduled&limit=200", { cache: "no-store" });
  if (!response.ok) throw new Error(`Scheduled posts request failed (${response.status})`);
  const data = (await response.json()) as { posts?: SocialPost[] };
  return data.posts ?? [];
}
