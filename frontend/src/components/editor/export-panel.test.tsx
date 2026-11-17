import { act, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { DEFAULT_DOC_FX, type EditDocument, type EditorState } from "@/lib/editor/document";
import { draft } from "@/lib/editor/document.test-fixture";
import type { FxCue } from "@/lib/fx/core/types";
import { ExportPanel, hasEditAudio, type ClipSummary } from "./export-panel";
import { editorRequest } from "./use-editor";

vi.mock("@/lib/auth-client", () => ({ useSession: () => ({ data: null }) }));
vi.mock("@/lib/clip-export-tracking", () => ({ trackClipExport: vi.fn() }));
vi.mock("@/lib/editor/export-draft", () => ({ exportDraft: vi.fn() }));
vi.mock("./use-editor", () => ({ editorRequest: vi.fn() }));
vi.mock("mediabunny", () => ({ canEncodeVideo: async () => true, canEncodeAudio: async () => true }));
const request = vi.mocked(editorRequest);

const asset = { duration: 3, width: 1920, height: 1080, hasAudio: true } as unknown as EditorState;
const clip: ClipSummary = { id: "c1", filename: "c1.mp4", clip_order: 1, duration: 3, text: "", video_url: "" };
const readyAsset = {
  status: "ready", basis: "c1.mp4", duration: 3, width: 1080, height: 1920, fps: 30, hasAudio: true,
  waveform: [], draft: { revision: 0, document: draft }, original: draft, jobs: [],
} satisfies EditorState;
const base = { id: "", start: 1, end: 1, seed: 0, beat: "", origin: "auto" as const };
const freeze: FxCue = { ...base, id: "f1", kind: "freeze", hold: 0.5, label: "" };
const behind: FxCue = { ...base, id: "a1", end: 1.5, kind: "accent", variant: "behind", text: "big", emoji: "", doodle: "sparkles", value: null, prefix: "", suffix: "" };
const sfx: FxCue = { ...base, id: "s1", kind: "sfx", sound: "whoosh", variant: 0, gain_db: 0 };
const withCues = (cues: FxCue[]): EditDocument => ({ ...draft, fx: { ...DEFAULT_DOC_FX, cues } });

async function open(doc: EditDocument) {
  render(<ExportPanel open setOpen={vi.fn()} taskId="t" clip={clip} clips={[clip]} doc={doc} asset={asset} save={vi.fn(async () => {})} />);
  await act(async () => {});
}

beforeEach(() => {
  vi.clearAllMocks();
  request.mockReset();
  request.mockImplementation(async () => ({ jobs: [] }) as never);
});

it("offers on-device export for a draft the browser can render", async () => {
  await open(draft);
  expect(screen.getByRole("button", { name: /On this device/ })).toBeEnabled();
});

it.each([
  ["speed ramps and freezes", [freeze], /Speed ramps and freeze frames render on the server/],
  ["text behind the speaker", [behind], /Text behind the speaker renders on the server/],
])("explains why %s can't export on this device", async (_name, cues, reason) => {
  await open(withCues(cues));
  const onDevice = screen.getByRole("button", { name: /On this device/ });
  expect(onDevice).toBeDisabled();
  expect(onDevice).toHaveTextContent(reason);
  // Background export stays the choice.
  expect(screen.getByRole("button", { name: /Background export/ })).toHaveAttribute("aria-pressed", "true");
});

it("shows the finished clip's length, freezes included", async () => {
  await open(withCues([freeze]));
  expect(screen.getByText("0:03.50")).toBeInTheDocument();
});

it("mentions the sound the edits add", async () => {
  expect(hasEditAudio(draft)).toBe(false);
  expect(hasEditAudio(withCues([sfx]))).toBe(true);
  await open(withCues([sfx]));
  expect(screen.getByText("100% volume · Sound effects and music")).toBeInTheDocument();
});

function panel(open: boolean) {
  return (
    <ExportPanel open={open} setOpen={() => {}} taskId="task" clip={clip} clips={[clip]}
      doc={draft} asset={readyAsset} save={async () => {}} />
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
    answerPoll({ ...readyAsset, jobs: [{ id: "late", status: "rendering", progress: 10 }] });
  });
  await act(async () => {});
  expect(request).toHaveBeenCalledTimes(sent);
  // ...and every request the panel made was aborted, so no response reaches state.
  expect(signals.length).toBe(sent);
  expect(signals.every((signal) => signal?.aborted)).toBe(true);
});
