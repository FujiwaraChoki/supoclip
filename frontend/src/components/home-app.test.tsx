import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

// The tour host's own behaviour is tested in feature-tour.test.tsx; here we only need what the page hands it.
const tour = vi.hoisted(() => ({ props: null as null | { onSettingsSection?: (id: string) => void; onEnd?: () => void } }));
vi.mock("@/components/feature-tour", () => ({
  FeatureTourHost: (props: { onSettingsSection?: (id: string) => void; onEnd?: () => void }) => {
    tour.props = props;
    return null;
  },
}));

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
    { id: "highlights", available: true, min_source_seconds: 0, settings: [{ key: "max_clips", type: "int", default: null, min: 1, max: 20 }] },
    { id: "series", available: true, min_source_seconds: 120, settings: [
      { key: "cold_open", type: "bool", default: false },
      { key: "endings", type: "enum", default: "cliffhanger", choices: ["cliffhanger", "natural"] },
    ], part_count: { min: 2, max: 10 }, lengths: { auto: [45, 150], short: [30, 60], medium: [45, 90], long: [90, 175] } },
    { id: "topic", available: true, min_source_seconds: 60 },
    { id: "recap", available: true, min_source_seconds: 180, lengths: { auto: null, short: 45, medium: 75, long: 120 } },
    { id: "trailer", available: true, min_source_seconds: 180, lengths: { auto: 30, short: 20, medium: 40, long: 60 } },
  ],
  topic_max_chars: 120,
  instructions_max_chars: 500,
};

const editStylesInfo = {
  enabled: true,
  intensities: ["off", "subtle", "bold", "max"],
  defaults: { sfx: true, music: true, music_mood: "auto", zooms: true, shake: true, transitions: true, speed: true, censor: "bleep_bar", grade: "auto", grain: false, progress_bar: false, loop_ending: false, wait_for_it: true, reactions: false },
  settings: [],
  music_moods: ["hype", "chill", "dramatic", "funny", "inspiring", "dark"],
  grades: ["none", "punchy", "warm", "teal_orange", "noir", "vhs", "dream"],
  templates: ["marker_circle", "emoji_drop", "slam", "behind_head", "stat_counter", "kinetic_stack", "decode_glitch", "highlighter_swipe", "face_doodles"],
  reactions_available: false,
};
const legacyTemplates = [
  { id: "default", name: "Default", description: "Punchy", animation: "karaoke", font_color: "#FFFFFF" },
  { id: "mrbeast", name: "MrBeast", description: "Bold", animation: "pop", font_color: "#FFFFFF" },
];
const fxTemplates = editStylesInfo.templates.map((id) => ({ id, name: id, description: "", animation: "pop", fx: true, requires: [] }));

const LINK = "https://www.youtube.com/watch?v=dQw4w9WgXcQ";
// Exactly what the create screen sent before workflows existed.
const LEGACY_KEYS = [
  "source", "font_options", "caption_template", "processing_mode", "output_format", "clip_length", "add_subtitles",
  "include_broll", "cut_long_pauses", "pause_threshold_ms", "remove_filler_words", "filtered_words",
];

let workflowsResponse: () => Response | Promise<Response>;
let editStylesResponse: () => Response | Promise<Response>;
let templateRequests: string[];
let createResponse: () => Response;
let savedClipLength: string;
let created: Record<string, unknown>[];
let preferenceSaves: unknown[];
let settingsSaves: Array<{ createSettings: Record<string, unknown>; fontFamily?: string }>;
let savedCreateSettings: unknown;
let savedFontFamily: string;
let storage: Map<string, string>;

