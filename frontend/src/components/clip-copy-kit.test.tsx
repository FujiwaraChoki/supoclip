import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

import { ClipCopyKitButton, type ClipCopy } from "./clip-copy-kit";

const copyText = vi.fn();
vi.mock("@/lib/copy-text", () => ({ useCopyText: () => copyText }));

const kit: ClipCopy = {
  generated_at: "2026-01-01T00:00:00Z",
  platforms: {
    tiktok: { title: "TikTok title", description: "TikTok desc", hashtags: ["#a", "#b"] },
    youtube_shorts: { title: "Shorts title", description: "Shorts desc", hashtags: ["#c"] },
    instagram_reels: { title: "Reels title", description: "Reels desc", hashtags: ["#d"] },
  },
};

const clip = { id: "clip-1", clip_order: 1, copy: null as ClipCopy | null };
const URL = "/api/tasks/task-1/clips/clip-1/copy";

function mockFetch(response: Response) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
}

function open() {
  fireEvent.click(screen.getByRole("button", { name: /titles & hashtags/i }));
}

beforeEach(() => {
  vi.resetAllMocks();
  mockFetch(new Response(JSON.stringify({ copy: kit })));
});

it("renders stored copy without fetching", async () => {
  render(<ClipCopyKitButton taskId="task-1" clip={{ ...clip, copy: kit }} />);
  open();
  expect(await screen.findByText("TikTok title")).toBeInTheDocument();
  expect(fetch).not.toHaveBeenCalled();
});

it("generates copy with regenerate false", async () => {
  const onChanged = vi.fn();
  render(<ClipCopyKitButton taskId="task-1" clip={clip} onChanged={onChanged} />);
  open();
  fireEvent.click(screen.getByRole("button", { name: "Generate" }));
  expect(await screen.findByText("TikTok title")).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledWith(URL, expect.objectContaining({
    method: "POST",
    body: JSON.stringify({ regenerate: false }),
  }));
  expect(onChanged).toHaveBeenCalled();
});

it("regenerate sends regenerate true", async () => {
  render(<ClipCopyKitButton taskId="task-1" clip={{ ...clip, copy: kit }} />);
  open();
  fireEvent.click(await screen.findByRole("button", { name: "Regenerate" }));
  await waitFor(() => expect(fetch).toHaveBeenCalledWith(URL, expect.objectContaining({
    body: JSON.stringify({ regenerate: true }),
  })));
});

it("shows the detail message on a 503", async () => {
  mockFetch(new Response(JSON.stringify({ detail: "AI is busy" }), { status: 503 }));
  render(<ClipCopyKitButton taskId="task-1" clip={clip} />);
  open();
  fireEvent.click(screen.getByRole("button", { name: "Generate" }));
  expect(await screen.findByText("AI is busy")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Generate" })).toBeEnabled();
});

it("copy all copies title, description and hashtags", async () => {
  render(<ClipCopyKitButton taskId="task-1" clip={{ ...clip, copy: kit }} />);
  open();
  fireEvent.click(await screen.findByRole("button", { name: "Copy all" }));
  expect(copyText).toHaveBeenCalledWith("TikTok title\n\nTikTok desc\n\n#a #b");
});

it("switching tabs shows the other platform and its limit", async () => {
  const user = userEvent.setup();
  render(<ClipCopyKitButton taskId="task-1" clip={{ ...clip, copy: kit }} />);
  open();
  expect(await screen.findByTestId("copykit-counter")).toHaveTextContent("12/100");
  await user.click(screen.getByRole("tab", { name: "Instagram Reels" }));
  expect(screen.getByText("Reels title")).toBeInTheDocument();
  expect(screen.getByTestId("copykit-counter")).toHaveTextContent("11/125");
});
