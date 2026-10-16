import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { toast } from "sonner";

import { ClipPostList, ClipPublishButton } from "./clip-publisher";
import type { SocialConnection, SocialPost, SocialProviderStatus } from "@/lib/social";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn(), loading: vi.fn() } }));

const clip = { id: "clip-1", clip_order: 1, hook_title: "Hook title", text: "Transcript" };

function provider(overrides: Partial<SocialProviderStatus> = {}): SocialProviderStatus {
  return {
    provider: "youtube",
    display_name: "YouTube Shorts",
    configured: true,
    supported_privacy_levels: ["public", "unlisted", "private"],
    default_privacy_level: "public",
    setup_hint: null,
    ...overrides,
  };
}

function connection(overrides: Partial<SocialConnection> = {}): SocialConnection {
  return {
    id: "acc-1",
    provider: "youtube",
    external_account_id: "chan-1",
    display_name: "My Channel",
    username: null,
    avatar_url: null,
    token_expires_at: null,
    status: "active",
    last_error: null,
    created_at: null,
    ...overrides,
  };
}

beforeEach(() => {
  vi.resetAllMocks();
});

it("hides the publish button when no platform is configured", () => {
  render(
    <ClipPublishButton
      taskId="task-1"
      clip={clip}
      connections={[]}
      providers={[provider({ configured: false })]}
      onChanged={vi.fn()}
    />,
  );
  expect(screen.queryByRole("button", { name: /publish/i })).toBeNull();
});

it("keeps the form when connections refresh while the sheet is open", async () => {
  const providers = [provider()];
  const { rerender } = render(
    <ClipPublishButton
      taskId="task-1"
      clip={clip}
      connections={[connection()]}
      providers={providers}
      onChanged={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: /publish/i }));
  const title = await screen.findByLabelText("Title");
  expect(title).toHaveValue("Hook title");
  fireEvent.change(title, { target: { value: "My edited title" } });

  // A background refresh hands down new connection objects.
  rerender(
    <ClipPublishButton
      taskId="task-1"
      clip={clip}
      connections={[connection(), connection({ id: "acc-2", display_name: "Second" })]}
      providers={[...providers]}
      onChanged={vi.fn()}
    />,
  );
  expect(screen.getByLabelText("Title")).toHaveValue("My edited title");
});

it("starts TikTok posts as private", async () => {
  render(
    <ClipPublishButton
      taskId="task-1"
      clip={clip}
      connections={[connection({ provider: "tiktok" })]}
      providers={[
        provider({
          provider: "tiktok",
          display_name: "TikTok",
          supported_privacy_levels: ["public", "private"],
          default_privacy_level: "private",
        }),
      ]}
      onChanged={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: /publish/i }));
  expect(await screen.findByLabelText("Visibility")).toHaveTextContent("Private (only me)");
});

it("reports failed post actions with a toast", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "This post is already being retried." }), { status: 400 }),
    ),
  );
  const onChanged = vi.fn();
  const post = {
    id: "post-1",
    provider: "youtube",
    status: "failed",
    error_message: "Upload outcome unknown; check your account before retrying.",
    account: { display_name: "My Channel", username: null, avatar_url: null, status: "active" },
    metrics: null,
  } as unknown as SocialPost;
  render(<ClipPostList posts={[post]} onChanged={onChanged} />);
  expect(screen.getByText(/check your account before retrying/)).toBeInTheDocument();
  fireEvent.keyDown(screen.getByRole("button", { name: "Post actions" }), { key: "Enter" });
  fireEvent.click(await screen.findByRole("menuitem", { name: /try again/i }));
  await waitFor(() => expect(toast.error).toHaveBeenCalledWith("This post is already being retried."));
  expect(fetch).toHaveBeenCalledWith("/api/social/posts/post-1/retry", { method: "POST" });
  expect(onChanged).toHaveBeenCalled();
});

it("shows a spinner while a post is uploading and offers no actions", () => {
  const post = {
    id: "post-2",
    provider: "tiktok",
    status: "publishing",
    error_message: null,
    account: { display_name: "My TikTok", username: null, avatar_url: null, status: "active" },
    metrics: null,
  } as unknown as SocialPost;
  render(<ClipPostList posts={[post]} onChanged={vi.fn()} />);
  expect(screen.getByText("Uploading…")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Post actions" })).toBeNull();
});
