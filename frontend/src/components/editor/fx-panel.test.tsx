import { act, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useRef, useState } from "react";

import { ApiRequestError } from "@/lib/api-error";
import { DEFAULT_DOC_FX, FX_KINDS, type DocFx, type EditDocument, type EditorState } from "@/lib/editor/document";
import { draft } from "@/lib/editor/document.test-fixture";
import { DEFAULT_EDIT_STYLE, type Beat, type FxCue } from "@/lib/fx/core/types";
import { EDIT_STYLES_UNAVAILABLE, type EditStylesInfo } from "@/lib/fx/edit-style-request";
import { deleteCue, FxLookControls, FxPanel, holdBudget, keepLook, useFxEditor } from "./fx-panel";
import { editorRequest } from "./use-editor";

vi.mock("./use-editor", () => ({ editorRequest: vi.fn() }));
vi.mock("sonner", () => ({ toast: { info: vi.fn(), success: vi.fn(), error: vi.fn() } }));
const request = vi.mocked(editorRequest);

const info: EditStylesInfo = { ...EDIT_STYLES_UNAVAILABLE, enabled: true, reactions_available: false };
const asset = { hasLayout: false, faceTrack: null } as unknown as EditorState;

const beat = (id: string, start: number, kind: Beat["kind"] = "punchline"): Beat => ({
  id, kind, start, end: start + 0.5, text: `beat ${id}`, strength: 2, tone: "neutral", emoji: "", label: "", value: null,
  prefix: "", suffix: "", source: "llm", src_start: null, src_end: null, words: [],
});
const zoom = (id: string, origin: "auto" | "user" = "auto"): FxCue => ({
  id, kind: "zoom", start: 1, end: 1.4, seed: 1, beat: "", origin, mode: "punch", scale: 1.2, anchor: "center",
});
const withFx = (fx: Partial<DocFx>): EditDocument => ({ ...structuredClone(draft), fx: { ...DEFAULT_DOC_FX, ...fx } });

/** The panel wired like the inspector: one history the tests can read, `update` counted. */
function Harness({ initial, cueId, onUpdate }: { initial: EditDocument; cueId?: string; onUpdate: (doc: EditDocument) => void }) {
  // Like use-editor: the change runs synchronously against the latest draft.
  const [doc, setDoc] = useState(initial);
  const latest = useRef(initial);
  const update = (change: (d: EditDocument) => EditDocument) => {
    const next = change(latest.current);
    if (next === latest.current) return;
    latest.current = next;
    onUpdate(next);
    setDoc(next);
  };
  const editor = useFxEditor({ endpoint: "/editor", doc, update });
  return (
    <>
      <FxPanel doc={doc} asset={asset} update={update} seek={vi.fn()} editor={editor} info={info} cueId={cueId} />
      <FxLookControls doc={doc} update={update} editor={editor} />
      <output data-testid="doc">{JSON.stringify(doc)}</output>
    </>
  );
}
const currentDoc = () => JSON.parse(screen.getByTestId("doc").textContent!) as EditDocument;

function setup(initial: EditDocument, cueId?: string) {
  const updates: EditDocument[] = [];
  render(<Harness initial={initial} cueId={cueId} onUpdate={(d) => updates.push(d)} />);
  return updates;
}

beforeEach(() => {
  request.mockReset();
  // Radix sliders measure themselves.
  globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;
});
afterEach(() => vi.useRealTimers());

