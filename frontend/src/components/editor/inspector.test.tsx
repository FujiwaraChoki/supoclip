import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { DEFAULT_DOC_FX, type EditDocument, type EditorState } from "@/lib/editor/document";
import { draft } from "@/lib/editor/document.test-fixture";
import { EDIT_STYLES_UNAVAILABLE, type EditStylesInfo } from "@/lib/fx/edit-style-request";
import { Inspector, type InspectorTab } from "./inspector";
import { editorRequest } from "./use-editor";

let editStyles: EditStylesInfo | null = null;
vi.mock("@/components/edit-intensity", () => ({ useEditStylesInfo: () => editStyles }));
vi.mock("./use-editor", () => ({ editorRequest: vi.fn() }));
vi.mock("@/components/caption-style-tiles", () => ({
  CaptionStyleTiles: ({ options, onChange }: { options: { id: string }[]; onChange: (id: string) => void }) => (
    <div>{options.map((o) => <button key={o.id} aria-label={`tile ${o.id}`} onClick={() => onChange(o.id)} />)}</div>
  ),
}));
const request = vi.mocked(editorRequest);

const asset = { duration: 3, width: 1080, height: 1920, hasAudio: true, captionLooks: undefined } as unknown as EditorState;
const enabled: EditStylesInfo = { ...EDIT_STYLES_UNAVAILABLE, enabled: true, templates: ["slam"] };

function setup({ doc = draft, tab = "captions", fxCueId }: { doc?: EditDocument; tab?: InspectorTab; fxCueId?: string | null } = {}) {
  const onTabChange = vi.fn();
  // Like use-editor, the change runs at once against the current draft.
  const update = vi.fn((change: (d: EditDocument) => EditDocument) => change(doc));
  const props = {
    doc, asset, onTabChange, time: 0, editTime: 0, seek: vi.fn(), update, applyAll: vi.fn(async () => {}),
    endpoint: "/editor", brollId: null, selectBroll: vi.fn(),
  };
  const view = render(<Inspector {...props} tab={tab} fxCueId={fxCueId} />);
  return { ...view, onTabChange, update, rerender: (next: Partial<typeof props> & { tab?: InspectorTab; fxCueId?: string | null }) => view.rerender(<Inspector {...props} tab={tab} fxCueId={fxCueId} {...next} />) };
}

beforeEach(() => {
  editStyles = null;
  request.mockReset();
  globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;
});

it("hides the Edits tab from users who can't use dynamic edits on a draft without them", () => {
  editStyles = EDIT_STYLES_UNAVAILABLE;
  setup();
  expect(screen.getAllByRole("tab")).toHaveLength(5);
  expect(screen.queryByRole("tab", { name: "Edits" })).toBeNull();
  expect(screen.queryByText("Animated")).toBeNull();
});

it("shows the Edits tab, with its tour target, when the flag is on or the draft already has edits", () => {
  editStyles = enabled;
  const { unmount } = setup();
  const tab = screen.getByRole("tab", { name: "Edits" });
  expect(tab).toHaveAttribute("data-tour", "studio-edits-tab");
  unmount();
  editStyles = EDIT_STYLES_UNAVAILABLE;
  setup({ doc: { ...draft, fx: DEFAULT_DOC_FX } });
  expect(screen.getByRole("tab", { name: "Edits" })).toBeInTheDocument();
});

it("is controlled: tabs report through onTabChange and follow the tab prop", () => {
  editStyles = enabled;
  const view = setup();
  fireEvent.mouseDown(screen.getByRole("tab", { name: "Edits" }), { button: 0 });
  expect(view.onTabChange).toHaveBeenCalledWith("edits");
  view.rerender({ tab: "edits" });
  expect(screen.getByRole("group", { name: "Edit intensity" })).toBeInTheDocument();
  view.rerender({ tab: "effects" });
  expect(screen.getByRole("combobox", { name: "Colour grade" })).toBeInTheDocument();
  expect(screen.getByRole("slider", { name: "Film grain" })).toBeInTheDocument();
});

it("opens the Edits tab when a cue is selected on the timeline", () => {
  editStyles = enabled;
  const view = setup({ doc: { ...draft, fx: DEFAULT_DOC_FX } });
  expect(view.onTabChange).not.toHaveBeenCalled();
  view.rerender({ fxCueId: "e1" });
  expect(view.onTabChange).toHaveBeenCalledWith("edits");
});

it("resolves when an animated caption template is picked; classic picks are plain edits", async () => {
  editStyles = enabled;
  request.mockImplementation(async (_url, _method, body) => ({ document: (body as { document: EditDocument }).document }));
  const view = setup();
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "tile slam" })));
  expect(request).toHaveBeenCalledTimes(1);
  expect(request.mock.calls[0][0]).toBe("/editor/fx/resolve");
  expect((request.mock.calls[0][2] as { document: EditDocument }).document.captions.template).toBe("slam");
  expect(view.update).toHaveBeenCalledTimes(1);
  await act(async () => fireEvent.click(screen.getByRole("radio", { name: "Neon" })));
  expect(request).toHaveBeenCalledTimes(1);
  const change = view.update.mock.calls[1][0] as (doc: EditDocument) => EditDocument;
  expect(change(draft).captions.template).toBe("neon");
});
