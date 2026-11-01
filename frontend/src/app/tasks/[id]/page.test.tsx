import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import TaskPage from "./page";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "task-1" }),
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("@/lib/auth-client", () => ({
  useSession: () => ({ data: { user: { id: "user-1", name: "Ada", email: "ada@example.com" } }, isPending: false }),
}));

const REASON = "No moments scored at least 80% virality. Try a longer video, a different clip length, or lower MIN_VIRALITY_SCORE.";

function mockFetch(progress_message: string) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/api/tasks/task-1")) {
      return Response.json({
        id: "task-1",
        user_id: "user-1",
        status: "error",
        progress: 0,
        progress_message,
        created_at: "2026-01-01T00:00:00Z",
        source: { title: "Video", type: "youtube" },
      });
    }
    if (url.endsWith("/clips")) return Response.json({ clips: [] });
    return Response.json({});
  }));
}

const failedBody = /There was an error processing your video/;

describe("TaskPage failed task", () => {
  beforeEach(() => vi.unstubAllGlobals());

  it("shows the stored failure reason", async () => {
    mockFetch(REASON);
    render(<TooltipProvider><TaskPage /></TooltipProvider>);
    expect(await screen.findByText(REASON)).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent(REASON);
  });

  it("shows only the generic body when there is no reason", async () => {
    mockFetch("");
    render(<TooltipProvider><TaskPage /></TooltipProvider>);
    expect(await screen.findByText(failedBody)).toBeVisible();
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
    expect(screen.queryByText(REASON)).not.toBeInTheDocument();
  });
});