describe("intensity", () => {
  it("resolves on the server and applies the result as one change", async () => {
    const initial = withFx({ beats: [beat("b0", 1)], mood: "hype" });
    const resolved = withFx({ beats: [beat("b0", 1)], mood: "hype", style: { ...DEFAULT_EDIT_STYLE, intensity: "bold" }, cues: [zoom("e1")] });
    request.mockResolvedValueOnce({ document: resolved });
    const updates = setup(initial);
    await act(async () => fireEvent.click(screen.getByRole("radio", { name: "Bold" })));
    expect(request).toHaveBeenCalledTimes(1);
    const [url, method, body] = request.mock.calls[0];
    expect(url).toBe("/editor/fx/resolve");
    expect(method).toBe("POST");
    expect((body as { style: { intensity: string } }).style.intensity).toBe("bold");
    expect(updates).toHaveLength(1);
    expect(currentDoc().fx!.cues.map((c) => c.id)).toEqual(["e1"]);
    expect(screen.getByRole("radio", { name: "Bold" })).toHaveAttribute("aria-checked", "true");
  });

  it("offers planning on NEEDS_PLAN, then plans, copies the beats and resolves once", async () => {
    vi.useFakeTimers();
    request
      .mockRejectedValueOnce(new ApiRequestError({ message: "Plan first", traceId: null, code: "NEEDS_PLAN" }))
      .mockResolvedValueOnce({ job_id: "a".repeat(32), status: "queued" })
      .mockResolvedValueOnce({ status: "running" })
      .mockResolvedValueOnce({
        status: "done",
        beats: { version: "beats-v1", mood: "funny", sensitivity: "normal", timing: "word", beats: [beat("b0", 1)], reactions: [] },
      })
      .mockImplementationOnce(async (_url, _method, body) => {
        const sent = (body as { document: EditDocument }).document;
        return { document: { ...sent, fx: { ...sent.fx!, style: { ...DEFAULT_EDIT_STYLE, intensity: "max" }, cues: [zoom("e1")] } } };
      });
    const updates = setup(structuredClone(draft));
    await act(async () => fireEvent.click(screen.getByRole("radio", { name: "Max" })));
    expect(updates).toHaveLength(0);
    // The chosen level stays selected while it waits for a plan.
    expect(screen.getByRole("radio", { name: "Max" })).toHaveAttribute("aria-checked", "true");
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Plan edits" })));
    expect(request.mock.calls[1][0]).toBe("/editor/fx/plan");
    await act(async () => vi.advanceTimersByTimeAsync(1500));
    await act(async () => vi.advanceTimersByTimeAsync(1500));
    expect(request).toHaveBeenCalledTimes(5);
    const resolveBody = request.mock.calls[4][2] as { document: EditDocument; style: { intensity: string } };
    expect(resolveBody.style.intensity).toBe("max");
    expect(resolveBody.document.fx!.beats.map((b) => b.id)).toEqual(["b0"]);
    expect(resolveBody.document.fx!.mood).toBe("funny");
    expect(updates).toHaveLength(1);
    expect(currentDoc().fx!.cues).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Plan edits" })).toBeNull();
  });

  it("shows why planning failed", async () => {
    request
      .mockRejectedValueOnce(new ApiRequestError({ message: "Plan first", traceId: null, code: "NEEDS_PLAN" }))
      .mockRejectedValueOnce(new ApiRequestError({ message: "limit", traceId: null, code: "EDIT_PLAN_LIMIT_REACHED" }));
    setup(structuredClone(draft));
    await act(async () => fireEvent.click(screen.getByRole("radio", { name: "Subtle" })));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Plan edits" })));
    expect(screen.getByText(/planned edits as often as you can today/i)).toBeInTheDocument();
  });

  it("resolves again on the latest draft when it changed meanwhile", async () => {
    const initial = withFx({ beats: [beat("b0", 1)], mood: "hype" });
    let release!: (value: unknown) => void;
    request
      .mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }))
      .mockImplementationOnce(async (_url, _method, body) => ({ document: (body as { document: EditDocument }).document }));
    const updates = setup(initial);
    await act(async () => fireEvent.click(screen.getByRole("radio", { name: "Bold" })));
    // An edit lands while the server works.
    await act(async () => fireEvent.click(screen.getByRole("switch", { name: /Sound effects/ })));
    // That toggle is a newer request: the first one is aborted, so its late answer changes nothing.
    await act(async () => release({ document: { ...initial, volume: 0.1 } }));
    expect(updates.every((d) => d.volume === 1)).toBe(true);
  });
});

it("never overwrites edits made while the server resolved: it resolves again on the latest draft", async () => {
  const initial = withFx({ beats: [beat("b0", 1)], mood: "hype" });
  let current = initial;
  const update = vi.fn((change: (d: EditDocument) => EditDocument) => { current = change(current); });
  let release!: (value: unknown) => void;
  request
    .mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }))
    .mockImplementationOnce(async (_url, _method, body) => ({ document: (body as { document: EditDocument }).document }));
  const { result, rerender } = renderHook(({ doc }) => useFxEditor({ endpoint: "/editor", doc, update }), { initialProps: { doc: initial } });
  let done!: Promise<void>;
  act(() => { done = result.current.apply({ ...DEFAULT_EDIT_STYLE, intensity: "bold" }); });
  current = { ...current, volume: 0.5 };
  rerender({ doc: current });
  await act(async () => { release({ document: { ...initial, volume: 1 } }); await done; });
  expect(request).toHaveBeenCalledTimes(2);
  expect((request.mock.calls[1][2] as { document: EditDocument }).document.volume).toBe(0.5);
  expect(current.volume).toBe(0.5);
  expect(result.current.busy).toBeNull();
});

