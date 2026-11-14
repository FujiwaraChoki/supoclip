import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { DEFAULT_DOC_FX, type EditDocument } from "@/lib/editor/document";
import { draft } from "@/lib/editor/document.test-fixture";
import type { FxCue } from "@/lib/fx/core/types";
import { FxTrack, addUserSound, moveFxCue, nextUserCueId, removeFxCue } from "./fx-track";
import { MusicTrack, musicEnvelope } from "./music-track";

const base = { seed: 1, beat: "b1", origin: "auto" as const };
const cues: FxCue[] = [
  { ...base, id: "b1.zoom", kind: "zoom", start: 0.5, end: 1.2, mode: "punch", scale: 1.1, anchor: "face" },
  { ...base, id: "b1.sfx", kind: "sfx", start: 0.5, end: 0.5, sound: "whoosh", variant: 0, gain_db: -2 },
  { ...base, id: "b2.flash", kind: "flash", start: 1.6, end: 1.8, color: "#FFFFFF", peak: 0.6 },
  { ...base, id: "b2.flash~1", kind: "flash", start: 2.0, end: 2.1, color: "#FFFFFF", peak: 0.6 },
  { ...base, id: "u0", kind: "freeze", start: 2.2, end: 2.2, hold: 0.5, label: "", origin: "user" },
];
const withFx = (patch: Partial<NonNullable<EditDocument["fx"]>> = {}): EditDocument => ({
  ...structuredClone(draft),
  fx: { ...DEFAULT_DOC_FX, cues: structuredClone(cues), ...patch },
});

it("deleting an auto cue suppresses it (and its junction pieces); a user cue is just removed", () => {
  const doc = withFx();
  const auto = removeFxCue(doc, "b2.flash~1");
  expect(auto.fx!.cues.map((c) => c.id)).toEqual(["b1.zoom", "b1.sfx", "u0"]);
  expect(auto.fx!.suppressed).toEqual(["b2.flash"]);
  // Deleting it again changes nothing; suppressed has no duplicates.
  expect(removeFxCue(auto, "b2.flash")).toBe(auto);
  const user = removeFxCue(doc, "u0");
  expect(user.fx!.cues.map((c) => c.id)).not.toContain("u0");
  expect(user.fx!.suppressed).toEqual([]);
  // The input is untouched (undo keeps the old document).
  expect(doc.fx!.cues).toHaveLength(5);
});

it("moving a cue keeps its length, stays inside the source and makes it the user's", () => {
  const doc = withFx();
  const moved = moveFxCue(doc, "b1.zoom", 0.3, 3).fx!.cues.find((c) => c.id === "b1.zoom")!;
  expect(moved).toMatchObject({ start: 0.8, end: 1.5, origin: "user" });
  const clamped = moveFxCue(doc, "b1.zoom", 10, 3).fx!.cues.find((c) => c.id === "b1.zoom")!;
  expect(clamped).toMatchObject({ start: 2.3, end: 3 });
  expect(moveFxCue(doc, "missing", 1, 3)).toBe(doc);
});

it("adds a user sound with the next free id", () => {
  expect(nextUserCueId(cues)).toBe("u1");
  const doc = addUserSound(withFx(), 1.23456, "riser");
  const cue = doc.fx!.cues.find((c) => c.id === "u1")!;
  expect(cue).toMatchObject({ kind: "sfx", sound: "riser", start: 1.235, end: 1.235, origin: "user", gain_db: -4, beat: "" });
  // Without fx the default container is created.
  expect(addUserSound(structuredClone(draft), 0, "pop").fx!.cues).toHaveLength(1);
});

function renderTrack(doc: EditDocument, props: Partial<Parameters<typeof FxTrack>[0]> = {}) {
  const select = vi.fn(), open = vi.fn(), seekOut = vi.fn(), update = vi.fn();
  render(
    <FxTrack doc={doc} selected={null} select={select} open={open} seekOut={seekOut} update={update}
      at={() => 0} secondsPerPixel={() => 0.01} duration={3} playheadSource={1} pickerOpen={false}
      setPickerOpen={vi.fn()} {...props} />,
  );
  return { select, open, seekOut, update };
}

it("draws enabled cues on the lane: click selects and seeks, double-click opens", () => {
  const { select, open, seekOut } = renderTrack(withFx({ disabled: ["sfx"] }));
  const lane = screen.getByRole("group", { name: "Dynamic edits lane" });
  expect(lane).toHaveAttribute("data-tour", "studio-fx-lane");
  expect(lane.querySelectorAll("[data-fx-cue]")).toHaveLength(4);
  expect(lane.querySelector('[data-fx-kind="sfx"]')).toBeNull();
  const zoom = lane.querySelector<HTMLElement>('[data-fx-cue="b1.zoom"]')!;
  fireEvent.pointerDown(zoom, { button: 0, clientX: 10 });
  fireEvent.pointerUp(lane, { clientX: 10 });
  expect(select).toHaveBeenCalledWith("b1.zoom");
  expect(seekOut).toHaveBeenCalledWith(0.501);
  fireEvent.doubleClick(zoom);
  expect(open).toHaveBeenCalledWith("b1.zoom");
  expect(lane.querySelector('[data-fx-cue="u0"]')).toHaveTextContent("Hold 0.5s");
});

it("arrow keys nudge the focused cue by a frame instead of moving the playhead", () => {
  const doc = withFx();
  const { update } = renderTrack(doc);
  fireEvent.keyDown(screen.getByRole("group", { name: "Dynamic edits lane" }).querySelector('[data-fx-cue="b1.zoom"]')!, { key: "ArrowRight" });
  const change = update.mock.calls[0][0] as (d: EditDocument) => EditDocument;
  expect(change(doc).fx!.cues.find((c) => c.id === "b1.zoom")!.start).toBeCloseTo(0.5 + 1 / 30, 3);
});

it("the music lane draws the duck envelope", () => {
  const doc = withFx({
    music: { mood: "chill", bed: "chill_1", gain_db: -18, duck_db: -10, attack: 0.15, release: 0.4, offset: 0, fade_in: 0.5, fade_out: 0.5 },
  });
  const points = musicEnvelope(doc)!;
  // Ducked under the words, back up between them.
  expect(Math.min(...points.map(([, db]) => db))).toBeLessThanOrEqual(-28);
  render(<MusicTrack doc={doc} />);
  expect(screen.getByRole("group", { name: "Music bed and ducking" })).toHaveTextContent("Music dips 10 dB under speech");
  expect(musicEnvelope(withFx())).toBeNull();
});
