import { QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SocialPost } from "@/lib/social";
import {
  fetchTaskDetail,
  socialPostsPollInterval,
  SOCIAL_POSTS_IN_FLIGHT_POLL_MS,
  SOCIAL_POSTS_SCHEDULED_POLL_MS,
  taskDetailKeys,
} from "./task-detail";

const post = (status: string) => ({ id: status, status }) as unknown as SocialPost;

describe("socialPostsPollInterval", () => {
  it("polls fast while an upload is in flight, slowly while one is scheduled, and not at all otherwise", () => {
    expect(socialPostsPollInterval([post("scheduled"), post("publishing")])).toBe(SOCIAL_POSTS_IN_FLIGHT_POLL_MS);
    expect(socialPostsPollInterval([post("published"), post("scheduled")])).toBe(SOCIAL_POSTS_SCHEDULED_POLL_MS);
    expect(socialPostsPollInterval([post("published"), post("failed")])).toBe(false);
    expect(socialPostsPollInterval(undefined)).toBe(false);
  });
});

describe("fetchTaskDetail while processing", () => {
  afterEach(() => { vi.unstubAllGlobals(); });

  function serveTask(progress: number, message: string) {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) =>
      String(input).endsWith("/clips")
        ? Response.json({ clips: [] })
        : Response.json({ id: "task-1", status: "processing", progress, progress_message: message })));
  }

  function cached(progress: number, message: string, status = "processing") {
    const queryClient = new QueryClient();
    queryClient.setQueryData(taskDetailKeys.detail("task-1"), {
      task: { id: "task-1", status, progress, progress_message: message },
      clips: [],
    });
    return queryClient;
  }

  const load = (queryClient: QueryClient) =>
    fetchTaskDetail<{ id: string; status: string; progress?: number; progress_message?: string }, { id: string; clip_order: number }>({
      taskId: "task-1", queryClient, notFoundRetries: 0, errorText: () => "failed",
    });

  it("keeps newer SSE progress over an older row", async () => {
    serveTask(35, "Rendering clip 1/5");
    const { task } = await load(cached(65, "Rendering clip 3/5"));
    expect(task).toMatchObject({ progress: 65, progress_message: "Rendering clip 3/5" });
  });

  it("takes the row when it is ahead, or when the cached task was not processing", async () => {
    serveTask(80, "Rendering clip 4/5");
    expect((await load(cached(65, "Rendering clip 3/5"))).task).toMatchObject({ progress: 80, progress_message: "Rendering clip 4/5" });
    serveTask(10, "Downloading");
    expect((await load(cached(100, "Done", "cancelled"))).task).toMatchObject({ progress: 10, progress_message: "Downloading" });
  });
});
