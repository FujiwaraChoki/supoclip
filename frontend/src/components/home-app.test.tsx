import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { WORKFLOWS } from "@/lib/workflows";
import HomeApp from "./home-app";

const navigation = vi.hoisted(() => ({ params: new URLSearchParams(), replace: vi.fn() }));
const durations = vi.hoisted(() => ({ seconds: null as number | null }));
const analytics = vi.hoisted(() => ({ track: vi.fn() }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn(), replace: navigation.replace }),
  useSearchParams: () => navigation.params,
}));

vi.mock("@/lib/auth-client", () => ({
  useSession: () => ({
    data: { user: { id: "user-1", name: "Ada", email: "ada@example.com" } },
    isPending: false,
  }),
  signOut: vi.fn(),
}));

vi.mock("@/lib/video-upload", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/video-upload")>()),
  readVideoDuration: vi.fn(async () => durations.seconds),
}));

vi.mock("@/lib/datafast", () => ({ track: analytics.track }));

const billingSummary = {
  monetization_enabled: true,
  plan: "pro",
  subscription_status: "active",
  usage_count: 0,
  usage_limit: null,
  remaining: null,
  can_create_task: true,
  upgrade_required: false,
  reason: null,
  max_upload_bytes: 2_000_000_000,
};

const workflowsInfo = {
  enabled: true,
  workflows: [
    { id: "highlights", available: true, min_source_seconds: 0 },
    { id: "series", available: true, min_source_seconds: 120, part_count: { min: 2, max: 10 }, lengths: { auto: [45, 150], short: [30, 60], medium: [45, 90], long: [90, 175] } },
    { id: "topic", available: true, min_source_seconds: 60 },
    { id: "recap", available: true, min_source_seconds: 180, lengths: { auto: null, short: 45, medium: 75, long: 120 } },
    { id: "trailer", available: true, min_source_seconds: 180, lengths: { auto: 30, short: 20, medium: 40, long: 60 } },
  ],
  topic_max_chars: 120,
  instructions_max_chars: 500,
};

const LINK = "https://www.youtube.com/watch?v=dQw4w9WgXcQ";
// Exactly what the create screen sent before workflows existed.
const LEGACY_KEYS = [
  "source", "font_options", "caption_template", "processing_mode", "output_format", "clip_length", "add_subtitles",
  "include_broll", "cut_long_pauses", "pause_threshold_ms", "remove_filler_words", "filtered_words",
];

let workflowsResponse: () => Response;
let createResponse: () => Response;
let savedClipLength: string;
let created: Record<string, unknown>[];
let preferenceSaves: unknown[];
let storage: Map<string, string>;

beforeEach(() => {
  navigation.params = new URLSearchParams();
  navigation.replace.mockReset();
  durations.seconds = null;
  analytics.track.mockReset();
  workflowsResponse = () => Response.json(workflowsInfo);
  createResponse = () => Response.json({ task_id: "task-1" });
  savedClipLength = "medium";
  created = [];
  preferenceSaves = [];
  storage = new Map();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => { storage.set(key, value); },
    removeItem: (key: string) => { storage.delete(key); },
  });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("billing-summary")) return Response.json(billingSummary);
    if (url.includes("/api/tasks/workflows")) return workflowsResponse();
    if (url.includes("/api/tasks/create")) {
      created.push(JSON.parse(String(init?.body)));
      return createResponse();
    }
    if (url.includes("/api/preferences")) {
      if (init?.method === "PATCH") preferenceSaves.push(JSON.parse(String(init.body)));
      return Response.json({ clipLength: savedClipLength });
    }
    if (url.includes("/api/tasks")) return Response.json({ tasks: [] });
    if (url.includes("/api/fonts")) return Response.json({ fonts: [] });
    if (url.includes("caption-templates")) return Response.json({ templates: [] });
    if (url.includes("announcements")) return Response.json({ announcements: [] });
    if (url.includes("social")) return Response.json({ enabled: false });
    if (url.includes("service-status")) return Response.json({ status: "operational" });
    return Response.json({});
  }));
  // Navigating to the new task is not part of these tests.
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function videoFile(name: string, size: number) {
  const file = new File(["video"], name, { type: "video/mp4" });
  Object.defineProperty(file, "size", { value: size });
  return file;
}

