"use client";

import { keepPreviousData, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { formatSupportMessage, parseApiError } from "@/lib/api-error";
import type { FontOption } from "@/components/font-select-option";
import { ACTIVE_POST_STATUSES, type SocialConnection, type SocialPost, type SocialProviderStatus } from "@/lib/social";
import { socialPostKeys } from "@/lib/queries/social";
import { generationKeys } from "@/lib/queries/generations";
import type { GenerationSummary } from "@/lib/generations";

/** What the task page reads: the task row and its clips, fetched together. */
export interface TaskDetail<TTask, TClip> {
  task: TTask;
  clips: TClip[];
  /** Which load produced this (see `taskDetailLoadCount`); in-place cache updates keep it. */
  loadNumber?: number;
}

let loadsStarted = 0;
/** How many task loads have started in this tab: a page compares it to tell a fresh load from a cached one. */
export function taskDetailLoadCount() {
  return loadsStarted;
}

export type CaptionTemplateOption = { id: string; name: string; description: string; animation: string };

export const taskDetailKeys = {
  all: (taskId: string) => ["task-detail", taskId] as const,
  detail: (taskId: string) => [...taskDetailKeys.all(taskId), "detail"] as const,
  socialPosts: (taskId: string) => socialPostKeys.forTask(taskId),
};

/** Lists the task page's sheets and publish buttons read. Namespaced so they never clash with another page's shape. */
export const taskPageKeys = {
  fonts: ["task-page", "fonts"] as const,
  captionTemplates: (withFx: boolean) => ["task-page", "caption-templates", withFx ? "fx" : "plain"] as const,
  brollStatus: ["task-page", "broll-status"] as const,
  socialConnections: ["task-page", "social-connections"] as const,
  socialProviders: ["task-page", "social-providers"] as const,
};

/** A just-created task may not be persisted yet: a first load retries a 404 this many times. */
export const TASK_NOT_FOUND_RETRIES = 5;
/** Social post polling: fast while an upload is in flight, slow while one is only scheduled. */
export const SOCIAL_POSTS_IN_FLIGHT_POLL_MS = 4000;
export const SOCIAL_POSTS_SCHEDULED_POLL_MS = 30000;

const NO_POSTS: SocialPost[] = [];

type ClipLike = { id: string; clip_order: number };
type TaskLike = { id?: string; status: string; progress?: number; progress_message?: string };

async function supportError(response: Response, fallback: string) {
  return new Error(formatSupportMessage(await parseApiError(response, fallback)));
}

/**
 * Loads the task and its clips. While the task is processing, clips already on screen (from SSE
 * `clip_ready` events) are kept and merged by id; once it stops, the server's list is the truth
 * (a failed series, recap or trailer has had its clips deleted and its minutes refunded).
 * Progress never goes backwards while processing: SSE can be ahead of the row this request read.
 */
export async function fetchTaskDetail<TTask extends TaskLike, TClip extends ClipLike>({
  taskId,
  queryClient,
  notFoundRetries,
  errorText,
}: {
  taskId: string;
  queryClient: QueryClient;
  notFoundRetries: number;
  errorText: (kind: "task" | "clips", status: number) => string;
}): Promise<TaskDetail<TTask, TClip>> {
  loadsStarted += 1;
  const loadNumber = loadsStarted;
  let taskResponse = await fetch(`/api/tasks/${taskId}`, { cache: "no-store" });
  for (let attempt = 0; taskResponse.status === 404 && attempt < notFoundRetries; attempt += 1) {
    console.log(`Task not found yet, retrying in ${(attempt + 1) * 500}ms... (${attempt + 1}/${notFoundRetries})`);
    await new Promise((resolve) => setTimeout(resolve, (attempt + 1) * 500));
    taskResponse = await fetch(`/api/tasks/${taskId}`, { cache: "no-store" });
  }
  if (!taskResponse.ok) throw await supportError(taskResponse, errorText("task", taskResponse.status));
  const task = (await taskResponse.json()) as TTask;

  // Queued retries can already have completed clips, including after reload.
  const clipsResponse = await fetch(`/api/tasks/${taskId}/clips`, { cache: "no-store" });
  if (!clipsResponse.ok) throw await supportError(clipsResponse, errorText("clips", clipsResponse.status));
  const nextClips = (((await clipsResponse.json()) as { clips?: TClip[] }).clips ?? []) as TClip[];

  if (task.status !== "processing") return { task, clips: nextClips, loadNumber };
  // Read the cache now, not before the requests: clips and progress that arrived over SSE meanwhile stay.
  const cached = queryClient.getQueryData<TaskDetail<TTask, TClip>>(taskDetailKeys.detail(taskId));
  const merged = new Map<string, TClip>();
  for (const clip of cached?.clips ?? []) merged.set(clip.id, clip);
  for (const clip of nextClips) merged.set(clip.id, clip);
  const cachedTask = cached?.task;
  const keepProgress = cachedTask?.status === "processing" && (cachedTask.progress ?? 0) > (task.progress ?? 0);
  return {
    task: keepProgress ? { ...task, progress: cachedTask.progress, progress_message: cachedTask.progress_message } : task,
    clips: Array.from(merged.values()).sort((a, b) => (a.clip_order ?? 0) - (b.clip_order ?? 0)),
    loadNumber,
  };
}

/** The task and its clips. Progress comes over SSE (`useTaskProgress`), which writes into this cache. */
export function useTaskDetail<TTask extends TaskLike, TClip extends ClipLike>(
  taskId: string,
  errorText: (kind: "task" | "clips", status: number) => string,
) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: taskDetailKeys.detail(taskId),
    queryFn: async () => {
      try {
        return await fetchTaskDetail<TTask, TClip>({
          taskId,
          queryClient,
          // Only a first load waits for a task that is still being persisted.
          notFoundRetries: queryClient.getQueryData(taskDetailKeys.detail(taskId)) ? 0 : TASK_NOT_FOUND_RETRIES,
          errorText,
        });
      } catch (err) {
        console.error("Error fetching task data:", err);
        throw err;
      }
    },
    enabled: Boolean(taskId),
    // A failed refresh keeps the last task on screen; the SSE hook reconciles on focus and reconnect.
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
}

