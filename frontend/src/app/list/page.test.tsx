import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import ListPage from "./page";

vi.mock("@/lib/auth-client", () => ({
  useSession: () => ({ data: { user: { id: "user-1" } }, isPending: false }),
}));

const tasks = [
  {
    id: "old-cut",
    user_id: "user-1",
    source_id: "source-old",
    source_title: "Podcast",
    source_type: "youtube",
    source_url: "https://youtu.be/dQw4w9WgXcQ",
    status: "completed",
    clips_count: 3,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
  },
  {
    id: "standup",
    user_id: "user-1",
    source_id: "source-standup",
    source_title: "Weekly standup",
    source_type: "youtube",
    source_url: "https://www.youtube.com/watch?v=abcdefghijk",
    status: "completed",
    clips_count: 1,
    created_at: "2026-10-03T00:00:00Z",
    updated_at: "2026-10-03T00:00:00Z",
  },
  {
    id: "new-cut",
    user_id: "user-1",
    source_id: "source-new",
    source_title: "Podcast (recut)",
    source_type: "youtube",
    source_url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    status: "completed",
    clips_count: 4,
    created_at: "2026-10-04T00:00:00Z",
    updated_at: "2026-10-04T00:00:00Z",
  },
];

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ tasks })));
});

it("groups repeat runs of the same original video", async () => {
  render(<ListPage />);

  expect(await screen.findByRole("link", { name: /Podcast \(recut\)/ })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { level: 2, name: "Podcast (recut)" })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("tab", { name: "By video" }));

  const headings = screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent);
  expect(headings).toEqual(["Podcast", "Weekly standup"]);
  expect(screen.queryByText("2 generations")).not.toBeInTheDocument();

  const links = screen.getAllByRole("link")
    .map((link) => link.getAttribute("href"))
    .filter((href) => href?.startsWith("/tasks/"));
  expect(links).toEqual(["/tasks/new-cut", "/tasks/old-cut", "/tasks/standup"]);
});

it("shows an error when the first load fails", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 500 })));
  render(<ListPage />);

  expect(await screen.findByText("Failed to fetch tasks: 500")).toBeInTheDocument();
});

it("polls while a generation is processing and keeps the list when a refresh fails", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    const processing = [{ ...tasks[0], status: "processing" }];
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ tasks: processing }))
      .mockResolvedValueOnce(new Response(null, { status: 503 }))
      .mockResolvedValue(Response.json({ tasks: [tasks[0]] }));
    vi.stubGlobal("fetch", fetchMock);
    render(<ListPage />);

    expect(await screen.findByRole("link", { name: /Podcast/ })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(5000);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("link", { name: /Podcast/ })).toBeInTheDocument();
    expect(screen.queryByText(/Failed to fetch tasks/)).not.toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(5000);
    expect(fetchMock).toHaveBeenCalledTimes(3);

    // Nothing is processing any more, so polling stops.
    await vi.advanceTimersByTimeAsync(15000);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  } finally {
    vi.useRealTimers();
  }
});
