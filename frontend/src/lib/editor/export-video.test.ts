import { expect, it, vi } from "vitest";
import { containRect, DEFAULT_VIDEO_FX } from "./video-effects";
import { exportVideo } from "./export-video";

const capture = vi.hoisted(() => ({ options: null as Record<string, unknown> | null }));
vi.mock("mediabunny", () => ({
  Input: class { dispose() {} }, Output: class { target = { buffer: new ArrayBuffer(2) }; },
  BlobSource: class {}, BufferTarget: class {}, Mp4OutputFormat: class {}, AudioSample: class {}, ALL_FORMATS: [],
  Conversion: { init: vi.fn(async (options) => {
    capture.options = options;
    return { isValid: true, discardedTracks: [], execute: vi.fn() };
  }) },
}));
vi.mock("@/lib/clip-actions", async (original) => ({ ...await original<typeof import("@/lib/clip-actions")>(), downloadBlob: vi.fn() }));

it("exports the rendered clip without adding captions and sizes landscape frames correctly", async () => {
  const ctx = { fillRect: vi.fn(), save: vi.fn(), restore: vi.fn(), translate: vi.fn(), scale: vi.fn(), fillText: vi.fn(), fillStyle: "", filter: "" };
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx as unknown as CanvasRenderingContext2D);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(new Blob(["video"]))));
  try {
    await exportVideo({ clip: { video_url: "/tasks/one/clips/two/file", filename: "saved.mp4" }, presetId: "reels", trimRange: [2, 8], videoFx: DEFAULT_VIDEO_FX, volume: 100, muted: false, onProgress: vi.fn() });
    const options = capture.options as unknown as { trim: { start: number; end: number }; video: { bitrate: number; process: (sample: unknown) => void } };
    expect(options.trim).toEqual({ start: 2, end: 8 });
    expect(options.video.bitrate).toBe(12_000_000);
    const draw = vi.fn();
    options.video.process({ displayWidth: 1920, displayHeight: 1080, draw });
    const rect = containRect(1920, 1080, 1080, 1920);
    expect(draw).toHaveBeenCalledWith(ctx, rect.x, rect.y, 1080, 607.5);
    expect(ctx.fillText).not.toHaveBeenCalled();
  } finally { vi.restoreAllMocks(); vi.unstubAllGlobals(); }
});
