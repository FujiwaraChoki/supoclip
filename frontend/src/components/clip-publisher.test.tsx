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
  HTMLElement.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async (url: string) => Response.json(url.includes("publishing-options") ? { privacy_levels: ["public", "private"] } : { caption: "An engaging caption" })));
});

it("drafts a social caption when opening the scheduling form", async () => {
  render(<ClipPublishButton taskId="task-1" clip={clip} connections={[connection()]} providers={[provider()]} onChanged={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: /publish/i }));
  await waitFor(() => expect(screen.getByLabelText("Caption")).toHaveValue("An engaging caption"));
  expect(fetch).toHaveBeenCalledWith("/api/social/captions", expect.objectContaining({
    body: JSON.stringify({ task_id: "task-1", clip_id: "clip-1", provider: "youtube" }),
  }));
});

it("preserves manual caption edits while generation is pending", async () => {
  let finish!: (response: Response) => void;
  vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise<Response>((resolve) => { finish = resolve; })));
  render(<ClipPublishButton taskId="task-1" clip={clip} connections={[connection()]} providers={[provider()]} onChanged={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: /publish/i }));
  fireEvent.change(await screen.findByLabelText("Caption"), { target: { value: "My own caption" } });
  finish(new Response(JSON.stringify({ caption: "AI caption" })));
  await waitFor(() => expect(screen.getByRole("button", { name: "Generate caption" })).toBeEnabled());
  expect(screen.getByLabelText("Caption")).toHaveValue("My own caption");
});

it("keeps the original caption editable when generation fails", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Try again" }), { status: 503 })));
  render(<ClipPublishButton taskId="task-1" clip={clip} connections={[connection()]} providers={[provider()]} onChanged={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: /publish/i }));
  expect(await screen.findByText("Try again")).toBeInTheDocument();
  expect(screen.getByLabelText("Caption")).toHaveValue("Transcript");
  expect(screen.getByRole("button", { name: "Publish now" })).toBeEnabled();
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

it("requires a deliberate TikTok visibility choice after loading account permissions", async () => {
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
  await waitFor(() => expect(screen.getByLabelText("Visibility")).toBeEnabled());
  expect(screen.getByLabelText("Visibility")).toHaveTextContent("Choose visibility");
  expect(screen.getByRole("button", { name: "Publish now" })).toBeDisabled();
});

it("excludes an old TikTok connection when only YouTube is enabled", async () => {

  render(
    <ClipPublishButton
      taskId="task-1"
      clip={clip}
      connections={[
        connection({ id: "old-tiktok", provider: "tiktok", display_name: "Disabled account" }),
        connection(),
      ]}
      providers={[
        provider(),
        provider({ provider: "tiktok", display_name: "TikTok", configured: false }),
      ]}
      onChanged={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: /publish/i }));
  expect(await screen.findByRole("checkbox", { name: /My Channel/ })).toBeChecked();
  expect(screen.queryByRole("checkbox", { name: /Disabled account/ })).toBeNull();
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

const tikTokConnection = () => connection({ id: "tt-1", provider: "tiktok", display_name: "My TikTok" });
const tikTokProvider = () => provider({ provider: "tiktok", display_name: "TikTok", supported_privacy_levels: ["public", "private"], default_privacy_level: "private" });

type PostBody = { social_account_id: string; privacy_level: string; title: string; caption: string; scheduled_for?: string };
function mockPublishing(reply?: (body: PostBody, attempt: number) => Response | Promise<Response>) {
  const requests: PostBody[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    if (url === "/api/social/captions") return Response.json({ caption: "A shared caption" });
    if (url.includes("publishing-options")) return Response.json({ privacy_levels: ["public", "private"] });
    const body = JSON.parse(options!.body as string) as PostBody;
    requests.push(body);
    if (reply) return reply(body, requests.filter((item) => item.social_account_id === body.social_account_id).length);
    return Response.json({ post: { id: `post-${body.social_account_id}`, status: body.scheduled_for ? "scheduled" : "queued", scheduled_for: body.scheduled_for } });
  }));
  return requests;
}

async function openMultiPublisher(onChanged = vi.fn()) {
  render(<ClipPublishButton taskId="task-1" clip={clip} connections={[connection(), tikTokConnection()]} providers={[provider(), tikTokProvider()]} onChanged={onChanged} />);
  fireEvent.click(screen.getByRole("button", { name: "Publish" }));
  await waitFor(() => expect(screen.getByLabelText("Caption")).toHaveValue("A shared caption"));
  fireEvent.click(screen.getByRole("checkbox", { name: /TikTok · My TikTok/ }));
  const visibility = screen.getByLabelText("Visibility · TikTok · My TikTok");
  await waitFor(() => expect(visibility).toBeEnabled());
  fireEvent.keyDown(visibility, { key: "Enter" });
  fireEvent.click(await screen.findByRole("option", { name: "Private (only me)" }));
  return onChanged;
}