/** Update the cached task and clips in place (SSE events, title edits, share toggles, deleted clips). */
export function updateTaskDetail<TTask, TClip>(
  queryClient: QueryClient,
  taskId: string,
  update: (current: TaskDetail<TTask, TClip>) => TaskDetail<TTask, TClip>,
) {
  queryClient.setQueryData<TaskDetail<TTask, TClip>>(taskDetailKeys.detail(taskId), (current) => (current ? update(current) : current));
}

/**
 * After a task is deleted: drop it from the library list now (and refetch that list), so /list never
 * shows it from cache. Its own entries go when the page unmounts (`forgetTaskDetail`), so the page
 * still on screen doesn't reload a task that is gone.
 */
export function dropDeletedTaskFromLists(queryClient: QueryClient, taskId: string, userId: string) {
  queryClient.setQueryData<GenerationSummary[]>(generationKeys.all(userId), (current) => current?.filter((item) => item.id !== taskId));
  void queryClient.invalidateQueries({ queryKey: generationKeys.all(userId) });
}

/** Remove everything cached for one task (its detail and its page's lists). */
export function forgetTaskDetail(queryClient: QueryClient, taskId: string) {
  queryClient.removeQueries({ queryKey: taskDetailKeys.all(taskId) });
}

async function okJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  // Throwing keeps the last good list: these are optional and never block the page.
  if (!response.ok) throw new Error(`${url} failed (${response.status})`);
  return (await response.json()) as T;
}

export function useTaskFonts() {
  return useQuery({
    queryKey: taskPageKeys.fonts,
    queryFn: async () => {
      try {
        return (await okJson<{ fonts?: FontOption[] }>("/api/fonts", { cache: "no-store" })).fonts ?? [];
      } catch (loadError) {
        console.error("Failed to load fonts:", loadError);
        throw loadError;
      }
    },
  });
}

/** Caption templates; animated ones are asked for only while dynamic edits are on. Keeps the last list while switching. */
export function useCaptionTemplates(withFx: boolean) {
  return useQuery({
    queryKey: taskPageKeys.captionTemplates(withFx),
    queryFn: async () => {
      try {
        return (await okJson<{ templates?: CaptionTemplateOption[] }>(withFx ? "/api/caption-templates?include=fx" : "/api/caption-templates")).templates ?? [];
      } catch (error) {
        console.error("Failed to load caption templates:", error);
        throw error;
      }
    },
    placeholderData: keepPreviousData,
  });
}

/** Whether B-roll is configured on this server; any failure reads as "no". */
export function useBrollAvailable() {
  const { data } = useQuery({
    queryKey: taskPageKeys.brollStatus,
    queryFn: async () => {
      try {
        const response = await fetch("/api/broll/status");
        if (!response.ok) return false;
        return ((await response.json()) as { configured?: unknown } | null)?.configured === true;
      } catch {
        return false;
      }
    },
  });
  return data === true;
}

/** Connected accounts and configured platforms for the publish button. Publishing is optional. */
export function useSocialPublishContext(enabled: boolean) {
  const connections = useQuery({
    queryKey: taskPageKeys.socialConnections,
    queryFn: async () => (await okJson<{ connections?: SocialConnection[] }>("/api/social/connections", { cache: "no-store" })).connections ?? [],
    enabled,
  });
  const providers = useQuery({
    queryKey: taskPageKeys.socialProviders,
    queryFn: async () => (await okJson<{ providers?: SocialProviderStatus[] }>("/api/social/providers", { cache: "no-store" })).providers ?? [],
    enabled,
  });
  return { connections: connections.data, providers: providers.data };
}

/** Pause between social post refreshes: fast while one uploads, slow while one waits for its time, off otherwise. */
export function socialPostsPollInterval(posts: SocialPost[] | undefined): number | false {
  if (posts?.some((post) => ACTIVE_POST_STATUSES.includes(post.status))) return SOCIAL_POSTS_IN_FLIGHT_POLL_MS;
  if (posts?.some((post) => post.status === "scheduled")) return SOCIAL_POSTS_SCHEDULED_POLL_MS;
  return false;
}

/** This task's social posts, polled while one is in flight or scheduled. */
export function useTaskSocialPosts(taskId: string, enabled: boolean) {
  const query = useQuery({
    queryKey: taskDetailKeys.socialPosts(taskId),
    queryFn: async () =>
      (await okJson<{ posts?: SocialPost[] }>(`/api/social/posts?task_id=${taskId}`, { cache: "no-store" })).posts ?? NO_POSTS,
    enabled: enabled && Boolean(taskId),
    refetchInterval: (query) => socialPostsPollInterval(query.state.data),
    // An upload's outcome toast should land even while the tab is in the background.
    refetchIntervalInBackground: true,
  });
  const { refetch } = query;
  const reload = useCallback(async () => {
    await refetch();
  }, [refetch]);
  return { posts: query.data ?? NO_POSTS, reload };
}
