import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { DYNAMIC_EDITS_INTERVIEW } from "@/lib/dynamic-edits-demo";
import { DynamicEditsSection } from "./dynamic-edits-section";

/** Media state jsdom doesn't implement: playback, position and duration. */
const media = new WeakMap<HTMLMediaElement, { time: number; paused: boolean }>();
const stateOf = (el: HTMLMediaElement) => {
  let state = media.get(el);
  if (!state) {
    state = { time: 0, paused: true };
    media.set(el, state);
  }
  return state;
};

class VisibleObserver {
  constructor(private callback: IntersectionObserverCallback) {}
  observe(target: Element) {
    this.callback([{ isIntersecting: true, target } as IntersectionObserverEntry], this as unknown as IntersectionObserver);
  }
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return [];
  }
}

beforeEach(() => {
  vi.stubGlobal("IntersectionObserver", VisibleObserver);
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: false,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
  const proto = HTMLMediaElement.prototype;
  vi.spyOn(proto, "play").mockImplementation(function (this: HTMLMediaElement) {
    stateOf(this).paused = false;
    this.dispatchEvent(new Event("play"));
    return Promise.resolve();
  });
  vi.spyOn(proto, "pause").mockImplementation(function (this: HTMLMediaElement) {
    stateOf(this).paused = true;
    this.dispatchEvent(new Event("pause"));
  });
  vi.spyOn(proto, "paused", "get").mockImplementation(function (this: HTMLMediaElement) {
    return stateOf(this).paused;
  });
  vi.spyOn(proto, "readyState", "get").mockReturnValue(4);
  vi.spyOn(proto, "duration", "get").mockReturnValue(30);
  vi.spyOn(proto, "currentTime", "get").mockImplementation(function (this: HTMLMediaElement) {
    return stateOf(this).time;
  });
  vi.spyOn(proto, "currentTime", "set").mockImplementation(function (this: HTMLMediaElement, value: number) {
    stateOf(this).time = value;
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const video = (container: HTMLElement, intensity: string) =>
  container.querySelector<HTMLVideoElement>(`video[data-clip="${intensity}"]`)!;

it("labels every intensity and starts on Max", () => {
  render(<DynamicEditsSection />);
  const group = screen.getByRole("group", { name: "Edit intensity" });
  for (const label of ["Off", "Subtle", "Bold", "Max"]) {
    expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
  }
  expect(group).toContainElement(screen.getByRole("button", { name: "Max" }));
  expect(screen.getByRole("button", { name: "Max" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByText(/MrBeast-style heavy captions/)).toBeInTheDocument();
});

it("keeps the playback position when switching intensity", async () => {
  const user = userEvent.setup();
  const { container } = render(<DynamicEditsSection />);

  const max = video(container, "max");
  expect(max.getAttribute("src")).toBe(DYNAMIC_EDITS_INTERVIEW.max.src);
  // Autoplays muted once the section is near the viewport.
  expect(max.paused).toBe(false);
  expect(max.muted).toBe(true);

  max.currentTime = 7.25;
  await user.click(screen.getByRole("button", { name: "Bold" }));

  const bold = video(container, "bold");
  expect(bold.getAttribute("src")).toBe(DYNAMIC_EDITS_INTERVIEW.bold.src);
  expect(bold.currentTime).toBe(7.25);
  expect(bold.paused).toBe(false);
  expect(max.paused).toBe(true);
  expect(bold).not.toHaveAttribute("aria-hidden", "true");
  expect(max).toHaveAttribute("aria-hidden", "true");
  expect(screen.getByRole("button", { name: "Bold" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByText(/Hormozi-style punch-ins/)).toBeInTheDocument();
});

it("keeps the position when switching while paused", async () => {
  const user = userEvent.setup();
  const { container } = render(<DynamicEditsSection />);

  await user.click(screen.getByRole("button", { name: "Pause: Clip at Max" }));
  const max = video(container, "max");
  expect(max.paused).toBe(true);
  max.currentTime = 2.5;

  await user.click(screen.getByRole("button", { name: "Subtle" }));
  const subtle = video(container, "subtle");
  expect(subtle.currentTime).toBe(2.5);
  expect(subtle.paused).toBe(true);
  expect(screen.getByRole("button", { name: "Play: Clip at Subtle" })).toBeInTheDocument();
});

it("lets one clip have sound at a time", async () => {
  const user = userEvent.setup();
  const { container } = render(<DynamicEditsSection />);
  const [interviewSound, brollSound] = screen.getAllByRole("button", { name: "Turn sound on" });

  await user.click(interviewSound);
  expect(video(container, "max").muted).toBe(false);

  await user.click(brollSound);
  await act(async () => {});
  expect(video(container, "max").muted).toBe(true);
  const broll = container.querySelectorAll<HTMLVideoElement>("video")[4];
  expect(broll.muted).toBe(false);
});
