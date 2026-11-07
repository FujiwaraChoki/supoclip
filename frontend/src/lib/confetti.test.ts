import { afterEach, describe, expect, it, vi } from "vitest";

import { celebrate } from "./confetti";

afterEach(() => { vi.restoreAllMocks(); document.body.innerHTML = ""; });

describe("celebrate", () => {
  it("draws over the page without catching clicks, then cleans up", () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ scale() {}, clearRect() {}, save() {}, restore() {}, translate() {}, rotate() {}, fillRect() {}, beginPath() {}, arc() {}, fill() {} } as never);
    const frames: FrameRequestCallback[] = [];
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { frames.push(callback); return frames.length; });
    celebrate({ x: 10, y: 10 });
    const canvas = document.querySelector("canvas")!;
    expect(canvas.style.pointerEvents).toBe("none");
    expect(canvas).toHaveAttribute("aria-hidden", "true");
    frames.shift()!(performance.now() + 10_000);
    expect(document.querySelector("canvas")).toBeNull();
    vi.unstubAllGlobals();
  });

  it("does nothing for people who asked for less motion", () => {
    vi.stubGlobal("matchMedia", () => ({ matches: true }));
    celebrate();
    expect(document.querySelector("canvas")).toBeNull();
    vi.unstubAllGlobals();
  });
});