async function renderHome({ workflows = true }: { workflows?: boolean } = {}) {
  render(<HomeApp />, { wrapper: TooltipProvider });
  if (workflows) await screen.findByRole("radiogroup", { name: "What do you want to make?" });
  else await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes("/api/tasks/workflows"))).toBe(true));
  // Let the saved clip length arrive before anything is submitted.
  await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes("/api/preferences"))).toBe(true));
  await act(async () => {});
}

function urlInput() {
  return screen.getByLabelText("YouTube, Twitch, or Kick URL") as HTMLInputElement;
}

function pasteIntoUrl(text: string) {
  const input = urlInput();
  fireEvent.paste(input, { clipboardData: { getData: () => text } });
  fireEvent.change(input, { target: { value: text } });
}

function pick(name: RegExp) {
  fireEvent.click(screen.getByRole("radio", { name }));
}

async function submit(name: string) {
  const button = screen.getByRole("button", { name });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  await waitFor(() => expect(created).toHaveLength(1));
  return created[0];
}

it("states the upload limit and rejects a file over it before upload", async () => {
  render(<HomeApp />, { wrapper: TooltipProvider });

  expect(await screen.findByText(/Uploads up to 2 GB/)).toBeInTheDocument();
  expect(screen.getByText(/above 1080p or 60 fps are scaled down automatically/)).toBeInTheDocument();

  const input = document.getElementById("video-upload") as HTMLInputElement;
  fireEvent.change(input, { target: { files: [videoFile("talk.mp4", 2_000_000_001)] } });

  expect(await screen.findByText(/This file is 2.0 GB, which is over the 2 GB upload limit/)).toBeInTheDocument();
  expect(screen.queryByText(/ready to upload/)).not.toBeInTheDocument();

  fireEvent.change(input, { target: { files: [videoFile("talk.mp4", 2_000_000_000)] } });

  await waitFor(() => expect(screen.getByText(/ready to upload/)).toBeInTheDocument());
  expect(screen.queryByText(/over the 2 GB upload limit/)).not.toBeInTheDocument();
});

