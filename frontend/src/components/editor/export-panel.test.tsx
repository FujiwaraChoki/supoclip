import { act, render } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { ExportPanel, type ClipSummary } from "./export-panel";
import { editorRequest } from "./use-editor";
import { draft } from "@/lib/editor/document.test-fixture";
import type { EditorState } from "@/lib/editor/document";

vi.mock("./use-editor", () => ({ editorRequest: vi.fn() }));
vi.mock("@/lib/auth-client", () => ({ useSession: () => ({ data: null }) }));
vi.mock("@/lib/editor/export-draft", () => ({ exportDraft: vi.fn() }));
vi.mock("mediabunny", () => ({
  canEncodeVideo: async () => true,
  canEncodeAudio: async () => true,
}));
const request = vi.mocked(editorRequest);

const clip: ClipSummary = { id: "clip-1", filename: "clip.mp4", clip_order: 1, duration: 3, text: "", video_url: "" };
const asset = {
  status: "ready", basis: "clip.mp4", duration: 3, width: 1080, height: 1920, fps: 30, hasAudio: true,
  waveform: [], draft: { revision: 0, document: draft }, original: draft, jobs: [],
} satisfies EditorState;

beforeEach(() => {
  request.mockReset();
});

function panel(open: boolean) {
  return (
    <ExportPanel open={open} setOpen={() => {}} taskId="task" clip={clip} clips={[clip]}
      doc={draft} asset={asset} save={async () => {}} />
  );
}

it("drops queued job refreshes and aborts the one in flight when the panel goes away", async () => {
  // The first poll hangs until the test answers it; it ignores aborts, like a
  // response that is already on its way.
  let answerPoll!: (state: EditorState) => void;
  const signals: (AbortSignal | undefined)[] = [];
  request.mockImplementationOnce(async (_url, _method, _body, signal) => {
    signals.push(signal);
    return new Promise<EditorState>((resolve) => (answerPoll = resolve)) as never;
  });
  request.mockImplementation(async (_url, _method, _body, signal) => {
    signals.push(signal);
    return new Promise(() => {}) as never;
  });

  const view = render(panel(true));
  await act(async () => {});
  expect(request).toHaveBeenCalledTimes(1);

  // Close and reopen while that poll is still pending, then leave the studio.
  view.rerender(panel(false));
  view.rerender(panel(true));
  await act(async () => {});
  const sent = request.mock.calls.length;
  view.unmount();

  // The late answer starts no queued refresh...
  await act(async () => {
    answerPoll({ ...asset, jobs: [{ id: "late", status: "rendering", progress: 10 }] });
  });
  await act(async () => {});
  expect(request).toHaveBeenCalledTimes(sent);
  // ...and every request the panel made was aborted, so no response reaches state.
  expect(signals.length).toBe(sent);
  expect(signals.every((signal) => signal?.aborted)).toBe(true);
});
