import { act, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { DEFAULT_DOC_FX, type EditDocument, type EditorState } from "@/lib/editor/document";
import { draft } from "@/lib/editor/document.test-fixture";
import type { FxCue } from "@/lib/fx/core/types";
import { ExportPanel, hasEditAudio, type ClipSummary } from "./export-panel";

vi.mock("@/lib/auth-client", () => ({ useSession: () => ({ data: null }) }));
vi.mock("@/lib/clip-export-tracking", () => ({ trackClipExport: vi.fn() }));
vi.mock("@/lib/editor/export-draft", () => ({ exportDraft: vi.fn() }));
vi.mock("./use-editor", () => ({ editorRequest: vi.fn(async () => ({ jobs: [] })) }));
vi.mock("mediabunny", () => ({ canEncodeVideo: async () => true, canEncodeAudio: async () => true }));

const asset = { duration: 3, width: 1920, height: 1080, hasAudio: true } as unknown as EditorState;
const clip: ClipSummary = { id: "c1", filename: "c1.mp4", clip_order: 1, duration: 3, text: "", video_url: "" };
const base = { id: "", start: 1, end: 1, seed: 0, beat: "", origin: "auto" as const };
const freeze: FxCue = { ...base, id: "f1", kind: "freeze", hold: 0.5, label: "" };
const behind: FxCue = { ...base, id: "a1", end: 1.5, kind: "accent", variant: "behind", text: "big", emoji: "", doodle: "sparkles", value: null, prefix: "", suffix: "" };
const sfx: FxCue = { ...base, id: "s1", kind: "sfx", sound: "whoosh", variant: 0, gain_db: 0 };
const withCues = (cues: FxCue[]): EditDocument => ({ ...draft, fx: { ...DEFAULT_DOC_FX, cues } });

async function open(doc: EditDocument) {
  render(<ExportPanel open setOpen={vi.fn()} taskId="t" clip={clip} clips={[clip]} doc={doc} asset={asset} save={vi.fn(async () => {})} />);
  await act(async () => {});
}

beforeEach(() => vi.clearAllMocks());

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
