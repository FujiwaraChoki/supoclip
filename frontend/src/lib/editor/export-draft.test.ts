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

import { exportDraft } from "./export-draft";

function mediaResponse() {
  const response = new Response(new Uint8Array([0, 0, 0, 24]));
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
});