it("publishes to both selected accounts with independent platform defaults", async () => {
  const requests = mockPublishing();
  const changed = await openMultiPublisher();
  expect(screen.getByRole("checkbox", { name: /YouTube Shorts · My Channel/ })).toBeChecked();
  expect(screen.getByLabelText("Visibility · YouTube Shorts · My Channel")).toHaveTextContent("Public");
  expect(screen.getByLabelText("Visibility · TikTok · My TikTok")).toHaveTextContent("Private (only me)");
  fireEvent.click(screen.getByRole("button", { name: "Publish to 2 accounts" }));
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  expect(requests).toEqual([
    expect.objectContaining({ social_account_id: "acc-1", caption: "A shared caption", privacy_level: "public" }),
    expect.objectContaining({ social_account_id: "tt-1", caption: "A shared caption", privacy_level: "private" }),
  ]);
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("schedules a separate post for every selected account at the same time", async () => {
  const requests = mockPublishing();
  const changed = await openMultiPublisher();
  fireEvent.click(screen.getByRole("switch", { name: "Schedule for later" }));
  const future = new Date(Date.now() + 86400000);
  const local = `${future.getFullYear()}-${String(future.getMonth() + 1).padStart(2, "0")}-${String(future.getDate()).padStart(2, "0")}T12:00`;
  fireEvent.change(screen.getByLabelText("Publish at"), { target: { value: local } });
  fireEvent.click(screen.getByRole("button", { name: "Schedule for 2 accounts" }));
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  expect(requests).toHaveLength(2);
  expect(requests.every((body) => body.scheduled_for === new Date(local).toISOString())).toBe(true);
  expect(toast.success).toHaveBeenCalledTimes(2);
});

it("retries only a rejected account after another account accepts its post", async () => {
  const requests = mockPublishing((body, attempt) => body.social_account_id === "tt-1" && attempt === 1
    ? Response.json({ detail: "Reconnect TikTok first" }, { status: 400 })
    : Response.json({ post: { id: `post-${body.social_account_id}`, status: "queued" } }));
  const changed = await openMultiPublisher();
  fireEvent.click(screen.getByRole("button", { name: "Publish to 2 accounts" }));
  expect(await screen.findByText("Reconnect TikTok first")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("button", { name: "Publish now" })).toBeEnabled());
  expect(screen.getByRole("checkbox", { name: /YouTube Shorts · My Channel/ })).toBeDisabled();
  expect(screen.getByText("Already submitted accounts won’t be sent again.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Publish now" }));
  await waitFor(() => expect(changed).toHaveBeenCalledTimes(2));
  expect(requests.map((body) => body.social_account_id)).toEqual(["acc-1", "tt-1", "tt-1"]);
});

it.each(["network", "server"])("does not resend an account when its %s result is uncertain", async (failure) => {
  const requests = mockPublishing((body) => {
    if (body.social_account_id === "tt-1") {
      if (failure === "network") throw new TypeError("Connection lost");
      return Response.json({ detail: "Gateway error" }, { status: 502 });
    }
    return Response.json({ post: { id: "post-youtube", status: "queued" } });
  });
  const changed = await openMultiPublisher();
  fireEvent.click(screen.getByRole("button", { name: "Publish to 2 accounts" }));
  expect(await screen.findByText(/Check the clip’s post list/)).toBeInTheDocument();
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  expect(screen.getByRole("checkbox", { name: /TikTok · My TikTok/ })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Publish now" })).toBeDisabled();
  expect(requests).toHaveLength(2);
});

it("requires at least one selected account", async () => {
  const requests = mockPublishing();
  await openMultiPublisher();
  fireEvent.click(screen.getByRole("checkbox", { name: /YouTube Shorts · My Channel/ }));
  fireEvent.click(screen.getByRole("checkbox", { name: /TikTok · My TikTok/ }));
  expect(screen.getByRole("button", { name: "Publish now" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Generate caption" })).toBeDisabled();
  expect(requests).toHaveLength(0);
});

it("does not choose an account that requires reconnection", async () => {
  mockPublishing();
  render(<ClipPublishButton taskId="task-1" clip={clip} connections={[connection({ status: "reauth_required" }), tikTokConnection()]} providers={[provider(), tikTokProvider()]} onChanged={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Publish" }));
  expect(screen.getByRole("checkbox", { name: /YouTube Shorts · My Channel/ })).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: /TikTok · My TikTok/ })).toBeChecked();
  await waitFor(() => expect(screen.getByLabelText("Caption")).toHaveValue("A shared caption"));
  expect(fetch).toHaveBeenCalledWith("/api/social/captions", expect.objectContaining({ body: JSON.stringify({ task_id: "task-1", clip_id: "clip-1", provider: "tiktok" }) }));
});

it("validates a shared title before sending any selected account", async () => {
  const requests = mockPublishing();
  await openMultiPublisher();
  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "x".repeat(101) } });
  fireEvent.click(screen.getByRole("button", { name: "Publish to 2 accounts" }));
  expect(await screen.findByText("Keep the title to 100 characters or fewer.")).toBeInTheDocument();
  expect(requests).toHaveLength(0);
});