beforeEach(() => {
  navigation.params = new URLSearchParams();
  navigation.replace.mockReset();
  durations.seconds = null;
  analytics.track.mockReset();
  workflowsResponse = () => Response.json(workflowsInfo);
  editStylesResponse = () => Response.json({ ...editStylesInfo, enabled: false });
  templateRequests = [];
  createResponse = () => Response.json({ task_id: "task-1" });
  savedClipLength = "medium";
  created = [];
  preferenceSaves = [];
  settingsSaves = [];
  savedCreateSettings = undefined;
  savedFontFamily = "TikTokSans-Regular";
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
    if (url.includes("/api/tasks/edit-styles")) return editStylesResponse();
    if (url.includes("/api/tasks/create")) {
      created.push(JSON.parse(String(init?.body)));
      return createResponse();
    }
    if (url.includes("/api/preferences")) {
      if (init?.method === "PATCH") {
        const body = JSON.parse(String(init.body));
        (body.createSettings ? settingsSaves : preferenceSaves).push(body);
      }
      return Response.json({ clipLength: savedClipLength, createSettings: savedCreateSettings, fontFamily: savedFontFamily });
    }
    if (url.includes("/api/tasks")) return Response.json({ tasks: [] });
    if (url.includes("/api/fonts")) return Response.json({ fonts: [] });
    if (url.includes("caption-templates")) {
      templateRequests.push(url);
      return Response.json({ templates: url.includes("include=fx") ? [...legacyTemplates, ...fxTemplates] : legacyTemplates });
    }
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

const settingsChip = () => screen.getByRole("button", { name: /^Settings:/ });

function openSettings() {
  if (!screen.queryByRole("tablist", { name: "Settings" })) fireEvent.click(settingsChip());
}

function openSection(name: RegExp) {
  openSettings();
  fireEvent.click(screen.getByRole("tab", { name }));
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
  fireEvent.click(screen.getByRole("button", { name: "Upload limits and details" }));
  const details = within(await screen.findByRole("dialog"));
  expect(details.getByRole("link", { name: "Settings" })).toHaveAttribute("href", "/settings");
  expect(details.getByText(/above 1080p or 60 fps are scaled down automatically/)).toBeInTheDocument();

  const input = document.getElementById("video-upload") as HTMLInputElement;
  fireEvent.change(input, { target: { files: [videoFile("talk.mp4", 2_000_000_001)] } });

  expect(await screen.findByText(/This file is 2.0 GB, which is over the 2 GB upload limit/)).toBeInTheDocument();
  expect(screen.queryByText(/ready to upload/)).not.toBeInTheDocument();

  fireEvent.change(input, { target: { files: [videoFile("talk.mp4", 2_000_000_000)] } });

  await waitFor(() => expect(screen.getByText(/ready to upload/)).toBeInTheDocument());
  expect(screen.queryByText(/over the 2 GB upload limit/)).not.toBeInTheDocument();
});

it("sends the caption defaults saved in Settings with a new task", async () => {
  const fetchMock = vi.mocked(fetch);
  const defaultFetch = fetchMock.getMockImplementation()!;
  fetchMock.mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.includes("/api/preferences")) {
      return Response.json({ fontFamily: "Inter", fontSize: 24, fontColor: "#FFD700", clipLength: "auto" });
    }
    if (url.includes("/api/tasks/create")) return Response.json({ task_id: "task-1" });
    return defaultFetch(input, init);
  });
  render(<HomeApp />, { wrapper: TooltipProvider });

  fireEvent.change(screen.getByLabelText("YouTube, Twitch, or Kick URL"), { target: { value: "https://www.youtube.com/watch?v=dQw4w9WgXcQ" } });
  const generate = screen.getByRole("button", { name: "Generate clips" });
  await waitFor(() => expect(generate).toBeEnabled());
  fireEvent.click(generate);

  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/api/tasks/create"))).toBe(true));
  const [, init] = fetchMock.mock.calls.find(([url]) => String(url).includes("/api/tasks/create"))!;
  // Size stays at its untouched stock value, so the caption template keeps deciding it.
  expect(JSON.parse(String(init?.body)).font_options).toEqual({ font_family: "Inter", font_size: null, font_color: "#FFD700" });
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
      expect(document.querySelector('[data-tour="workflow-picker"]')).toBeNull();
      openSettings();
      expect(screen.queryByRole("tab", { name: /Advanced/ })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Upload limits and details" }));
      expect(screen.queryByRole("button", { name: "Take the tour" })).not.toBeInTheDocument();
      fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });

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

describe("before the server says what's offered", () => {
  it("won't send a saved series as plain highlights", async () => {
    storage.set("supoclip.workflow", "series");
    let settle: (response: Response) => void = () => {};
    workflowsResponse = () => new Promise<Response>((resolve) => { settle = resolve; });
    render(<HomeApp />, { wrapper: TooltipProvider });
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes("/api/tasks/workflows"))).toBe(true));
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes("/api/pr"))).toBe(true));
    await act(async () => {});
    fireEvent.change(urlInput(), { target: { value: LINK } });
    expect(screen.getByRole("button", { name: /Generate/ })).toBeDisabled();

    await act(async () => { settle(Response.json(workflowsInfo)); });
    expect(screen.getByRole("radio", { name: /Series/ })).toHaveAttribute("aria-checked", "true");
  });

  it("doesn't hold back someone making plain highlights", async () => {
    storage.set("supoclip.workflow", "highlights");
    workflowsResponse = () => new Promise<Response>(() => {});
    render(<HomeApp />, { wrapper: TooltipProvider });
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes("/api/tasks/workflows"))).toBe(true));
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes("/api/pr"))).toBe(true));
    await act(async () => {});
    fireEvent.change(urlInput(), { target: { value: LINK } });
    const body = await submit("Generate clips");
    expect(Object.keys(body)).toEqual(LEGACY_KEYS);
  });
});

