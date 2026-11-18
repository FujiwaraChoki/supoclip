import { describe, expect, it, vi } from "vitest";

import { ensureFullQuality, ensureTaskFullQuality, hasPreviewClips, isPreviewClip } from "./full-quality";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const base = {
  readError: async () => "server error",
  failedMessage: "failed",
  timeoutMessage: "timed out",
  sleep: async () => {},
};

describe("ensureFullQuality", () => {
  it("starts the render, reports progress and resolves once the clip is full quality", async () => {
    const request = vi.fn()
      .mockResolvedValueOnce(json({ render_quality: "preview", full_render: { status: "queued", progress: 0, error: null } }))
      .mockResolvedValueOnce(json({ render_quality: "preview", full_render: { status: "running", progress: 45, error: null } }))
      .mockResolvedValueOnce(json({ render_quality: "full", full_render: { status: "ready", progress: 100, error: null } }));
    const progress: number[] = [];

    await ensureFullQuality({
      ...base, request, taskApiUrl: "/api/tasks", taskId: "t 1", clipId: "c1", trigger: "download",
      onProgress: (value) => progress.push(value),
    });

    expect(request).toHaveBeenNthCalledWith(1, "/api/tasks/t%201/clips/c1/full-render", expect.objectContaining({
      method: "POST", body: JSON.stringify({ trigger: "download" }),
    }));
    expect(request).toHaveBeenNthCalledWith(2, "/api/tasks/t%201/clips/c1/full-render", { cache: "no-store" });
    expect(progress).toEqual([0, 45, 100]);
  });

  it("resolves at once for a clip that is already full quality", async () => {
    const request = vi.fn().mockResolvedValue(json({ render_quality: "full", full_render: { status: "ready", progress: 100, error: null } }));
    await ensureFullQuality({ ...base, request, taskApiUrl: "/api/tasks", taskId: "t", clipId: "c", trigger: "export" });
    expect(request).toHaveBeenCalledTimes(1);
  });

  it("throws the render's error, the server's error, or a timeout", async () => {
    const failed = vi.fn().mockResolvedValue(json({ render_quality: "preview", full_render: { status: "failed", progress: 0, error: "Regenerate the clip." } }));
    await expect(ensureFullQuality({ ...base, request: failed, taskApiUrl: "/a", taskId: "t", clipId: "c", trigger: "publish" }))
      .rejects.toThrow("Regenerate the clip.");

    const unavailable = vi.fn().mockResolvedValue(json({ detail: "down" }, 503));
    await expect(ensureFullQuality({ ...base, request: unavailable, taskApiUrl: "/a", taskId: "t", clipId: "c", trigger: "download" }))
      .rejects.toThrow("server error");

    let clock = 0;
    const slow = vi.fn().mockImplementation(async () => json({ render_quality: "preview", full_render: { status: "running", progress: 10, error: null } }));
    await expect(ensureFullQuality({
      ...base, request: slow, taskApiUrl: "/a", taskId: "t", clipId: "c", trigger: "download",
      now: () => clock, sleep: async () => { clock += 60_000; }, timeoutMs: 120_000,
    })).rejects.toThrow("timed out");
  });
});

describe("ensureTaskFullQuality", () => {
  it("waits until every clip is ready", async () => {
    const request = vi.fn()
      .mockResolvedValueOnce(json({ status: "preparing", progress: 30, clips: [] }))
      .mockResolvedValueOnce(json({ status: "ready", progress: 100, clips: [] }));
    const progress: number[] = [];
    await ensureTaskFullQuality({ ...base, request, taskApiUrl: "/api/tasks", taskId: "t", onProgress: (value) => progress.push(value) });
    expect(request).toHaveBeenNthCalledWith(1, "/api/tasks/t/full-render", expect.objectContaining({ method: "POST" }));
    expect(progress).toEqual([30, 100]);
  });

  it("stops on the first failed clip", async () => {
    const request = vi.fn().mockResolvedValue(json({
      status: "failed", progress: 50,
      clips: [{ full_render: { status: "ready" } }, { full_render: { status: "failed", error: "No inputs" } }],
    }));
    await expect(ensureTaskFullQuality({ ...base, request, taskApiUrl: "/api/tasks", taskId: "t" })).rejects.toThrow("No inputs");
  });
});

it("tells previews apart", () => {
  expect(isPreviewClip({ render_quality: "preview" })).toBe(true);
  expect(isPreviewClip({ render_quality: "full" })).toBe(false);
  expect(isPreviewClip({})).toBe(false);
  expect(hasPreviewClips([{}, { render_quality: "preview" }])).toBe(true);
});