describe("removal and cues", () => {
  it("\"Remove all edits\" writes the inert DocFx", async () => {
    const updates = setup(withFx({ beats: [beat("b0", 1)], mood: "hype", cues: [zoom("e1")], style: { ...DEFAULT_EDIT_STYLE, intensity: "bold" } }));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Remove all edits" })));
    expect(updates).toHaveLength(1);
    const fx = currentDoc().fx!;
    expect(fx.style.intensity).toBe("off");
    expect(fx.cues).toEqual([]);
    expect(fx.beats).toEqual([]);
    expect([...fx.disabled].sort()).toEqual([...FX_KINDS].sort());
    expect(request).not.toHaveBeenCalled();
  });

  it("turning edits back on after removal re-enables every kind", async () => {
    request.mockImplementationOnce(async (_url, _method, body) => ({ document: (body as { document: EditDocument }).document }));
    setup(withFx({ beats: [beat("b0", 1)], mood: "hype", disabled: [...FX_KINDS] }));
    await act(async () => fireEvent.click(screen.getByRole("radio", { name: "Bold" })));
    expect((request.mock.calls[0][2] as { document: EditDocument }).document.fx!.disabled).toEqual([]);
  });

  it("deleting an AI cue suppresses it; a user cue just goes", () => {
    const doc = withFx({ cues: [zoom("e1"), zoom("u1", "user")] });
    const auto = deleteCue(doc, "e1").fx!;
    expect(auto.cues.map((c) => c.id)).toEqual(["u1"]);
    expect(auto.suppressed).toEqual(["e1"]);
    const user = deleteCue(doc, "u1").fx!;
    expect(user.suppressed).toEqual([]);
  });

  it("editing a selected AI cue makes it the user's", async () => {
    const updates = setup(withFx({ cues: [zoom("e1")], style: { ...DEFAULT_EDIT_STYLE, intensity: "bold" } }), "e1");
    expect(screen.getByRole("group", { name: "Selected edit" })).toHaveTextContent("Zoom");
    await act(async () => fireEvent.click(screen.getByRole("radio", { name: "Push in" })));
    expect(updates).toHaveLength(1);
    expect(currentDoc().fx!.cues[0]).toMatchObject({ id: "e1", mode: "push", origin: "user" });
  });

  it("deleting a beat re-resolves without it", async () => {
    request.mockImplementationOnce(async (_url, _method, body) => ({ document: (body as { document: EditDocument }).document }));
    setup(withFx({ beats: [beat("b0", 1), beat("b1", 2)], mood: "hype", style: { ...DEFAULT_EDIT_STYLE, intensity: "bold" } }));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Delete the beat at 0:01.00" })));
    expect((request.mock.calls[0][2] as { document: EditDocument }).document.fx!.beats.map((b) => b.id)).toEqual(["b1"]);
  });
});

describe("look", () => {
  it("keeps a hand-picked grade, grain and music levels through a re-resolve", () => {
    const before = withFx({
      style: { ...DEFAULT_EDIT_STYLE, intensity: "bold", grade: "noir", grain: true }, grade: "noir", grain: 0.7,
      music: { mood: "hype", bed: "hype", gain_db: -10, duck_db: -4, attack: 0.08, release: 0.35, offset: 0, fade_in: 0.5, fade_out: 0.8 },
    });
    const after = withFx({
      style: { ...DEFAULT_EDIT_STYLE, intensity: "bold", grade: "noir", grain: true, shake: false }, grade: "none", grain: 0.2,
      music: { mood: "hype", bed: "hype", gain_db: -20, duck_db: -12, attack: 0.08, release: 0.35, offset: 0, fade_in: 0.5, fade_out: 0.8 },
    });
    const kept = keepLook(before, after).fx!;
    expect(kept).toMatchObject({ grade: "noir", grain: 0.7 });
    expect(kept.music).toMatchObject({ gain_db: -10, duck_db: -4 });
  });

  it("a picked grade is stored as the style's grade too, so re-resolves keep it", async () => {
    HTMLElement.prototype.scrollIntoView = vi.fn();
    const updates = setup(structuredClone(draft));
    fireEvent.keyDown(screen.getByRole("combobox", { name: "Colour grade" }), { key: "Enter" });
    await act(async () => fireEvent.click(await screen.findByRole("option", { name: "Warm" })));
    expect(updates).toHaveLength(1);
    expect(currentDoc().fx).toMatchObject({ grade: "warm", style: { grade: "warm", intensity: "off" } });
    expect(request).not.toHaveBeenCalled();
  });

  it("caps freezes at 4 % of the edit", () => {
    expect(holdBudget(draft)).toBeCloseTo(0.12);
  });
});
