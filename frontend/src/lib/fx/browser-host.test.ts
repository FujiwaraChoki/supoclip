import { describe, expect, it, vi } from "vitest";
import { createBrowserHost, detectFilterSupport } from "./browser-host";
import type { DrawOp } from "./core/host";
import { recordingContext } from "./core/test-utils/fake-host";

const pictureOp = (over: Partial<Extract<DrawOp, { op: "picture" }>> = {}): Extract<DrawOp, { op: "picture" }> => ({
  op: "picture", transform: [2, 0, 0, 2, 5, 6], filter: "saturate(0)", alpha: 1, blend: "source-over", channel: "rgb",
  dx: 0, dy: 0, cutout: false, clip: null, ...over,
});

describe("detectFilterSupport", () => {
  it("needs a filter property that keeps the assigned value", () => {
    expect(detectFilterSupport(null)).toBe(false);
    expect(detectFilterSupport({})).toBe(false);   // Safari < 18: no ctx.filter
    expect(detectFilterSupport({ filter: "none" })).toBe(true);
    const ignored = { get filter() { return "none"; }, set filter(_v: string) { /* unsupported value dropped */ } };
    expect(detectFilterSupport(ignored)).toBe(false);
    const ctx = { filter: "none" };
    detectFilterSupport(ctx);
    expect(ctx.filter).toBe("none");
  });
});

describe("browser host", () => {
  it("draws the base picture under the op transform and filter", () => {
    const drawBase = vi.fn(), drawCutout = vi.fn();
    const host = createBrowserHost({ drawBase, drawCutout, supportsFilter: true });
    const { ctx, calls } = recordingContext();
    host.drawPicture(ctx, pictureOp());
    expect(calls).toContainEqual(["setTransform", 2, 0, 0, 2, 5, 6]);
    expect(calls).toContainEqual(["=filter", "saturate(0)"]);
    expect(drawBase).toHaveBeenCalledOnce();
    expect(drawCutout).not.toHaveBeenCalled();
    expect(calls[0][0]).toBe("save");
    expect(calls.at(-1)![0]).toBe("restore");
  });

  it("routes cutout draws to the matte drawer and skips the filter when unsupported", () => {
    const drawBase = vi.fn(), drawCutout = vi.fn();
    const host = createBrowserHost({ drawBase, drawCutout, supportsFilter: false });
    const { ctx, calls } = recordingContext();
    host.drawPicture(ctx, pictureOp({ cutout: true }));
    expect(drawCutout).toHaveBeenCalledOnce();
    expect(drawCutout.mock.calls[0][0]).toBe(ctx);
    expect(drawCutout.mock.calls[0][1].cutout).toBe(true);
    expect(drawBase).not.toHaveBeenCalled();
    expect(calls.some((c) => c[0] === "=filter")).toBe(false);
    expect(host.supportsFilter).toBe(false);
  });

  it("names bundled fonts FX-<stem> and exposes the optional atlas and frame0", () => {
    const host = createBrowserHost({ drawBase: () => {}, supportsFilter: true });
    expect(host.fontFamily("THEBOLDFONT")).toBe("FX-THEBOLDFONT");
    expect(host.atlas()).toBeNull();
    expect(host.frame0()).toBeNull();
    expect(host.measure("abcd", "FX-X", 10)).toBeGreaterThan(0);
  });
});
