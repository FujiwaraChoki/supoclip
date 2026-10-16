import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { toast } from "sonner";

import { ScheduledPosts } from "./scheduled-posts";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

function scheduledPost(id: string, hoursFromNow: number, title: string) {
  return {
    id,
    task_id: `task-${id}`,
    provider: "youtube",
    status: "scheduled",
    scheduled_for: new Date(Date.now() + hoursFromNow * 3_600_000).toISOString(),
    hook_title: title,
    title: null,
    account: { display_name: "My Channel", username: null, avatar_url: null, status: "active" },
  };
}

it("lists scheduled posts soonest first and links each to its clip", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ posts: [scheduledPost("b", 72, "Later clip"), scheduledPost("a", 0.5, "Soon clip")] })),
    ),
  );
  render(<ScheduledPosts sectionHeadingClassName="" />);
  const links = await screen.findAllByRole("link", { name: /clip$/ });
  expect(links.map((link) => link.textContent)).toEqual(["Soon clip", "Later clip"]);
  expect(links[0]).toHaveAttribute("href", "/tasks/task-a");
  expect(fetch).toHaveBeenCalledWith("/api/social/posts?status=scheduled&limit=200", { cache: "no-store" });
});

it("shows an empty state when nothing is scheduled", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ posts: [] }))));
  render(<ScheduledPosts sectionHeadingClassName="" />);
  expect(await screen.findByText(/Nothing scheduled/)).toBeInTheDocument();
});

it("cancels a scheduled post from its menu", async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ posts: [scheduledPost("a", 2, "Soon clip")] })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ post: { id: "a", status: "cancelled" } })))
    .mockResolvedValue(new Response(JSON.stringify({ posts: [] })));
  vi.stubGlobal("fetch", fetchMock);
  render(<ScheduledPosts sectionHeadingClassName="" />);
  const trigger = await screen.findByRole("button", { name: "Scheduled post actions" });
  fireEvent.keyDown(trigger, { key: "Enter" });
  const menu = await screen.findByRole("menu");
  fireEvent.click(within(menu).getByRole("menuitem", { name: /cancel post/i }));
  await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Scheduled post cancelled"));
  expect(fetchMock).toHaveBeenCalledWith("/api/social/posts/a/cancel", { method: "POST" });
  expect(await screen.findByText(/Nothing scheduled/)).toBeInTheDocument();
});
