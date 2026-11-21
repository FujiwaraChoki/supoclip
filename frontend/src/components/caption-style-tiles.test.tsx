import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { recordingContext } from "@/lib/fx/core/test-utils/fake-host";
import { DEMO_STATIC_T } from "@/lib/fx/demo-scene";
import { CaptionStyleTiles, type CaptionStyleOption } from "./caption-style-tiles";

const rendered = vi.hoisted(() => ({ calls: [] as { template: string; e: number }[] }));

vi.mock("@/lib/fx/core/render", () => ({
  renderFrame: vi.fn((_ctx: unknown, prepared: { scene: { template: string } }, clock: { e: number }) => {
    rendered.calls.push({ template: prepared.scene.template, e: clock.e });
  }),
}));
// No network or FontFace in jsdom: assets simply never arrive.
vi.mock("@/lib/fx/assets-client", () => ({ loadFxAtlas: () => new Promise(() => {}) }));
vi.mock("@/lib/fx/browser-host", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/fx/browser-host")>()),
  loadFxFont: () => new Promise(() => {}),
}));

const OPTIONS: CaptionStyleOption[] = [{ id: "slam" }, { id: "behind_head" }, { id: "stat_counter", name: "Server name" }];

function stubMotion(reduce: boolean) {
  vi.stubGlobal("matchMedia", vi.fn((query: string) => ({
    matches: reduce && query.includes("reduce"), media: query, addEventListener: vi.fn(), removeEventListener: vi.fn(),
  })));
}

beforeEach(() => {
  rendered.calls = [];
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(function (this: HTMLCanvasElement) {
    return recordingContext(this.width, this.height).ctx as never;
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("CaptionStyleTiles", () => {
  it("lists classic styles in the same group, drawn from their own font and colours", () => {
    stubMotion(true);
    const onChange = vi.fn();
    const classic = { font: "THEBOLDFONT", size: 32, color: "#FFFFFF", highlight: "#00FF66" };
    render(<CaptionStyleTiles options={[{ id: "hormozi", name: "Hormozi", description: "Green pill", classic }, { id: "slam" }]} value="hormozi" onChange={onChange} />);
    const group = screen.getByRole("radiogroup", { name: "Caption style" });
    expect(group.querySelectorAll('[role="radio"]')).toHaveLength(2);
    const tile = screen.getByRole("radio", { name: "Hormozi" });
    expect(tile).toHaveAttribute("aria-checked", "true");
    expect(tile).toHaveAccessibleDescription("Green pill");
    // Its sample is the demo line in the style's case, not an fx-core scene.
    expect(tile).toHaveTextContent("OURREVENUEGREW");
    expect(rendered.calls.every((call) => call.template === "slam")).toBe(true);
    fireEvent.keyDown(tile, { key: "ArrowRight" });
    expect(onChange).toHaveBeenLastCalledWith("slam");
  });

  it("is a radio group of named tiles the tour can point at", () => {
    stubMotion(true);
    const onChange = vi.fn();
    render(<CaptionStyleTiles options={OPTIONS} value="behind_head" onChange={onChange} />);
    const group = screen.getByRole("radiogroup", { name: "Caption style" });
    expect(group).toHaveAttribute("data-tour", "caption-styles");
    expect(screen.getByRole("radio", { name: "Behind Head" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: "Slam" })).toHaveAttribute("aria-checked", "false");
    // Our own words win over the server's English name.
    expect(screen.getByRole("radio", { name: "Stat Counter" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Behind Head" })).toHaveAccessibleDescription("One giant word appears behind the speaker");
    expect(screen.getAllByText("Best with a face on camera")).toHaveLength(1);

    fireEvent.click(screen.getByRole("radio", { name: "Slam" }));
    expect(onChange).toHaveBeenLastCalledWith("slam");
    fireEvent.keyDown(screen.getByRole("radio", { name: "Behind Head" }), { key: "ArrowRight" });
    expect(onChange).toHaveBeenLastCalledWith("stat_counter");
    fireEvent.keyDown(screen.getByRole("radio", { name: "Slam" }), { key: "ArrowLeft" });
    expect(onChange).toHaveBeenLastCalledWith("stat_counter");
  });

  it("draws one still frame per tile with reduced motion", async () => {
    stubMotion(true);
    const raf = vi.spyOn(window, "requestAnimationFrame");
    render(<CaptionStyleTiles options={OPTIONS} value="slam" onChange={() => {}} />);
    // The animated tiles (and the renderer) load on demand.
    await waitFor(() => expect(document.querySelectorAll("canvas[data-motion]")).toHaveLength(3));
    for (const canvas of document.querySelectorAll("canvas")) expect(canvas).toHaveAttribute("data-motion", "static");
    expect(raf).not.toHaveBeenCalled();
    expect(new Set(rendered.calls.map((call) => call.template))).toEqual(new Set(["slam", "behind_head", "stat_counter"]));
    expect(rendered.calls.every((call) => call.e === DEMO_STATIC_T)).toBe(true);
  });

  it("animates on one shared clock otherwise", async () => {
    stubMotion(false);
    const raf = vi.spyOn(window, "requestAnimationFrame").mockImplementation(() => 1);
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});
    const { unmount } = render(<CaptionStyleTiles options={OPTIONS} value="slam" onChange={() => {}} />);
    // The animated tiles (and the renderer) load on demand.
    await waitFor(() => expect(document.querySelectorAll("canvas[data-motion]")).toHaveLength(3));
    for (const canvas of document.querySelectorAll("canvas")) expect(canvas).toHaveAttribute("data-motion", "animated");
    // One frame request drives every tile.
    expect(raf).toHaveBeenCalledTimes(1);
    const tick = raf.mock.calls[0][0];
    rendered.calls = [];
    act(() => tick(performance.now() + 1000));
    expect(rendered.calls.map((call) => call.template).sort()).toEqual(["behind_head", "slam", "stat_counter"]);
    unmount();
    expect(window.cancelAnimationFrame).toHaveBeenCalled();
  });

  it("renders nothing without options", () => {
    stubMotion(true);
    const { container } = render(<CaptionStyleTiles options={[]} value="default" onChange={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });
});