describe("when the server does not offer workflows", () => {
  for (const [label, response] of [
    ["disabled", () => Response.json({ ...workflowsInfo, enabled: false })],
    ["unreachable", () => new Response("Not found", { status: 404 })],
  ] as const) {
    it(`hides every workflow control and sends today's request (${label})`, async () => {
      workflowsResponse = response;
      storage.set("supoclip.workflow", "series");
      await renderHome({ workflows: false });

      expect(screen.queryByText("What do you want to make?")).not.toBeInTheDocument();
      expect(screen.queryByRole("radio", { name: /Series/ })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Instructions" })).not.toBeInTheDocument();
      expect(screen.getByText("Drop in a long video. Get back captioned, ready-to-post clips.")).toBeInTheDocument();

      // No rescue either: the link box behaves exactly as before.
      pasteIntoUrl(`${LINK} make it a series`);
      expect(urlInput().value).toBe(`${LINK} make it a series`);
      fireEvent.change(urlInput(), { target: { value: LINK } });

      const body = await submit("Generate clips");
      expect(Object.keys(body)).toEqual(LEGACY_KEYS);
      expect(body.clip_length).toBe("medium");
    });
  }
});

describe("when workflows are enabled", () => {
  it("sends today's request for plain highlights", async () => {
    await renderHome();
    expect(screen.getByRole("radio", { name: /Highlights/ })).toHaveAttribute("aria-checked", "true");
    fireEvent.change(urlInput(), { target: { value: LINK } });
    const body = await submit("Generate clips");
    expect(Object.keys(body)).toEqual(LEGACY_KEYS);
    expect(analytics.track).toHaveBeenCalledWith("task_created", expect.not.objectContaining({ workflow: expect.anything() }));
  });

  it("adds instructions to highlights without naming a workflow", async () => {
    await renderHome();
    fireEvent.click(screen.getByRole("button", { name: "Instructions" }));
    const box = await screen.findByRole("textbox", { name: "Instructions" });
    expect(box).toHaveAttribute("placeholder", "Anything specific? e.g. only the funny moments, skip the sponsor read");
    fireEvent.change(box, { target: { value: "  only the funny moments  " } });
    fireEvent.change(urlInput(), { target: { value: LINK } });
    const body = await submit("Generate clips");
    expect(Object.keys(body)).toEqual([...LEGACY_KEYS, "instructions"]);
    expect(body.instructions).toBe("only the funny moments");
    expect(analytics.track).toHaveBeenCalledWith("task_created", expect.objectContaining({ workflow: "highlights", has_instructions: true }));
  });

  it("sends a series with its part count and part length, without saving the length as the default", async () => {
    await renderHome();
    pick(/Series/);
    expect(screen.getByText("Drop in a long video. Get back a numbered series that keeps people watching.")).toBeInTheDocument();
    expect(storage.get("supoclip.workflow")).toBe("series");

    fireEvent.click(screen.getByRole("button", { name: "Part length" }));
    fireEvent.click(await screen.findByRole("radio", { name: "Long (1.5–3 min)" }));
    expect(screen.getByRole("button", { name: "Part length" })).toHaveTextContent("Long (1.5–3 min)");

    fireEvent.click(screen.getByRole("button", { name: /Parts: Auto/ }));
    const parts = await screen.findByRole("radiogroup", { name: "Number of parts" });
    expect(within(parts).getAllByRole("radio").map((radio) => radio.getAttribute("aria-label"))).toEqual([
      "Auto", "2 parts", "3 parts", "4 parts", "5 parts", "6 parts", "8 parts", "10 parts",
    ]);
    fireEvent.click(within(parts).getByRole("radio", { name: "3 parts" }));
    expect(screen.getByRole("button", { name: /Parts: 3/ })).toBeInTheDocument();

    fireEvent.change(urlInput(), { target: { value: LINK } });
    const body = await submit("Generate series");
    expect(body).toMatchObject({ workflow: "series", workflow_options: { part_count: 3 }, clip_length: "long" });
    expect(body).not.toHaveProperty("instructions");
    expect(preferenceSaves).toEqual([]);
    expect(analytics.track).toHaveBeenCalledWith("task_created", expect.objectContaining({ workflow: "series", has_instructions: false, part_count: 3 }));
  });

  it("omits the part count for an automatic series", async () => {
    await renderHome();
    pick(/Series/);
    fireEvent.change(urlInput(), { target: { value: LINK } });
    const body = await submit("Generate series");
    expect(body.workflow).toBe("series");
    expect(body).not.toHaveProperty("workflow_options");
    expect(body.clip_length).toBe("auto");
  });

  it("keeps recap and trailer lengths to this generation and leaves the saved clip length alone", async () => {
    await renderHome();
    pick(/Recap/);
    fireEvent.click(screen.getByRole("button", { name: "Recap length" }));
    expect(await screen.findByText("Total length of the recap.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "Short (about 45s)" }));
    expect(preferenceSaves).toEqual([]);

    pick(/Highlights/);
    expect(screen.getByRole("button", { name: "Clip length" })).toHaveTextContent("Medium (30–60s)");

    pick(/Recap/);
    fireEvent.change(urlInput(), { target: { value: LINK } });
    const body = await submit("Make recap");
    expect(body).toMatchObject({ workflow: "recap", clip_length: "short" });
    expect(body).not.toHaveProperty("workflow_options");
  });

  it("asks for a topic before searching and sends it with the saved clip length", async () => {
    await renderHome();
    pick(/Topic/);
    const topic = screen.getByLabelText("Topic");
    expect(topic).toHaveAttribute("placeholder", "What topic? e.g. building a business");
    fireEvent.change(urlInput(), { target: { value: LINK } });
    const button = screen.getByRole("button", { name: "Find moments" });
    expect(button).toBeDisabled();

    fireEvent.keyDown(urlInput(), { key: "Enter", metaKey: true });
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter a topic to look for.");
    expect(topic).toHaveFocus();

    fireEvent.change(topic, { target: { value: "!!" } });
    expect(button).toBeDisabled();
    fireEvent.change(topic, { target: { value: "钱" } });
    expect(button).toBeEnabled();
    fireEvent.change(topic, { target: { value: "  building   a business " } });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    const body = await submit("Find moments");
    expect(body).toMatchObject({ workflow: "topic", workflow_options: { topic: "building a business" }, clip_length: "medium" });
    expect(storage.get("supoclip.workflow")).toBe("topic");
    expect([...storage.values()].join()).not.toContain("building");
  });

  it("moves words pasted after a link into instructions, and Undo puts them back", async () => {
    await renderHome();
    pasteIntoUrl(`${LINK} only the funny bits`);

    expect(urlInput().value).toBe(LINK);
    expect(screen.getByRole("textbox", { name: "Instructions" })).toHaveValue("only the funny bits");
    expect(screen.getByText(/Moved “only the funny bits” to instructions\./)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /instead\?/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(urlInput().value).toBe(`${LINK} only the funny bits`);
    expect(screen.queryByRole("textbox", { name: "Instructions" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Moved/)).not.toBeInTheDocument();

    // What the user put back stays as typed.
    fireEvent.blur(urlInput());
    expect(urlInput().value).toBe(`${LINK} only the funny bits`);
  });

  it("submits a link typed with a request by keeping the request as instructions", async () => {
    await renderHome();
    fireEvent.change(urlInput(), { target: { value: `${LINK} skip the sponsor read` } });
    fireEvent.keyDown(urlInput(), { key: "Enter", metaKey: true });
    await waitFor(() => expect(created).toHaveLength(1));
    expect(created[0]).toMatchObject({ source: { url: LINK }, instructions: "skip the sponsor read" });
    expect(created[0]).not.toHaveProperty("workflow");
  });

  it("suggests the workflow a request asks for and switches only when clicked", async () => {
    await renderHome();
    pasteIntoUrl(`${LINK} make a recap`);
    expect(screen.getByRole("button", { name: "Make a recap instead?" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Highlights/ })).toHaveAttribute("aria-checked", "true");
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));

    // Typed and sent in one go: generating would ignore the request, so the press stops to show it.
    fireEvent.change(urlInput(), { target: { value: `${LINK} make it a 3 part series` } });
    fireEvent.keyDown(urlInput(), { key: "Enter", metaKey: true });
    expect(created).toHaveLength(0);
    expect(urlInput().value).toBe(LINK);
    expect(screen.getByRole("radio", { name: /Highlights/ })).toHaveAttribute("aria-checked", "true");

    fireEvent.click(screen.getByRole("button", { name: "Make a 3-part series instead?" }));
    expect(screen.getByRole("radio", { name: /Series/ })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("button", { name: /Parts: 3/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /instead\?/ })).not.toBeInTheDocument();

    const body = await submit("Generate series");
    expect(body).toMatchObject({ workflow: "series", workflow_options: { part_count: 3 }, instructions: "make it a 3 part series" });
  });

  it("holds a click on Generate that lands right after the link box moved a request", async () => {
    await renderHome();
    fireEvent.change(urlInput(), { target: { value: `${LINK} cut a teaser` } });
    // Pressing the button blurs the link box first.
    fireEvent.blur(urlInput());
    fireEvent.click(screen.getByRole("button", { name: "Generate clips" }));
    expect(created).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Make a trailer instead?" })).toBeInTheDocument();
  });

  it("drops punctuation stuck to a pasted link", async () => {
    await renderHome();
    pasteIntoUrl(`(${LINK}),`);
    expect(urlInput().value).toBe(LINK);
    expect(screen.queryByRole("textbox", { name: "Instructions" })).not.toBeInTheDocument();
  });

  it("fills the topic from a suggestion", async () => {
    await renderHome();
    pasteIntoUrl(`${LINK} only the parts about building a business`);
    fireEvent.click(screen.getByRole("button", { name: "Find every moment about “building a business” instead?" }));
    expect(screen.getByRole("radio", { name: /Topic/ })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByLabelText("Topic")).toHaveValue("building a business");
  });

  it("offers to move a typed request out of the link box instead of showing an error", async () => {
    await renderHome();
    fireEvent.change(urlInput(), { target: { value: "make a short recap of this" } });
    fireEvent.blur(urlInput());

    expect(screen.getByText(/That looks like an instruction, not a link\./)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Make a recap instead?" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Generate clips" }));
    expect(created).toHaveLength(0);
    expect(screen.queryByText(/Enter a valid/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Move to instructions" }));
    expect(urlInput().value).toBe("");
    expect(screen.getByRole("textbox", { name: "Instructions" })).toHaveValue("make a short recap of this");
    await waitFor(() => expect(urlInput()).toHaveFocus());
    expect(screen.getByText(/Moved “make a short recap of this” to instructions\./)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Make a recap instead?" })).toBeInTheDocument();
  });

  it("keeps a typed request as instructions when a link replaces it, as the note promises", async () => {
    await renderHome();
    fireEvent.change(urlInput(), { target: { value: "only the funny bits" } });
    fireEvent.blur(urlInput());
    expect(screen.getByText(/That looks like an instruction, not a link\./)).toBeInTheDocument();

    // Cleared to make room for the link, then the link pasted in.
    fireEvent.change(urlInput(), { target: { value: "" } });
    expect(screen.getByText(/That looks like an instruction, not a link\./)).toBeInTheDocument();
    pasteIntoUrl(LINK);
    expect(urlInput().value).toBe(LINK);
    expect(screen.getByRole("textbox", { name: "Instructions" })).toHaveValue("only the funny bits");
    expect(screen.getByText(/Moved “only the funny bits” to instructions\./)).toBeInTheDocument();

    const body = await submit("Generate clips");
    expect(body).toMatchObject({ source: { url: LINK }, instructions: "only the funny bits" });
  });

  it("Undo after a link replaced the request drops the request and keeps the link", async () => {
    await renderHome();
    fireEvent.change(urlInput(), { target: { value: "only the funny bits" } });
    fireEvent.blur(urlInput());
    fireEvent.change(urlInput(), { target: { value: LINK } });
    expect(screen.getByRole("textbox", { name: "Instructions" })).toHaveValue("only the funny bits");
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(urlInput().value).toBe(LINK);
    expect(screen.queryByRole("textbox", { name: "Instructions" })).not.toBeInTheDocument();
  });

  it("drops the note when the request is rewritten, and reads the new one on blur", async () => {
    await renderHome();
    fireEvent.change(urlInput(), { target: { value: "only the funny bits" } });
    fireEvent.blur(urlInput());
    fireEvent.change(urlInput(), { target: { value: "make a trailer" } });
    expect(screen.queryByText(/That looks like an instruction/)).not.toBeInTheDocument();
    fireEvent.blur(urlInput());
    expect(screen.getByText(/That looks like an instruction/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Make a trailer instead?" })).toBeInTheDocument();
  });

  it("splits a link from Chinese written straight after it", async () => {
    await renderHome();
    pasteIntoUrl(`${LINK}做成三集系列`);
    expect(urlInput().value).toBe(LINK);
    expect(screen.getByRole("textbox", { name: "Instructions" })).toHaveValue("做成三集系列");
    expect(screen.getByRole("button", { name: "Make a 3-part series instead?" })).toBeInTheDocument();
  });

  it("removing the moved instructions also drops their suggestion", async () => {
    await renderHome();
    pasteIntoUrl(`${LINK} make a recap`);
    expect(screen.getByRole("button", { name: "Make a recap instead?" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Remove instructions" }));
    expect(screen.queryByRole("button", { name: "Make a recap instead?" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Moved/)).not.toBeInTheDocument();
  });

  it("explains workflow rejections in the user's language", async () => {
    createResponse = () => Response.json(
      { detail: { code: "TOO_MANY_PARTS", message: "This video fits at most 4 parts. Choose fewer parts or Auto.", max_parts: 4 } },
      { status: 422 },
    );
    await renderHome();
    pick(/Series/);
    fireEvent.change(urlInput(), { target: { value: LINK } });
    await submit("Generate series");
    expect(await screen.findByText("This video fits at most 4 parts. Choose fewer parts or Auto.")).toBeInTheDocument();
  });

  it("opens the workflow from the address and tidies it", async () => {
    navigation.params = new URLSearchParams("workflow=trailer");
    await renderHome();
    await waitFor(() => expect(screen.getByRole("radio", { name: /Trailer/ })).toHaveAttribute("aria-checked", "true"));
    expect(navigation.replace).toHaveBeenCalledWith("/", { scroll: false });
    expect(screen.getByRole("button", { name: "Trailer length" })).toHaveTextContent("Auto (about 30s)");
  });

  it("remembers the last workflow", async () => {
    storage.set("supoclip.workflow", "recap");
    await renderHome();
    expect(screen.getByRole("radio", { name: /Recap/ })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("button", { name: "Make recap" })).toBeInTheDocument();
  });

  it("warns when an upload is too short for the workflow and offers highlights", async () => {
    durations.seconds = 95;
    await renderHome();
    pick(/Recap/);
    fireEvent.change(document.getElementById("video-upload") as HTMLInputElement, { target: { files: [videoFile("talk.mp4", 1000)] } });
    expect(await screen.findByText(/This video is 1:35\. Recaps work on videos over 3 minutes\./)).toBeInTheDocument();
    pick(/Topic/);
    expect(screen.queryByText(/This video is 1:35/)).not.toBeInTheDocument();
    pick(/Trailer/);
    fireEvent.click(screen.getByRole("button", { name: "Use Highlights" }));
    expect(screen.getByRole("radio", { name: /Highlights/ })).toHaveAttribute("aria-checked", "true");
  });

  it("stops before uploading a video the workflow cannot use", async () => {
    durations.seconds = 95;
    await renderHome();
    pick(/Recap/);
    fireEvent.change(document.getElementById("video-upload") as HTMLInputElement, { target: { files: [videoFile("talk.mp4", 1000)] } });
    await screen.findByText(/This video is 1:35\./);
    fireEvent.click(screen.getByRole("button", { name: "Make recap" }));
    expect(await screen.findByText("This video is already short. Recaps work on videos over 3 minutes.")).toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.some(([input]) => /upload/.test(String(input)))).toBe(false);
    expect(created).toHaveLength(0);

    // Picking something that fits clears it.
    fireEvent.click(screen.getByRole("button", { name: "Use Highlights" }));
    expect(screen.queryByText("This video is already short. Recaps work on videos over 3 minutes.")).not.toBeInTheDocument();
  });

  it("explains a part count the upload cannot hold before uploading it", async () => {
    durations.seconds = 200;
    await renderHome();
    pick(/Series/);
    fireEvent.click(screen.getByRole("button", { name: /Parts: Auto/ }));
    fireEvent.click(within(await screen.findByRole("radiogroup", { name: "Number of parts" })).getByRole("radio", { name: "6 parts" }));
    fireEvent.change(document.getElementById("video-upload") as HTMLInputElement, { target: { files: [videoFile("talk.mp4", 1000)] } });
    expect(await screen.findByText("This video fits at most 3 parts. Choose fewer parts or Auto.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Generate series" }));
    expect(await screen.findAllByText("This video fits at most 3 parts. Choose fewer parts or Auto.")).toHaveLength(2);
    expect(created).toHaveLength(0);
    expect(vi.mocked(fetch).mock.calls.some(([input]) => /upload/.test(String(input)))).toBe(false);

    // The parts menu is still open: 6 is marked but can no longer be picked.
    const parts = screen.getByRole("radiogroup", { name: "Number of parts" });
    expect(within(parts).getByRole("radio", { name: "6 parts" })).toBeDisabled();
    fireEvent.click(within(parts).getByRole("radio", { name: "3 parts" }));
    expect(screen.queryByText("This video fits at most 3 parts. Choose fewer parts or Auto.")).not.toBeInTheDocument();
  });

  it("only offers part counts an upload can hold", async () => {
    durations.seconds = 200;
    await renderHome();
    pick(/Series/);
    fireEvent.change(document.getElementById("video-upload") as HTMLInputElement, { target: { files: [videoFile("talk.mp4", 1000)] } });
    await screen.findByText(/ready to upload/);
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: /Parts: Auto/ }));
    const parts = await screen.findByRole("radiogroup", { name: "Number of parts" });
    // Auto parts are at least 45 s: 200 s holds three (3 × 45 × 1.25 ≤ 200).
    expect(within(parts).getByRole("radio", { name: "3 parts" })).toBeEnabled();
    expect(within(parts).getByRole("radio", { name: "4 parts" })).toBeDisabled();
    expect(within(parts).getByRole("radio", { name: "Auto" })).toBeEnabled();
  });

  it("counts instructions near the limit and blocks sending too many", async () => {
    await renderHome();
    fireEvent.click(screen.getByRole("button", { name: "Instructions" }));
    const box = await screen.findByRole("textbox", { name: "Instructions" });
    fireEvent.change(box, { target: { value: "a".repeat(399) } });
    expect(screen.queryByText("399/500")).not.toBeInTheDocument();
    fireEvent.change(box, { target: { value: "a".repeat(400) } });
    expect(screen.getByText("400/500")).toBeInTheDocument();
    fireEvent.change(box, { target: { value: "a".repeat(501) } });
    fireEvent.change(urlInput(), { target: { value: LINK } });
    expect(screen.getByRole("button", { name: "Generate clips" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Remove instructions" }));
    expect(screen.queryByRole("textbox", { name: "Instructions" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate clips" })).toBeEnabled();
  });

  it("labels every workflow's card and button", async () => {
    await renderHome();
    const buttons = { highlights: "Generate clips", series: "Generate series", topic: "Find moments", recap: "Make recap", trailer: "Make trailer" };
    for (const workflow of WORKFLOWS) {
      pick(new RegExp(workflow, "i"));
      expect(screen.getByRole("button", { name: buttons[workflow] })).toBeInTheDocument();
    }
  });
});
