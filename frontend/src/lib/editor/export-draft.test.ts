import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EditorState } from "./document";
import { draft } from "./document.test-fixture";

const sources = vi.hoisted(() => ({ buffer: [] as unknown[], blob: [] as unknown[] }));

vi.mock("mediabunny", () => {
  class Input {
    constructor(public options: { source: unknown }) {}
    async getPrimaryVideoTrack() {
      return null;
    }
    async getPrimaryAudioTrack() {
      return null;
    }
    dispose() {}
  }
  return {
    ALL_FORMATS: [],
    Input,
    Output: class {
      async cancel() {}
    },
    Mp4OutputFormat: class {},
    BufferTarget: class {},
    VideoSampleSink: class {},
    BufferSource: class {
      constructor(buffer: unknown) {
        sources.buffer.push(buffer);
      }
    },
    BlobSource: class {
      constructor(blob: unknown) {
        sources.blob.push(blob);
      }
    },
    canEncodeAudio: async () => true,
    canEncodeVideo: async () => true,
  };
});

vi.mock("./render", () => ({
  drawEditorFrame: vi.fn(),
  loadEditorFont: vi.fn(async () => {}),
}));

vi.mock("./document", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./document")>();
  return {
    ...actual,
    mappedBroll: vi.fn(() => [{ clip: { footage: { id: "stock" } } }]),
    footageKey: vi.fn(() => "stock"),
    footageUrl: vi.fn((endpoint: string) => `${endpoint}/broll/stock.mp4`),
  };
});

import { exportDraft, onDeviceMemoryBudget, readMedia, TOO_LARGE_ON_DEVICE } from "./export-draft";

function mediaResponse(init: ResponseInit = { headers: { "content-length": "4" } }) {
  const response = new Response(new Uint8Array([0, 0, 0, 24]), init);
  vi.spyOn(response, "blob");
  vi.spyOn(response, "arrayBuffer");
  return response;
}

describe("exportDraft", () => {
  const responses: Response[] = [];

  beforeEach(() => {
    sources.buffer.length = 0;
    sources.blob.length = 0;
    responses.length = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        const response = mediaResponse();
        responses.push(response);
        return response;
      }),
    );
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(
      {} as CanvasRenderingContext2D,
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  // Blob reads fail on iOS WebKit, and mediabunny's parallel read-ahead then
  // leaves unhandled rejections behind. The clip and its B-roll are read from memory instead.
  it("reads the clip and its B-roll from an ArrayBuffer, never a Blob", async () => {
    const doc = draft;
    const asset = { width: 1080, height: 1920, hasAudio: false } as EditorState;
    await expect(
      exportDraft(
        "/api/tasks/t/clips/c/editor/media/clean.mp4",
        doc,
        asset,
        "tiktok",
        new AbortController().signal,
        () => {},
      ),
    ).rejects.toThrow("Use background export.");

    expect(responses).toHaveLength(2);
    for (const response of responses) {
      expect(response.blob).not.toHaveBeenCalled();
      expect(response.arrayBuffer).toHaveBeenCalledOnce();
    }
    expect(sources.blob).toHaveLength(0);
    expect(sources.buffer).toHaveLength(2);
    for (const buffer of sources.buffer) expect(buffer).toBeInstanceOf(ArrayBuffer);
  });

  const run = (doc = draft) =>
    exportDraft(
      "/api/tasks/t/clips/c/editor/media/clean.mp4",
      doc,
      { width: 1080, height: 1920, hasAudio: false } as EditorState,
      "tiktok",
      new AbortController().signal,
      () => {},
    );

  it("refuses a clip larger than the device budget before downloading it", async () => {
    const huge = mediaResponse({ headers: { "content-length": String(8 * 1024 ** 3) } });
    vi.stubGlobal("fetch", vi.fn(async () => huge));
    await expect(run()).rejects.toThrow(TOO_LARGE_ON_DEVICE);
    expect(huge.arrayBuffer).not.toHaveBeenCalled();
    expect(sources.buffer).toHaveLength(0);
  });

  it("counts every file against the same budget", async () => {
    const budget = { left: 10 };
    const bytes = (n: number) =>
      new Response(new Uint8Array(n), { headers: { "content-length": String(n) } });
    expect((await readMedia(bytes(6), budget)).byteLength).toBe(6);
    expect(budget.left).toBe(4);
    const second = bytes(6);
    const read = vi.spyOn(second, "arrayBuffer");
    await expect(readMedia(second, budget)).rejects.toThrow(TOO_LARGE_ON_DEVICE);
    expect(read).not.toHaveBeenCalled();
    expect(budget.left).toBe(4);
  });

  it("stops reading a body without a length once it passes the budget", async () => {
    let pulled = 0;
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({
      pull(controller) {
        pulled++;
        controller.enqueue(new Uint8Array(4));
      },
      cancel,
    });
    await expect(readMedia(new Response(body), { left: 10 })).rejects.toThrow(TOO_LARGE_ON_DEVICE);
    expect(cancel).toHaveBeenCalled();
    expect(pulled).toBeLessThanOrEqual(4);
  });

  it("reads a body without a length into one buffer", async () => {
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array([1, 2]));
        controller.enqueue(new Uint8Array([3]));
        controller.close();
      },
    });
    const budget = { left: 10 };
    expect(new Uint8Array(await readMedia(new Response(body), budget))).toEqual(new Uint8Array([1, 2, 3]));
    expect(budget.left).toBe(7);
  });

  it("sends a long edit on a phone to background export before any download", async () => {
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue(
      "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
    );
    expect(onDeviceMemoryBudget()).toBe(512 * 1024 ** 2);
    const long = { ...draft, segments: [{ ...draft.segments[0], start: 0, end: 600 }] };
    await expect(run(long)).rejects.toThrow(TOO_LARGE_ON_DEVICE);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("gives an iPad in desktop mode the phone budget", () => {
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue(
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
    );
    vi.stubGlobal("navigator", { userAgent: navigator.userAgent, maxTouchPoints: 5 });
    expect(onDeviceMemoryBudget()).toBe(512 * 1024 ** 2);
  });
});