it("does not submit twice while the first multi-account request is pending", async () => {
  const finish: Array<() => void> = [];
  const requests = mockPublishing(body => new Promise<Response>(resolve => {
    finish.push(() => resolve(Response.json({ post: { id: `post-${body.social_account_id}`, status: "queued" } })));
  }));
  const changed = await openMultiPublisher();
  const submit = screen.getByRole("button", { name: "Publish to 2 accounts" });
  fireEvent.click(submit);
  fireEvent.click(submit);
  expect(requests).toHaveLength(2);
  expect(screen.getByRole("button", { name: "Publishing…" })).toBeDisabled();
  finish.forEach(resolve => resolve());
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
});

it("never offers public when TikTok reports only private for the account", async () => {
  const requests: PostBody[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    if (url.includes("publishing-options")) return Response.json({ privacy_levels: ["private"] });
    if (url.endsWith("captions")) return Response.json({ caption: "Caption" });
    requests.push(JSON.parse(options!.body as string));
    return Response.json({ post: { id: "private-post", status: "queued" } });
  }));
  render(<ClipPublishButton taskId="task-1" clip={clip} connections={[tikTokConnection()]} providers={[tikTokProvider()]} onChanged={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Publish" }));
  const visibility = await screen.findByLabelText("Visibility");
  await waitFor(() => expect(visibility).toBeEnabled());
  expect(screen.getByRole("button", { name: "Publish now" })).toBeDisabled();
  expect(screen.getByText(/Public isn’t available/)).toBeInTheDocument();
  fireEvent.keyDown(visibility, { key: "Enter" });
  expect(screen.queryByRole("option", { name: "Public" })).toBeNull();
  fireEvent.click(await screen.findByRole("option", { name: "Private (only me)" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Publish now" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Publish now" }));
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(requests[0].privacy_level).toBe("private");
});

it("blocks posting if current TikTok permissions cannot be loaded", async () => {
  const fetcher = vi.fn(async (url: string) => url.includes("publishing-options")
    ? Response.json({ detail: "TikTok is unavailable" }, { status: 503 })
    : Response.json({ caption: "Caption" }));
  vi.stubGlobal("fetch", fetcher);
  render(<ClipPublishButton taskId="task-1" clip={clip} connections={[tikTokConnection()]} providers={[tikTokProvider()]} onChanged={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Publish" }));
  expect(await screen.findByText("TikTok is unavailable")).toBeInTheDocument();
  expect(screen.getByLabelText("Visibility")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Publish now" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Publish now" }));
  expect(fetcher.mock.calls.some(([url]) => url === "/api/social/posts")).toBe(false);
});

it("refreshing account permissions clears an earlier visibility choice", async () => {
  let checks = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => url.includes("publishing-options")
    ? Response.json({ privacy_levels: ++checks === 1 ? ["public", "private"] : ["private"] })
    : Response.json({ caption: "Caption" })));
  render(<ClipPublishButton taskId="task-1" clip={clip} connections={[tikTokConnection()]} providers={[tikTokProvider()]} onChanged={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Publish" }));
  const visibility = await screen.findByLabelText("Visibility");
  await waitFor(() => expect(visibility).toBeEnabled());
  fireEvent.keyDown(visibility, { key: "Enter" });
  fireEvent.click(await screen.findByRole("option", { name: "Public" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Publish now" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Refresh options" }));
  await waitFor(() => expect(checks).toBe(2));
  await waitFor(() => expect(visibility).toBeEnabled());
  expect(visibility).toHaveTextContent("Choose visibility");
  expect(screen.getByRole("button", { name: "Publish now" })).toBeDisabled();
  fireEvent.keyDown(visibility, { key: "Enter" });
  expect(screen.queryByRole("option", { name: "Public" })).toBeNull();
});
