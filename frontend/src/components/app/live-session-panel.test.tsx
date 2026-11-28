import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LiveSessionPanel, type LiveSessionInfo } from "./live-session-panel";

const SESSION: LiveSessionInfo = {
  id: "00000000-0000-4000-8000-000000000001",
  platform: "twitch",
  channel: "SomeStreamer",
  title: "Sunday stream",
  status: "active",
  stop_reason: null,
  covered_until_seconds: 7250,
  parts_created: 2,
  next_check_at: null,
  stream_started_at: new Date(Date.now() - 8000 * 1000).toISOString(),
  interval_seconds: 1800,
  parts: [
    { part_number: 1, task_id: "task-1", start_seconds: 1990, end_seconds: 5590, task_status: "completed", clips_count: 6 },
    { part_number: 2, task_id: "task-2", start_seconds: 5500, end_seconds: 7250, task_status: "processing", clips_count: 0 },
  ],
};

describe("LiveSessionPanel", () => {
  it("says the stream is being clipped and links the other parts", () => {
    render(<LiveSessionPanel taskId="task-2" session={SESSION} />);
    const panel = screen.getByTestId("live-session-panel");
    expect(panel).toHaveTextContent("Clipping SomeStreamer live · next part soon");
    const parts = within(panel).getByRole("list", { name: "Parts of this stream" });
    expect(within(parts).getByRole("link", { name: "Part 1" })).toHaveAttribute("href", "/tasks/task-1");
    expect(within(parts).getByText("Part 2")).toHaveAttribute("aria-current", "page");
    expect(within(panel).getByRole("button", { name: "Stop" })).toBeEnabled();
  });

  it("says why a session ended and offers nothing to stop", () => {
    render(<LiveSessionPanel taskId="task-1" session={{ ...SESSION, status: "stopped", stop_reason: "minutes" }} />);
    const panel = screen.getByTestId("live-session-panel");
    expect(panel).toHaveTextContent("Clipped SomeStreamer live · out of minutes this month");
    expect(within(panel).queryByRole("button", { name: "Stop" })).not.toBeInTheDocument();
  });
});