describe("when only some workflows are open to everyone", () => {
  const partial = () => Response.json({
    ...workflowsInfo,
    workflows: workflowsInfo.workflows.map((entry) => {
      const open = entry.id === "trailer" || entry.id === "topic";
      return {
        ...entry,
        available: open || entry.id === "highlights",
        customizable: open,
        settings: open ? [{ key: "spoilers", type: "enum", default: "strict", choices: ["strict", "relaxed"] }] : [],
      };
    }),
  });

  it("offers instructions and settings only on the open workflows", async () => {
    workflowsResponse = partial;
    await renderHome();

    expect(screen.getByRole("radio", { name: /Series/ })).toBeDisabled();
    expect(screen.getByRole("radio", { name: /Recap/ })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Instructions" })).not.toBeInTheDocument();
    openSettings();
    expect(screen.queryByRole("tab", { name: /Advanced/ })).not.toBeInTheDocument();

    // Words after a link stay put: plain highlights can't take instructions.
    pasteIntoUrl(`${LINK} only the funny bits`);
    expect(urlInput().value).toBe(`${LINK} only the funny bits`);
    fireEvent.change(urlInput(), { target: { value: LINK } });
    const body = await submit("Generate clips");
    expect(Object.keys(body)).toEqual(LEGACY_KEYS);
  });

  it("shows them once an open workflow is chosen", async () => {
    workflowsResponse = partial;
    await renderHome();
    fireEvent.click(screen.getByRole("radio", { name: /Trailer/ }));
    expect(await screen.findByRole("button", { name: "Instructions" })).toBeInTheDocument();
    openSettings();
    expect(screen.getByRole("tab", { name: /Advanced/ })).toBeInTheDocument();
  });
});

describe("when workflows are enabled", () => {
  it("links to the workflows tour", async () => {
    await renderHome();
    expect(document.querySelector('[data-tour="workflow-picker"]')).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Upload limits and details" }));
    expect(await screen.findByRole("button", { name: "Take the tour" })).toBeInTheDocument();
  });

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

    openSection(/Part length/);
    fireEvent.click(await screen.findByRole("radio", { name: "Long (1.5–3 min)" }));
    expect(settingsChip()).toHaveTextContent("Long (1.5–3 min)");

    openSection(/Number of parts/);
    const parts = await screen.findByRole("radiogroup", { name: "Number of parts" });
    expect(within(parts).getAllByRole("radio").map((radio) => radio.getAttribute("aria-label"))).toEqual([
      "Auto", "2 parts", "3 parts", "4 parts", "5 parts", "6 parts", "8 parts", "10 parts",
    ]);
    fireEvent.click(within(parts).getByRole("radio", { name: "3 parts" }));
    expect(settingsChip()).toHaveTextContent("Parts: 3");

    fireEvent.change(urlInput(), { target: { value: LINK } });
    const body = await submit("Generate series");
    expect(body).toMatchObject({ workflow: "series", workflow_options: { part_count: 3 }, clip_length: "long" });
    expect(body).not.toHaveProperty("instructions");
    expect(preferenceSaves).toEqual([]);
    expect(analytics.track).toHaveBeenCalledWith("task_created", expect.objectContaining({ workflow: "series", has_instructions: false, part_count: 3 }));
  });

  it("keeps Advanced closed and leaves the request as it was until something changes", async () => {
    await renderHome();
    pick(/Series/);
    expect(screen.queryByLabelText("Cold open")).not.toBeInTheDocument();
    openSection(/Advanced/);
    expect(await screen.findByRole("switch", { name: "Cold open" })).not.toBeChecked();
    fireEvent.change(urlInput(), { target: { value: LINK } });
    const body = await submit("Generate series");
    expect(body).not.toHaveProperty("workflow_options");
  });

  it("sends only the settings that changed, counts them, and resets", async () => {
    await renderHome();
    pick(/Series/);
    openSection(/Advanced/);
    const cold = await screen.findByRole("switch", { name: "Cold open" });
    fireEvent.click(cold);
    expect(screen.getByRole("tab", { name: /Advanced/ })).toHaveTextContent("1");
    fireEvent.click(screen.getByRole("button", { name: "Reset to defaults" }));
    expect(screen.getByRole("tab", { name: /Advanced/ })).not.toHaveTextContent("1");
    expect(cold).not.toBeChecked();
    fireEvent.click(cold);
    fireEvent.change(urlInput(), { target: { value: LINK } });
    const body = await submit("Generate series");
    expect(body.workflow_options).toEqual({ advanced: { cold_open: true } });
  });

  it("holds the form while a number is out of range, and sends highlights' clip count without a workflow", async () => {
    await renderHome();
    pick(/Highlights/);
    openSection(/Advanced/);
    const clips = await screen.findByRole("spinbutton", { name: "Number of clips" });
    fireEvent.change(urlInput(), { target: { value: LINK } });
    fireEvent.change(clips, { target: { value: "99" } });
    expect(screen.getByText("Enter a whole number from 1 to 20.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate clips" })).toBeDisabled();
    fireEvent.change(clips, { target: { value: "3" } });
    const body = await submit("Generate clips");
    expect(body).not.toHaveProperty("workflow");
    expect(body.workflow_options).toEqual({ advanced: { max_clips: 3 } });
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
    openSection(/Recap length/);
    expect(await screen.findByText("Total length of the recap.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "Short (about 45s)" }));
    expect(preferenceSaves).toEqual([]);

    pick(/Highlights/);
    expect(settingsChip()).toHaveTextContent("Medium (30–60s)");

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
    expect(settingsChip()).toHaveTextContent("Parts: 3");
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
    expect(settingsChip()).toHaveTextContent("Auto (about 30s)");
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
    openSection(/Number of parts/);
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
    openSection(/Number of parts/);
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

describe("remembering settings between videos", () => {

  it("comes back with what was saved and sends it without being touched", async () => {
    savedCreateSettings = {
      workflow: "recap",
      captionTemplate: "minimal",
      outputFormat: "vertical_fit",
      cutLongPauses: false,
      filteredWords: "um, like",
      lengths: { recap: "short" },
    };
    await renderHome();
    await waitFor(() => expect(settingsChip()).toHaveTextContent("Short"));
    fireEvent.change(urlInput(), { target: { value: LINK } });
    const body = await submit("Make recap");
    expect(body).toMatchObject({ workflow: "recap", caption_template: "minimal", output_format: "vertical_fit", cut_long_pauses: false, filtered_words: ["um", "like"], clip_length: "short" });
    expect(settingsSaves).toEqual([]);
  });

  it("ignores saved values it can't use", async () => {
    savedCreateSettings = { workflow: "montage", outputFormat: "square", pauseThresholdMs: 1, lengths: { recap: "huge" }, advanced: { highlights: { max_clips: 999 } } };
    await renderHome();
    pick(/Highlights/);
    fireEvent.change(urlInput(), { target: { value: LINK } });
    const body = await submit("Generate clips");
    expect(body.output_format).toBe("vertical");
    expect(body.pause_threshold_ms).toBe(1200);
    expect(body).not.toHaveProperty("workflow_options");
  });

  it("saves a change, including Advanced, but not the topic, instructions, or part count", async () => {
    await renderHome();
    pick(/Series/);
    openSection(/Advanced/);
    fireEvent.click(await screen.findByRole("switch", { name: "Cold open" }));
    openSection(/Number of parts/);
    fireEvent.click(await screen.findByRole("radio", { name: "3 parts" }));
    await waitFor(() => expect(settingsSaves.length).toBeGreaterThan(0), { timeout: 2000 });
    const saved = settingsSaves[settingsSaves.length - 1].createSettings;
    expect(saved).toMatchObject({ workflow: "series", advanced: { series: { cold_open: true } } });
    expect(JSON.stringify(saved)).not.toMatch(/partCount|part_count|instructions|topic/);
  });

  it("resets everything it remembers, and saves that", async () => {
    savedCreateSettings = { captionTemplate: "minimal", includeBroll: true, advanced: { series: { cold_open: true } } };
    savedFontFamily = "Inter";
    await renderHome();
    openSettings();
    fireEvent.click(await screen.findByRole("button", { name: "Reset all settings" }));
    await waitFor(() => expect(settingsSaves.length).toBeGreaterThan(0), { timeout: 2000 });
    const last = settingsSaves[settingsSaves.length - 1];
    expect(last.createSettings).toMatchObject({ captionTemplate: "default", includeBroll: false, advanced: {} });
    expect(last.fontFamily).toBe("TikTokSans-Regular");
  });
});

describe("dynamic edits", () => {
  beforeEach(() => {
    // jsdom has no canvas; the tiles just don't draw.
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
  });

  const enable = () => { editStylesResponse = () => Response.json(editStylesInfo); };

  async function renderWithEdits() {
    enable();
    await renderHome();
    await waitFor(() => expect(templateRequests).toContain("/api/caption-templates?include=fx"));
  }

  it("adds exactly edit_style for a bold request", async () => {
    await renderWithEdits();
    openSection(/Edits/);
    const group = screen.getByRole("radiogroup", { name: "Edit intensity" });
    expect(group).toHaveAttribute("data-tour", "edit-intensity");
    expect(within(group).getByRole("radio", { name: /Off/ })).toHaveAttribute("aria-checked", "true");
    expect(screen.queryByRole("switch", { name: "Sound effects" })).not.toBeInTheDocument();
    pick(/Bold/);
    fireEvent.click(screen.getByRole("button", { name: "Fine-tune" }));
    fireEvent.click(screen.getByRole("switch", { name: "Music" }));
    expect(settingsChip()).toHaveAccessibleName(/Bold/);
    fireEvent.change(urlInput(), { target: { value: LINK } });
    const body = await submit("Generate clips");
    expect(Object.keys(body)).toEqual([...LEGACY_KEYS, "edit_style"]);
    expect(body.edit_style).toEqual({ intensity: "bold", music: false });
    expect(body.caption_template).toBe("default");
  });

  it("sends today's request with edits off, even with the feature on", async () => {
    await renderWithEdits();
    fireEvent.change(urlInput(), { target: { value: LINK } });
    const body = await submit("Generate clips");
    expect(Object.keys(body)).toEqual(LEGACY_KEYS);
  });

  it("offers animated caption styles as live tiles, and sends the one picked", async () => {
    await renderWithEdits();
    openSection(/Captions/);
    // One list: the classic styles first, then the animated ones.
    const tiles = screen.getByRole("radiogroup", { name: "Caption style" });
    expect(tiles).toHaveAttribute("data-tour", "caption-styles");
    const names = within(tiles).getAllByRole("radio").map((radio) => radio.getAttribute("aria-label"));
    expect(names).toHaveLength(11);
    expect(names.slice(0, 2)).toEqual(["Default", "MrBeast"]);
    expect(screen.getAllByRole("radiogroup", { name: "Caption style" })).toHaveLength(1);
    // Font and colour stay folded away until asked for.
    expect(screen.queryByText("Color")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Customize" }));
    expect(screen.getByText("Color")).toBeInTheDocument();
    fireEvent.click(within(tiles).getByRole("radio", { name: "Slam" }));
    expect(within(tiles).getByRole("radio", { name: "Slam" })).toHaveAttribute("aria-checked", "true");
    expect(settingsChip()).toHaveAccessibleName(/Slam/);
    fireEvent.change(urlInput(), { target: { value: LINK } });
    const body = await submit("Generate clips");
    // Style only: the template carries it, intensity off adds nothing.
    expect(Object.keys(body)).toEqual(LEGACY_KEYS);
    expect(body.caption_template).toBe("slam");
  });

  it("hides everything and never asks for animated templates while the feature is off", async () => {
    savedCreateSettings = { captionTemplate: "slam", editStyle: { intensity: "bold" } };
    await renderHome();
    await waitFor(() => expect(templateRequests.length).toBeGreaterThan(0));
    openSettings();
    expect(screen.queryByRole("tab", { name: /Edits/ })).not.toBeInTheDocument();
    openSection(/Captions/);
    expect(within(screen.getByRole("radiogroup", { name: "Caption style" })).queryByRole("radio", { name: "Slam" })).not.toBeInTheDocument();
    expect(templateRequests.every((url) => !url.includes("include"))).toBe(true);
  });

  for (const [label, response] of [
    ["the flag is off", () => Response.json({ ...editStylesInfo, enabled: false })],
    ["the fetch fails", () => Promise.reject(new TypeError("offline"))],
    ["an older backend answers 404", () => new Response("Not found", { status: 404 })],
  ] as const) {
    it(`drops a stored bold style and animated template when ${label}, but keeps them saved`, async () => {
      editStylesResponse = response;
      savedCreateSettings = { captionTemplate: "slam", editStyle: { intensity: "bold", music: false } };
      await renderHome();
      // A save still carries the stored choice, for when the flag comes back.
      openSection(/Cleanup/);
      fireEvent.click(screen.getByRole("switch", { name: /Remove filler words/ }));
      await waitFor(() => expect(settingsSaves.length).toBeGreaterThan(0), { timeout: 2000 });
      expect(settingsSaves[settingsSaves.length - 1].createSettings).toMatchObject({
        captionTemplate: "slam", editStyle: { intensity: "bold", music: false },
      });

      fireEvent.change(urlInput(), { target: { value: LINK } });
      const body = await submit("Generate clips");
      expect(Object.keys(body)).toEqual(LEGACY_KEYS);
      expect(body.caption_template).toBe("mrbeast");
    });
  }

  it("keeps the two cleanup switches up front and the fine print behind More options", async () => {
    await renderWithEdits();
    openSection(/Cleanup/);
    expect(screen.getAllByRole("switch")).toHaveLength(2);
    expect(screen.queryByLabelText("Pause threshold (ms)")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "More options" }));
    fireEvent.change(screen.getByLabelText("Pause threshold (ms)"), { target: { value: "800" } });
    fireEvent.change(urlInput(), { target: { value: LINK } });
    const body = await submit("Generate clips");
    expect(body.pause_threshold_ms).toBe(800);
  });

  it("saves the edit style and brings it back", async () => {
    await renderWithEdits();
    openSection(/Edits/);
    pick(/Max/);
    fireEvent.click(screen.getByRole("button", { name: "Fine-tune" }));
    fireEvent.click(screen.getByRole("switch", { name: "Sound effects" }));
    await waitFor(() => expect(settingsSaves.length).toBeGreaterThan(0), { timeout: 2000 });
    expect(settingsSaves[settingsSaves.length - 1].createSettings.editStyle).toEqual({ intensity: "max", sfx: false });

    cleanup();
    settingsSaves = [];
    created = [];
    savedCreateSettings = { editStyle: { intensity: "subtle", progress_bar: true } };
    await renderWithEdits();
    openSection(/Edits/);
    expect(screen.getByRole("radio", { name: /Subtle/ })).toHaveAttribute("aria-checked", "true");
    // The folded row says one thing is changed; opened, it is the progress bar.
    expect(screen.getByRole("button", { name: /Fine-tune/ })).toHaveTextContent("1");
    fireEvent.click(screen.getByRole("button", { name: /Fine-tune/ }));
    expect(screen.getByRole("switch", { name: "Progress bar" })).toBeChecked();
    fireEvent.change(urlInput(), { target: { value: LINK } });
    expect((await submit("Generate clips")).edit_style).toEqual({ intensity: "subtle", progress_bar: true });
  });

  it("holds Generate until it knows whether saved edits can be sent", async () => {
    let settle: (response: Response) => void = () => {};
    editStylesResponse = () => new Promise<Response>((resolve) => { settle = resolve; });
    savedCreateSettings = { editStyle: { intensity: "bold" } };
    await renderHome();
    fireEvent.change(urlInput(), { target: { value: LINK } });
    expect(screen.getByRole("button", { name: "Generate clips" })).toBeDisabled();
    await act(async () => { settle(Response.json(editStylesInfo)); });
    expect((await submit("Generate clips")).edit_style).toEqual({ intensity: "bold" });
  });

  it("opens the settings menu on the section a tour step talks about, and closes it after", async () => {
    await renderWithEdits();
    expect(screen.queryByRole("tablist", { name: "Settings" })).not.toBeInTheDocument();
    act(() => tour.props?.onSettingsSection?.("edits"));
    await waitFor(() => expect(screen.getByRole("tab", { name: /Edits/ })).toHaveAttribute("aria-selected", "true"));
    expect(document.querySelector('[data-tour="edit-intensity"]')).not.toBeNull();
    // Idempotent: asking again for another section keeps the menu open and switches.
    act(() => tour.props?.onSettingsSection?.("captions"));
    await waitFor(() => expect(document.querySelector('[data-tour="caption-styles"]')).not.toBeNull());
    act(() => tour.props?.onEnd?.());
    await waitFor(() => expect(screen.queryByRole("tablist", { name: "Settings" })).not.toBeInTheDocument());
  });
});
