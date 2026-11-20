import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Preview } from "./preview";
import { TooltipProvider } from "@/components/ui/tooltip";
import { draft } from "@/lib/editor/document.test-fixture";
import type { EditorState } from "@/lib/editor/document";

vi.mock("@/lib/editor/render", () => ({
  drawEditorFrame: vi.fn(),
  loadEditorFont: vi.fn(() => Promise.resolve()),
}));

const asset = {
  status: "ready",
  basis: "clip.mp4",
  duration: 3,
  width: 1080,
  height: 1920,
  fps: 30,
  hasAudio: true,
  waveform: [],
  draft: { revision: 1, document: draft },
  original: draft,
  jobs: [],
} satisfies EditorState;

let resume: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  resume = vi.fn(() => Promise.resolve());
  vi.stubGlobal(
    "AudioContext",
    class {
      destination = {};
      resume = resume;
      close = vi.fn(() => Promise.resolve());
      createGain() {
        return { gain: { value: 1 }, connect: vi.fn() };
      }
      createMediaElementSource() {
        return { connect: vi.fn() };
      }
    },
  );
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function setup() {
  const videoRef = { current: null as HTMLVideoElement | null };
  render(
    <Preview
      doc={draft}
      asset={asset}
      endpoint="/editor"
      segmentIndex={0}
      select={vi.fn()}
      time={0}
      onTime={vi.fn()}
      seekRequest={{ time: 0, nonce: 0 }}
      tab="captions"
      update={vi.fn()}
      videoRef={videoRef}
    />,
    { wrapper: TooltipProvider },
  );
  const play = vi
    .spyOn(HTMLMediaElement.prototype, "play")
    .mockImplementation(() => Promise.resolve());
  return { play };
}

it("starts the video when the audio device fails to resume", async () => {
  resume.mockImplementation(() =>
    Promise.reject(new DOMException("Failed to start the audio device", "InvalidStateError")),
  );
  const { play } = setup();
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Play" }));
  });
  expect(resume).toHaveBeenCalledOnce();
  expect(play).toHaveBeenCalledOnce();
});

it("resumes the audio before starting the video", async () => {
  const { play } = setup();
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Play" }));
  });
  expect(resume).toHaveBeenCalledOnce();
  expect(play).toHaveBeenCalledOnce();
  expect(resume.mock.invocationCallOrder[0]).toBeLessThan(play.mock.invocationCallOrder[0]);
});
